"""百宝箱: 目录清理、Mod 管理、崩溃诊断、整合包、服务器、截图与优化."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from .diagnose_tab import DiagnoseTab
from .modpack_tab import ModpackImportDialog, ModpackTab
from .mods_tab import ModsTab
from .optimize_tab import OptimizeTab
from .screenshots_tab import ScreenshotsTab
from .servers_tab import ServersTab
from .shortcuts_tab import ShortcutsTab

__all__ = ["ToolboxPage", "ModpackImportDialog"]


class ToolboxPage(QWidget):
    log_requested = Signal(str, str)
    join_requested = Signal(object)
    install_version_requested = Signal(str)
    versions_changed = Signal()
    profile_installed = Signal(str)
    java_selected = Signal(str)
    settings_requested = Signal()

    def __init__(self, parent=None, game_dir: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("ToolboxPage")
        self.game_dir = game_dir

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 8, 14, 10)
        layout.setSpacing(8)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("百宝箱")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        subtitle = QLabel(
            "目录与清理、可视化 Mod 管理、崩溃诊断、整合包、服务器、截图和性能优化。"
        )
        subtitle.setObjectName("Muted")
        header.addWidget(subtitle)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("ToolTabs")
        self.shortcuts_tab = ShortcutsTab()
        self.mods_tab = ModsTab()
        self.diagnose_tab = DiagnoseTab()
        self.modpack_tab = ModpackTab()
        self.servers_tab = ServersTab()
        self.screenshots_tab = ScreenshotsTab()
        self.optimize_tab = OptimizeTab()

        for widget, label in (
            (self.shortcuts_tab, "目录与备份"),
            (self.mods_tab, "Mod 管理"),
            (self.diagnose_tab, "崩溃诊断"),
            (self.modpack_tab, "整合包"),
            (self.servers_tab, "服务器"),
            (self.screenshots_tab, "截图"),
            (self.optimize_tab, "优化"),
        ):
            self.tabs.addTab(widget, label)
        layout.addWidget(self.tabs, 1)

        for tab in (
            self.shortcuts_tab,
            self.mods_tab,
            self.diagnose_tab,
            self.modpack_tab,
            self.servers_tab,
            self.screenshots_tab,
            self.optimize_tab,
        ):
            tab.log_requested.connect(self.log_requested)
            if hasattr(tab, "join_requested"):
                tab.join_requested.connect(self.join_requested)
            if hasattr(tab, "install_version_requested"):
                tab.install_version_requested.connect(self.install_version_requested)
            if hasattr(tab, "java_selected"):
                tab.java_selected.connect(self.java_selected)
            if hasattr(tab, "settings_requested"):
                tab.settings_requested.connect(self.settings_requested)
        self.modpack_tab.versions_changed.connect(self.versions_changed)
        self.optimize_tab.versions_changed.connect(self.profile_installed)
        self.optimize_tab.versions_changed.connect(
            lambda _profile: self.versions_changed.emit()
        )

        self.set_game_dir(game_dir)

    # ------------------------------------------------------------------ API
    def set_game_dir(self, game_dir: str) -> None:
        self.game_dir = game_dir or ""
        for tab in (
            self.shortcuts_tab,
            self.mods_tab,
            self.diagnose_tab,
            self.modpack_tab,
            self.servers_tab,
            self.screenshots_tab,
            self.optimize_tab,
        ):
            tab.set_game_dir(self.game_dir)

    def focus_modpack(self, path: str) -> None:
        """Switch to the 整合包 tab and preload a dropped ``.mrpack``."""
        self.tabs.setCurrentWidget(self.modpack_tab)
        self.modpack_tab.load_file(path)

    def show_optimize(self) -> None:
        self.tabs.setCurrentWidget(self.optimize_tab)
