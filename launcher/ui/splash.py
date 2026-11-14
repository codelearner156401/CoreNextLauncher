"""Startup splash: the shared C-ring animation plus a soft fade in and out.

The splash owns no animation driver of its own - LineCLoading runs the single
rotation/bounce timeline, so the old 16 ms QTimer that recomputed the angle is
gone.  finish() fades the window out before closing it, which removes the hard
cut that used to flash the splash on top of the freshly built main window.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget

from .. import APP_NAME, anim, theme
from .loading_widget import LineCLoading

FADE_IN_MS = 220
FADE_OUT_MS = 260
DOTS_INTERVAL_MS = 420


class LoadingSplash(QWidget):
    """Small frameless splash shown while the main window is constructed."""

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(460, 280)
        self._style = str(getattr(config, "ui_style", "liquid") or "liquid")
        profiles = getattr(config, "theme_profiles", {}) or {}
        self._profile = profiles.get(self._style, {}) if isinstance(profiles, dict) else {}
        self._base = str(self._profile.get("base") or theme.STYLE_BASE.get(self._style, "dark"))
        self._primary = str(self._profile.get("primary") or "#4f8cff")
        self._accent = str(self._profile.get("accent") or "#7b5cff")
        splash = str(self._profile.get("splash") or "")
        self._image = QPixmap(splash) if splash and Path(splash).is_file() else QPixmap()

        self.line = LineCLoading(self)
        self.line.setFixedSize(120, 150)
        self.line.move((self.width() - 120) // 2, 18)
        self.line.setVisible(self._image.isNull() and anim.loading_enabled())

        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.rect().center())

        self._dots = 1
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(DOTS_INTERVAL_MS)
        self._dots_timer.timeout.connect(self._tick_dots)

        self._closing = False
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_finished)

    # --------------------------------------------------------------- ticking
    def _tick_dots(self) -> None:
        self._dots = self._dots % 3 + 1
        self.update()

    # ---------------------------------------------------------------- fades
    def fade_in(self) -> None:
        self._fade.stop()
        self._effect.setOpacity(0.0)
        self._fade.setDuration(anim.ms(FADE_IN_MS))
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        if not self._dots_timer.isActive():
            self._dots_timer.start()

    def finish(self, window: QWidget | None = None) -> None:
        """Fade the splash out, then close it.  Safe to call more than once."""
        if self._closing:
            return
        self._closing = True
        self._dots_timer.stop()
        if window is not None:
            window.raise_()
            window.activateWindow()
        if not self.isVisible():
            self._shutdown()
            return
        self._fade.stop()
        self._fade.setDuration(anim.ms(FADE_OUT_MS))
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._closing:
            self._shutdown()

    def _shutdown(self) -> None:
        self._fade.stop()
        self._dots_timer.stop()
        self.line.stop()
        self.setVisible(False)
        self.close()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._closing:
            self.fade_in()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        self._fade.stop()
        self._dots_timer.stop()
        self.line.stop()
        super().closeEvent(event)

    # -------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 18, 18)
        painter.setClipPath(path)

        if not self._image.isNull():
            image = self._image.toImage()
            # The painter works in logical pixels, so the copy has to be made at
            # device resolution and mapped back onto the logical target rect;
            # scaling to self.size() would leave the widget's 2x device pixels
            # to be upscaled by Qt on a HiDPI screen.
            dpr = self.devicePixelRatioF() or 1.0
            device = QSize(
                max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr))
            )
            scaled = image.scaled(
                device,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = max(0, (scaled.width() - device.width()) // 2)
            y = max(0, (scaled.height() - device.height()) // 2)
            crop = scaled.copy(x, y, device.width(), device.height())
            crop.setDevicePixelRatio(1.0)
            # The target rect stays logical, so a 1x screen blits the copy 1:1 and
            # a scaled screen maps the device-resolution copy onto the same area.
            painter.drawImage(QRectF(0, 0, self.width(), self.height()), crop)
        else:
            colours = theme.colors(self._base)
            background = QLinearGradient(0, 0, self.width(), self.height())
            if self._base == "light":
                background.setColorAt(0.0, QColor("#f8faff"))
                background.setColorAt(1.0, QColor("#e6edfb"))
            else:
                background.setColorAt(0.0, QColor("#070b12"))
                background.setColorAt(1.0, QColor("#111827"))
            painter.fillRect(self.rect(), background)

            painter.setPen(QColor(colours["text"]))
            title = QFont("Segoe UI")
            title.setPixelSize(20)
            title.setWeight(QFont.Weight.DemiBold)
            painter.setFont(title)
            painter.drawText(
                QRectF(0, 166, self.width(), 30),
                Qt.AlignmentFlag.AlignCenter,
                APP_NAME,
            )
            painter.setPen(QColor(colours["muted"]))
            sub = QFont("Segoe UI")
            sub.setPixelSize(11)
            painter.setFont(sub)
            painter.drawText(
                QRectF(0, 196, self.width(), 24),
                Qt.AlignmentFlag.AlignCenter,
                "正在准备启动器…",
            )
            painter.drawText(
                QRectF(0, 224, self.width(), 22),
                Qt.AlignmentFlag.AlignCenter,
                "." * self._dots,
            )

        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QColor(255, 255, 255, 38 if self._base == "dark" else 160))
        painter.drawPath(path)
        painter.end()


def create_splash(config) -> LoadingSplash | None:
    profiles = getattr(config, "theme_profiles", {}) or {}
    style = str(getattr(config, "ui_style", "liquid") or "liquid")
    profile = profiles.get(style, {}) if isinstance(profiles, dict) else {}
    splash = str(profile.get("splash") or "")
    if splash and not Path(splash).is_file():
        splash = ""
    if not anim.loading_enabled() and not splash:
        return None
    return LoadingSplash(config)
