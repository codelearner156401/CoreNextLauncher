"""Non-modal in-window loading animation (blue line C)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import anim, theme


class LoadingOverlay(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setVisible(False)
        self._angle = 0.0
        self._phase = 0.0
        self._text = "正在加载…"
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def show_loading(self, text: str = "正在加载…") -> None:
        self._text = text
        self.setVisible(True)
        self.raise_()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def _tick(self) -> None:
        speed = anim.speed()
        self._angle = (self._angle + 3.2 * speed) % 360.0
        self._phase += 0.11 * speed
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        palette = theme.colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 55))
        size = 40.0
        bounce = -abs(math.sin(self._phase)) * 8.0
        center = QPointF(self.width() / 2.0, self.height() / 2.0 - 12 + bounce)
        painter.save()
        painter.translate(center)
        painter.rotate(self._angle)
        pen = QPen(QColor("#4f8cff"))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(
            QRectF(-size / 2, -size / 2, size, size),
            int(42 * 16),
            int(276 * 16),
        )
        painter.restore()
        painter.setPen(QColor(palette["text"]))
        painter.drawText(
            QRectF(0, self.height() / 2 + 42, self.width(), 24),
            Qt.AlignmentFlag.AlignCenter,
            self._text,
        )
        painter.end()
