"""Embed launcher dialogs as sliding in-window pages instead of modal windows."""

from __future__ import annotations

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QDialog, QWidget

from .subpage import SubPage


class DialogPage(SubPage):
    def __init__(self, dialog: QDialog, title: str, parent=None) -> None:
        super().__init__(title, "返回按钮会滑动回到上一页。", parent)
        self.dialog = dialog
        dialog.setWindowFlags(Qt.WindowType.Widget)
        dialog.setParent(self)
        self.body.addWidget(dialog, 1)
        self.back_button.clicked.connect(dialog.reject)


def run_in_window(owner: QWidget, dialog: QDialog, title: str) -> int:
    """Run *dialog* inside the main window's page stack, never as a window."""
    host = owner.window()
    if hasattr(host, "run_dialog"):
        return host.run_dialog(dialog, title)
    return dialog.exec()
