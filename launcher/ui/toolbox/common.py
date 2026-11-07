"""Shared helpers for the 百宝箱 tabs (cards, tasks, file shortcuts)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...services.util import human_size  # re-exported for the tabs
from ..tasks import Task, start_task
from ..widgets import HoverCard

__all__ = [
    "card",
    "human_size",
    "open_path",
    "Task",
    "start_task",
    "choose_file",
    "section_label",
]


def card(title: str | None = None, subtitle: str | None = None) -> tuple[HoverCard, QVBoxLayout]:
    frame = HoverCard()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 15, 18, 15)
    layout.setSpacing(10)
    if title:
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
    if subtitle:
        note = QLabel(subtitle)
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
    return frame, layout


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("FieldLabel")
    return label


def open_path(path: str | Path, create: bool = False) -> bool:
    target = Path(path)
    try:
        if create:
            target.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            return False
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        return True
    except OSError:
        return False


def choose_file(parent: QWidget, title: str, patterns: str, start: str = "") -> str:
    chosen, _ = QFileDialog.getOpenFileName(parent, title, start, patterns)
    return chosen


class ConfirmMixin:
    """Tiny helper for consistent confirmation dialogs."""

    def confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes


def make_toolbar(*buttons: QPushButton, stretch: bool = True) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    for button in buttons:
        row.addWidget(button)
    if stretch:
        row.addStretch(1)
    return row
