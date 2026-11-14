"""Developer-only pages: loading animation preview."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from .splash import LoadingSplash
from .subpage import SubPage


class AnimationPreviewPage(SubPage):
    def __init__(self, config, parent=None) -> None:
        super().__init__(
            "加载动画预览",
            "仅开发调试用途，非普通用户功能。蓝色细线 C 会持续旋转并做重力弹跳。",
            parent,
        )
        self.splash = LoadingSplash(config)
        self.splash.setWindowFlags(Qt.WindowType.Widget)
        self.splash.setParent(self)
        self.body.addWidget(self.splash)
        tip = QLabel("开关与速度设置位于「设置 → 开发者选项」。")
        tip.setObjectName("Hint")
        self.body.addWidget(tip)
