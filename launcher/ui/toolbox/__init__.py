"""百宝箱: 目录清理、Mod 管理、崩溃诊断、整合包、服务器、截图、优化与高速下载."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QTabWidget, QVBoxLayout, QWidget

from ..widgets import install_tab_transition

from .diagnose_tab import DiagnoseTab
from .download_tab import DownloadTab
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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("ToolboxScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        inner = QWidget()
        inner.setObjectName("PageInner")
        scroll.setWidget(inner)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(2, 8, 14, 10)
        layout.setSpacing(8)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("百宝箱")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        subtitle = QLabel(
            "目录与清理、可视化 Mod 管理、崩溃诊断、整合包、服务器、截图、性能优化和高速下载。"
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
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
        self.download_tab = DownloadTab()

        for widget, label in (
            (self.shortcuts_tab, "目录与备份"),
            (self.mods_tab, "Mod 管理"),
            (self.diagnose_tab, "崩溃诊断"),
            (self.modpack_tab, "整合包"),
            (self.servers_tab, "服务器"),
            (self.screenshots_tab, "截图"),
            (self.optimize_tab, "优化"),
            (self.download_tab, "高速下载"),
        ):
            self.tabs.addTab(widget, label)
        self.tabs.setMinimumHeight(620)
        layout.addWidget(self.tabs, 1)
        install_tab_transition(self.tabs)

        for tab in (
            self.shortcuts_tab,
            self.mods_tab,
            self.diagnose_tab,
            self.modpack_tab,
            self.servers_tab,
            self.screenshots_tab,
            self.optimize_tab,
            self.download_tab,
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
            self.download_tab,
        ):
            tab.set_game_dir(self.game_dir)

    def set_current_version(self, version_id: str) -> None:
        """Forward the version selected in the version library to the tabs."""
        forward = getattr(self.modpack_tab, "set_current_version", None)
        if forward is not None:
            forward(version_id)

    def focus_modpack(self, path: str) -> None:
        """Switch to the 整合包 tab and preload a dropped ``.mrpack``."""
        self.tabs.setCurrentWidget(self.modpack_tab)
        self.modpack_tab.load_file(path)

    def show_optimize(self) -> None:
        self.tabs.setCurrentWidget(self.optimize_tab)
