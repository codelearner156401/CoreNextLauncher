"""Skin management as an in-window sliding subpage."""

from __future__ import annotations

from PySide6.QtCore import Signal

from .skin_panel import SkinPanel
from .subpage import SubPage


class SkinPage(SubPage):
    log_requested = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(
            "皮肤管理",
            "本地皮肤、正版皮肤与 Steve / Alex 2D 纸娃娃预览都在此页面完成，返回按钮滑动回到启动页。",
            parent,
        )
        self.setObjectName("SkinPage")
        self.panel = SkinPanel()
        self.panel.log_requested.connect(self.log_requested.emit)
        self.body.addWidget(self.panel, 1)

    def set_account(self, account) -> None:
        self.panel.set_account(account)
