"""Read-only Mod details as an in-window sliding subpage."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget

from .subpage import SubPage


class ModDetailPage(SubPage):
    def __init__(self, parent=None) -> None:
        super().__init__("Mod 详情", "查看 Mod 的作者、版本、前置与简介；返回按钮滑动回到 Mod 列表。", parent)
        self.setObjectName("ModDetailPage")
        card = QWidget()
        form = QFormLayout(card)
        form.setContentsMargins(18, 14, 18, 14)
        form.setSpacing(10)
        self.name = QLabel("—")
        self.name.setObjectName("PageTitle")
        form.addRow("名称", self.name)
        self.version = QLabel("—")
        form.addRow("版本", self.version)
        self.author = QLabel("—")
        form.addRow("作者", self.author)
        self.mod_id = QLabel("—")
        form.addRow("Mod ID", self.mod_id)
        self.loader = QLabel("—")
        form.addRow("加载器", self.loader)
        self.file = QLabel("—")
        self.file.setWordWrap(True)
        self.file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("文件", self.file)
        self.depends = QLabel("—")
        self.depends.setWordWrap(True)
        form.addRow("前置", self.depends)
        self.description = QLabel("—")
        self.description.setWordWrap(True)
        form.addRow("简介", self.description)
        self.body.addWidget(card)

    def set_mod(self, mod) -> None:
        self.name.setText(getattr(mod, "display_name", "") or "—")
        self.version.setText(getattr(mod, "display_version", "") or "—")
        self.author.setText(getattr(mod, "author", "") or "—")
        self.mod_id.setText(getattr(mod, "mod_id", "") or "—")
        self.loader.setText(getattr(mod, "loader", "") or "—")
        self.file.setText(str(getattr(mod, "path", "")))
        deps = getattr(mod, "depends", {}) or {}
        self.depends.setText(", ".join(f"{k} {v}" for k, v in deps.items()) or "无")
        self.description.setText(getattr(mod, "description", "") or "暂无简介")
