"""Startup splash / loading animation (rotating bouncing C or custom image)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .. import APP_NAME, anim, theme


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
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.rect().center())
        self._angle = 0.0
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        if self._image.isNull():
            self._timer.start()

    def _tick(self) -> None:
        speed = anim.speed()
        self._angle = (self._angle + 3.2 * speed) % 360.0
        self._phase += 0.11 * speed
        self.update()

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
            scaled = image.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = max(0, (scaled.width() - self.width()) // 2)
            y = max(0, (scaled.height() - self.height()) // 2)
            painter.drawImage(0, 0, scaled.copy(x, y, self.width(), self.height()))
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

            # Small, single, very thin blue line C (no fill, no thick glow).
            import math

            size = 46.0
            bounce = -abs(math.sin(self._phase)) * 9.0
            center = QPointF(self.width() / 2.0, 102 + bounce)
            painter.save()
            painter.translate(center)
            painter.rotate(self._angle)
            pen = QPen(QColor("#4f8cff"))
            pen.setWidthF(2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(
                QRectF(-size / 2, -size / 2, size, size),
                int(42 * 16),
                int(276 * 16),
            )
            painter.restore()

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
            dots = "." * (1 + int(self._phase * 1.5) % 3)
            painter.drawText(
                QRectF(0, 224, self.width(), 22),
                Qt.AlignmentFlag.AlignCenter,
                dots,
            )

        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QColor(255, 255, 255, 38 if self._base == "dark" else 160))
        painter.drawPath(path)
        painter.end()

    def finish(self, window: QWidget) -> None:
        self._timer.stop()
        QTimer.singleShot(850, self.close)


def create_splash(config) -> LoadingSplash | None:
    profiles = getattr(config, "theme_profiles", {}) or {}
    style = str(getattr(config, "ui_style", "liquid") or "liquid")
    profile = profiles.get(style, {}) if isinstance(profiles, dict) else {}
    splash = str(profile.get("splash") or "")
    if not getattr(config, "loading_animation", True) and not splash:
        return None
    if splash and not Path(splash).is_file():
        splash = ""
    if not getattr(config, "loading_animation", True) and not splash:
        return None
    return LoadingSplash(config)
