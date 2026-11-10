"""Background artwork and frosted-glass (acrylic) surfaces.

The launcher paints a single background layer in :class:`BackgroundWidget` and
lets :class:`GlassFrame` copy the blurred region underneath each panel.  That is
the same idea as CSS ``backdrop-filter: blur(...)``: panels stay translucent and
the artwork behind them is genuinely blurred, not just faded.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QImageReader,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QWidget,
)

from .. import theme

DEFAULT_BLUR = 26
DEFAULT_DIM = 42
MAX_BLUR = 48
MAX_DIM = 80


def _clamp_int(value: object, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _blur_image(image: QImage, radius: float) -> QImage:
    """Return a Gaussian-blurred copy of *image*.

    The source is padded with stretched edge pixels first so the blur does not
    fade into transparency along the window borders.  QGraphicsBlurEffect runs
    in C++, which keeps a full-window blur around 20 ms on a typical desktop.
    """
    if image.isNull():
        return image
    if radius < 0.5:
        return image.copy()

    pad = max(2, int(radius * 2))
    width, height = image.width(), image.height()
    padded = QImage(
        width + pad * 2,
        height + pad * 2,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    padded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(padded)
    painter.drawImage(pad, pad, image)
    # Continue the edge colours outwards; the corners are filled by the strips.
    painter.drawImage(QRect(0, pad, pad, height), image, QRect(0, 0, 1, height))
    painter.drawImage(
        QRect(pad + width, pad, pad, height), image, QRect(width - 1, 0, 1, height)
    )
    painter.drawImage(QRect(pad, 0, width, pad), image, QRect(0, 0, width, 1))
    painter.drawImage(
        QRect(pad, pad + height, width, pad), image, QRect(0, height - 1, width, 1)
    )
    painter.end()

    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(padded))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(float(radius))
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)

    out = QImage(padded.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    renderer = QPainter(out)
    renderer.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    scene.render(
        renderer,
        QRectF(0, 0, padded.width(), padded.height()),
        QRectF(0, 0, padded.width(), padded.height()),
    )
    renderer.end()
    return out.copy(pad, pad, width, height)


class BackgroundWidget(QWidget):
    """Paints the launcher backdrop and owns the cached blurred copy."""

    changed = Signal()
    image_error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

        self._theme = theme.current_theme()
        self._image_path = ""
        self._image = QImage()
        self._image_rev = 0
        self._glass = True
        self._blur_radius = DEFAULT_BLUR
        self._dim = DEFAULT_DIM
        self._system_blur = False

        self._base = QPixmap()
        self._blurred = QPixmap()
        self._dpr = 1.0
        self._build_key: tuple | None = None

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(70)
        self._rebuild_timer.timeout.connect(self._rebuild)

    # ------------------------------------------------------------- settings
    @property
    def image_path(self) -> str:
        return self._image_path

    @property
    def glass_enabled(self) -> bool:
        return bool(self._glass and not self._blurred.isNull())

    @property
    def device_pixel_ratio(self) -> float:
        return self._dpr

    def apply_settings(
        self,
        image_path: str = "",
        glass: bool = True,
        blur: int = DEFAULT_BLUR,
        dim: int = DEFAULT_DIM,
        theme_key: str | None = None,
        system_blur: bool = False,
    ) -> None:
        path = str(image_path or "").strip()
        if path != self._image_path:
            self._load_image(path)

        self._glass = bool(glass)
        self._system_blur = bool(system_blur)
        self._blur_radius = _clamp_int(blur, 0, MAX_BLUR, DEFAULT_BLUR)
        self._dim = _clamp_int(dim, 0, MAX_DIM, DEFAULT_DIM)
        if theme_key and theme_key != self._theme:
            self._theme = theme_key
        self._invalidate()
        self.update()

    def set_theme(self, theme_key: str) -> None:
        if theme_key == self._theme:
            return
        self._theme = theme_key
        self._invalidate()
        # Rebuild immediately so panels do not flash a stale (light) backdrop
        # while the QSS flips to dark, or vice versa.
        if self.isVisible() and self.width() > 2 and self.height() > 2:
            self._rebuild()
        self.update()

    def _invalidate(self) -> None:
        self._build_key = None
        self._rebuild_timer.start()

    def _load_image(self, path: str) -> None:
        self._image_path = path
        self._image = QImage()
        self._image_rev += 1
        if not path:
            return
        candidate = Path(path)
        if not candidate.is_file():
            self.image_error.emit(f"背景图片不存在，已回退到内置背景：{path}")
            return

        reader = QImageReader(str(candidate))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and (size.width() > 3840 or size.height() > 2160):
            reader.setScaledSize(
                size.scaled(QSize(3840, 2160), Qt.AspectRatioMode.KeepAspectRatio)
            )
        image = reader.read()
        if image.isNull():
            self.image_error.emit(
                f"无法读取背景图片（{reader.errorString()}）：{path}"
            )
            return
        self._image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    # -------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._base.isNull() and self.width() > 2 and self.height() > 2:
            self._rebuild()
        if not self._base.isNull():
            painter.drawPixmap(self.rect(), self._base, self._base.rect())
        else:
            painter.fillRect(self.rect(), QColor(theme.colors(self._theme)["bg"]))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._invalidate()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.width() > 2 and self.height() > 2:
            self._rebuild()

    def event(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self._invalidate()
            self._rebuild()
        return super().event(event)

    def _compose_base(self, size: QSize) -> QImage:
        image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        # With system acrylic enabled and no custom artwork, the window itself
        # must stay translucent so the desktop can show through.
        transparent = bool(self._system_blur and self._image.isNull())
        if transparent:
            # A very light tint keeps text readable while the DWM acrylic
            # layer still lets desktop light through.
            tint = QColor(theme.colors(self._theme)["bg"])
            tint.setAlpha(10)
            image.fill(tint)
        else:
            image.fill(QColor(theme.colors(self._theme)["bg"]))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if transparent:
            painter.end()
            return image
        if not self._image.isNull():
            self._paint_cover(painter, size)
        else:
            self._paint_ambient(painter, size)

        self._paint_overlay(painter, size)
        painter.end()
        return image

    def _paint_cover(self, painter: QPainter, size: QSize) -> None:
        image = self._image
        iw, ih = image.width(), image.height()
        if iw <= 0 or ih <= 0:
            return
        scale = max(size.width() / iw, size.height() / ih)
        crop_w = min(iw, size.width() / scale)
        crop_h = min(ih, size.height() / scale)
        source = QRectF(
            (iw - crop_w) / 2.0,
            (ih - crop_h) / 2.0,
            crop_w,
            crop_h,
        )
        painter.drawImage(QRectF(0, 0, size.width(), size.height()), image, source)

    def _paint_ambient(self, painter: QPainter, size: QSize) -> None:
        width, height = size.width(), size.height()
        dark = self._theme != "light"
        if dark:
            base = QLinearGradient(0, 0, width, height)
            base.setColorAt(0.0, QColor("#05070d"))
            base.setColorAt(0.45, QColor("#0a0f19"))
            base.setColorAt(1.0, QColor("#070a12"))
            painter.fillRect(0, 0, width, height, QBrush(base))
            blobs = (
                (0.12, 0.06, 0.58, theme.current_primary(), 105),
                (0.92, 0.16, 0.52, theme.current_accent(), 95),
                (0.68, 0.98, 0.55, "#0fb9a5", 62),
                (0.02, 0.86, 0.42, "#e04f9b", 44),
            )
        else:
            base = QLinearGradient(0, 0, width, height)
            base.setColorAt(0.0, QColor("#f8faff"))
            base.setColorAt(0.5, QColor("#eef2fc"))
            base.setColorAt(1.0, QColor("#e7ecfa"))
            painter.fillRect(0, 0, width, height, QBrush(base))
            blobs = (
                (0.10, 0.05, 0.60, theme.current_primary(), 110),
                (0.95, 0.12, 0.52, theme.current_accent(), 95),
                (0.70, 0.98, 0.55, "#8de7dc", 80),
                (0.00, 0.88, 0.42, "#ffb7d5", 70),
            )

        painter.setPen(Qt.PenStyle.NoPen)
        for fx, fy, fr, colour, alpha in blobs:
            gradient = QRadialGradient(
                width * fx, height * fy, max(width, height) * fr
            )
            core = QColor(colour)
            core.setAlpha(alpha)
            middle = QColor(colour)
            middle.setAlpha(int(alpha * 0.35))
            clear = QColor(colour)
            clear.setAlpha(0)
            gradient.setColorAt(0.0, core)
            gradient.setColorAt(0.55, middle)
            gradient.setColorAt(1.0, clear)
            painter.fillRect(0, 0, width, height, QBrush(gradient))

        # A whisper of grain keeps the blurred panels from looking like flat
        # gradients when the built-in backdrop is used.
        grain = QColor(255, 255, 255, 9 if dark else 20)
        for index in range(1900):
            x = (index * 1103515245 + 12345) % width
            y = (index * 214013 + 2531011) % height
            painter.fillRect(x, y, 1, 1, grain)

    def _paint_overlay(self, painter: QPainter, size: QSize) -> None:
        width, height = size.width(), size.height()
        dark = self._theme != "light"
        if dark:
            dim_alpha = int(min(210, self._dim * 1.55))
        else:
            dim_alpha = int(min(150, self._dim * 1.15))
        if dim_alpha > 0:
            overlay = QColor(0, 0, 0) if dark else QColor(255, 255, 255)
            overlay.setAlpha(dim_alpha)
            painter.fillRect(0, 0, width, height, overlay)

        # A soft scrim over the header and tab strip keeps text readable even
        # when the chosen photo is very bright.
        scrim_height = max(1, int(height * 0.30))
        scrim = QLinearGradient(0, 0, 0, scrim_height)
        if dark:
            scrim.setColorAt(0.0, QColor(4, 7, 12, 165))
            scrim.setColorAt(0.55, QColor(4, 7, 12, 82))
            scrim.setColorAt(1.0, QColor(4, 7, 12, 0))
        else:
            scrim.setColorAt(0.0, QColor(248, 250, 255, 195))
            scrim.setColorAt(0.55, QColor(248, 250, 255, 128))
            scrim.setColorAt(1.0, QColor(248, 250, 255, 0))
        painter.fillRect(0, 0, width, scrim_height, QBrush(scrim))

        vignette = QRadialGradient(
            width * 0.5, height * 0.42, max(width, height) * 0.78
        )
        edge = QColor(0, 0, 0, 95) if dark else QColor(255, 255, 255, 110)
        vignette.setColorAt(0.0, QColor(edge.red(), edge.green(), edge.blue(), 0))
        vignette.setColorAt(1.0, edge)
        painter.fillRect(0, 0, width, height, QBrush(vignette))

    # --------------------------------------------------------------- rebuild
    def _rebuild(self) -> None:
        width, height = self.width(), self.height()
        if width <= 2 or height <= 2:
            return
        dpr = float(self.devicePixelRatioF() or 1.0)
        raw_size = QSize(max(1, round(width * dpr)), max(1, round(height * dpr)))
        key = (
            raw_size.width(),
            raw_size.height(),
            round(dpr, 3),
            self._theme,
            self._image_rev,
            self._blur_radius,
            self._dim,
        )
        if key == self._build_key and not self._base.isNull():
            return

        base_image = self._compose_base(raw_size)
        self._base = QPixmap.fromImage(base_image)
        self._blurred = QPixmap.fromImage(
            _blur_image(base_image, float(self._blur_radius) * dpr)
        )
        self._dpr = dpr
        self._build_key = key
        self.changed.emit()
        self.update()

    # --------------------------------------------------------------- helpers
    @property
    def system_blur(self) -> bool:
        return bool(self._system_blur)

    def panel_alpha(self) -> int:
        alpha = 180 if self._theme != "light" else 208
        if self._system_blur:
            alpha -= 72 if self._theme != "light" else 52
        return max(80, alpha)

    def paint_backdrop(self, painter: QPainter, widget: QWidget) -> bool:
        """Draw the cached backdrop region currently under *widget*."""
        pixmap = self._blurred if self._glass else self._base
        if pixmap.isNull() or widget.width() <= 0 or widget.height() <= 0:
            return False
        origin = widget.mapTo(self, QPoint(0, 0))
        dpr = self._dpr or 1.0
        source_left = origin.x() * dpr
        source_top = origin.y() * dpr
        source_right = source_left + widget.width() * dpr
        source_bottom = source_top + widget.height() * dpr
        left = max(0.0, source_left)
        top = max(0.0, source_top)
        right = min(float(pixmap.width()), source_right)
        bottom = min(float(pixmap.height()), source_bottom)
        if right <= left or bottom <= top:
            return False
        target = QRectF(
            (left - source_left) / dpr,
            (top - source_top) / dpr,
            (right - left) / dpr,
            (bottom - top) / dpr,
        )
        source = QRectF(left, top, right - left, bottom - top)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(target, pixmap, source)
        return True


class GlassFrame(QFrame):
    """A rounded panel that blurs whatever is behind it.

    When no backdrop is available (for example inside a standalone dialog),
    the same painter falls back to the solid card colour, so every dialog keeps
    working without special-casing.
    """

    def __init__(
        self,
        parent=None,
        radius: float = 14.0,
        tint_alpha: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._radius = float(radius)
        self._tint_alpha = tint_alpha
        self._backdrop: BackgroundWidget | None = None
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._bind_backdrop()

    def _bind_backdrop(self) -> None:
        provider = find_backdrop(self)
        if provider is self._backdrop:
            return
        if self._backdrop is not None:
            try:
                self._backdrop.changed.disconnect(self.update)
            except (RuntimeError, TypeError):
                pass
        self._backdrop = provider
        if provider is not None:
            provider.changed.connect(self.update)

    def paintEvent(self, event) -> None:  # noqa: N802
        self._paint_surface()

    def _paint_surface(self, hover: float = 0.0) -> None:
        colours = theme.colors()
        provider = self._backdrop or find_backdrop(self)
        if provider is not None and provider is not self._backdrop:
            self._bind_backdrop()
            provider = self._backdrop

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if rect.width() <= 1 or rect.height() <= 1:
            return
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setPen(Qt.PenStyle.NoPen)

        style = theme.current_style()
        glass = style != "html" and provider is not None and provider.glass_enabled
        painted_backdrop = False
        if glass:
            painter.save()
            painter.setClipPath(path)
            painted_backdrop = provider.paint_backdrop(painter, self)
            if painted_backdrop:
                tint = QColor(colours["card"])
                alpha = self._tint_alpha or provider.panel_alpha()
                if style == "frosted":
                    alpha = min(255, alpha + (20 if provider.system_blur else 45))
                tint.setAlpha(alpha)
                painter.fillRect(self.rect(), tint)

                if style == "liquid":
                    sheen = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.center().y()))
                    sheen.setColorAt(
                        0.0, QColor(255, 255, 255, 34 if provider._theme != "light" else 92)
                    )
                    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
                    painter.fillPath(path, QBrush(sheen))
            painter.restore()

        if not painted_backdrop:
            painter.fillPath(path, QColor(colours["card"]))

        border = QColor(colours["border_strong"] if glass else colours["border"])
        if glass:
            border.setAlpha(170 if provider._theme != "light" else 210)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)

        if glass and painted_backdrop and style == "liquid":
            highlight = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            highlight.setColorAt(
                0.0, QColor(255, 255, 255, 72 if provider._theme != "light" else 190)
            )
            highlight.setColorAt(0.22, QColor(255, 255, 255, 0))
            painter.setPen(QPen(QBrush(highlight), 1.0))
            painter.drawPath(path)

        if hover > 0.001:
            accent = QColor(colours["accent"])
            accent.setAlpha(int(150 * max(0.0, min(1.0, hover))))
            painter.setPen(QPen(accent, 1.3))
            painter.drawPath(path)
        painter.end()


def find_backdrop(widget: QWidget) -> BackgroundWidget | None:
    """Return the main window backdrop for *widget*, if it belongs to one."""
    window = widget.window()
    backdrop = getattr(window, "backdrop", None)
    if isinstance(backdrop, BackgroundWidget) and window is not widget:
        if window.isAncestorOf(widget):
            return backdrop
    return None
