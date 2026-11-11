"""In-window subpage frame with a PCL-style back button."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class SubPage(QWidget):
    back_requested = Signal()

    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 8, 12, 8)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        self.back_button = QPushButton("← 返回")
        self.back_button.setObjectName("Ghost")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self.back_requested.emit)
        header.addWidget(self.back_button)
        text = QVBoxLayout()
        text.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        text.addWidget(title_label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Muted")
            sub.setWordWrap(True)
            text.addWidget(sub)
        header.addLayout(text, 1)
        outer.addLayout(header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        outer.addLayout(self.body, 1)

    def add_widget(self, widget: QWidget, stretch: int = 1) -> None:
        self.body.addWidget(widget, stretch)
