"""Non-modal in-window loading overlay using the fixed thin-line C widget.

The overlay fades in when work starts and fades out when it stops, and every
show/stop pair is driven by a single animation whose finished signal is wired
once, so overlapping show/stop calls can never leave a pending hide behind.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

from .. import anim
from .loading_widget import LineCLoading

FADE_IN_MS = 180
FADE_OUT_MS = 200


class LoadingOverlay(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setVisible(False)
        self._text = "正在加载…"
        self.line = LineCLoading(self)
        self.label = QLabel(self._text, self)
        self.label.setObjectName("InstallStatus")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._fade.finished.connect(self._on_fade_finished)
        self._hiding = False

    # ---------------------------------------------------------------- public
    def is_loading(self) -> bool:
        return self.isVisible() and not self._hiding

    def show_loading(self, text: str = "正在加载…") -> None:
        if not anim.loading_enabled():
            self._hide_now()
            return
        self._text = text
        self.label.setText(text)
        self._layout_children()
        self._hiding = False
        self.setVisible(True)
        self.raise_()
        self._fade.stop()
        self._effect.setOpacity(0.0)
        self._fade.setDuration(anim.ms(FADE_IN_MS))
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def stop(self) -> None:
        """Idempotent: repeated calls never queue a second fade or hide."""
        if self._hiding or not self.isVisible():
            return
        self._hiding = True
        self._fade.stop()
        self._fade.setDuration(anim.ms(FADE_OUT_MS))
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    # --------------------------------------------------------------- private
    def _on_fade_finished(self) -> None:
        if self._hiding:
            self._hide_now()

    def _hide_now(self) -> None:
        self._hiding = False
        self._fade.stop()
        self._effect.setOpacity(0.0)
        self.setVisible(False)
        self.line.stop()

    def _layout_children(self) -> None:
        self.line.setFixedSize(120, 150)
        self.line.move((self.width() - 120) // 2, (self.height() - 190) // 2)
        self.label.setGeometry(0, self.height() // 2 + 56, self.width(), 26)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_children()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 55))
        painter.end()
