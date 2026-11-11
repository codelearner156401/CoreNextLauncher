"""Main window: version sidebar, tabbed pages and the launch bar."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_VERSION, anim, dev_state
from .account import clear_account, load_account, save_account
from .auth import AuthError, AuthNetworkError, refresh_account
from .config import AppConfig, save_config
from .constants import DEFAULT_MS_CLIENT_ID
from .core_client import CoreClient
from .java import normalize_java_path
from .paths import ensure_workspace, workspace_root
from .services import backup as backup_service
from .services import crash as crash_service
from .services import java_install
from .services import isolation
from .services import java_scan
from .services import mods as mods_service
from .services import updater
from .theme import THEME_LABELS, apply_acrylic, apply_theme, apply_window_effects
from .ui.account_dialog import AccountDialog
from .ui.crash_dialog import CrashDialog
from .ui.dialog_page import DialogPage, run_in_window
from .ui.glass import BackgroundWidget, GlassFrame
from .ui.instance_page import InstancePage
from .ui.inwindow import install as install_inwindow_hooks, install_input_hooks, install_file_hooks, install_color_hooks, set_host as set_inwindow_host
from .ui.loading_overlay import LoadingOverlay
from .ui.mod_detail_page import ModDetailPage
from .ui.pages import InstallPage, LogPage, SettingsPage
from .ui.skin_page import SkinPage
from .ui.start_page import StartPage
from .ui.version_browser import VersionBrowserPage
from .ui.tasks import cancel_tasks, start_task
from .ui.toolbox import ModpackImportDialog, ToolboxPage
from .ui.update_dialog import UpdateDialog
from .ui.widgets import (
    TYPE_LABELS,
    AnimatedLaunchButton,
    AnimatedTabView,
    VersionList,
    natural_key,
)
from .workers import InstallWorker, LaunchWorker, ScanWorker


class DragHeader(QFrame):
    """Frameless title area: drag to move, double-click to maximise."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag = None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
            self._drag = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if window.isMaximized():
                window.showNormal()
            else:
                window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def theme_colors_bg(base: str) -> str:
    from . import theme as _theme

    return _theme.colors(base)["bg"]


class MainWindow(QMainWindow):
    def __init__(self, client: CoreClient, config: AppConfig) -> None:
        super().__init__()
        self.client = client
        self.config = config
        set_inwindow_host(self)
        install_inwindow_hooks()
        install_input_hooks()
        install_file_hooks()
        install_color_hooks()
        anim.set_speed(getattr(config, "animation_speed", 1.0))
        self.workspace = ensure_workspace()
        # Developer mode and offline login are session-only: wipe them before
        # any UI exists, so a stale state file or a killed process cannot
        # re-enable them on the next start.
        self.dev_state = dev_state.reset_session_state()
        self.account = load_account()
        self._scan_worker: ScanWorker | None = None
        self._launch_worker: LaunchWorker | None = None
        self._install_worker: InstallWorker | None = None
        self._scan_game_dir: str = ""
        self._launch_reported = False
        self._install_reported = False
        self._last_install_percent = -1
        self._install_completed_version = ""
        self._last_background_path = ""
        self._launch_started_at = 0.0
        self._preparing = False
        self._pending_select = ""
        self._update_checked = False
        self._appearance_save_timer = QTimer(self)
        self._appearance_save_timer.setSingleShot(True)
        self._appearance_save_timer.setInterval(450)
        self._appearance_save_timer.timeout.connect(lambda: self._save_config(silent=True))

        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle(f"{APP_NAME} · Minecraft")
        self.resize(1240, 820)
        self.setMinimumSize(1000, 680)
        self.setAcceptDrops(True)

        self.backdrop = BackgroundWidget()
        root = self.backdrop
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 16, 22, 18)
        outer.setSpacing(14)
        outer.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._build_sidebar(), 0)

        right = QVBoxLayout()
        right.setSpacing(14)
        self.tabs = AnimatedTabView()
        self.start_page = StartPage()
        self.settings_page = SettingsPage(config)
        self.settings_page.apply_developer_state(self.dev_state)
        self.instance_page = InstancePage()
        self.instance_page.set_active_dir(config.game_dir)
        self.install_page = InstallPage()
        self.toolbox_page = ToolboxPage(self, config.game_dir)
        self.log_page = LogPage()
        self.tabs.addTab(self.start_page, "启动")
        self.tabs.addTab(self.instance_page, "实例")
        self.tabs.addTab(self.install_page, "下载 / 安装")
        self.tabs.addTab(self.toolbox_page, "百宝箱")
        self.tabs.addTab(self.log_page, "日志")
        self.tabs.addTab(self.settings_page, "设置")
        self.tabs.setCurrentIndex(0)
        right.addWidget(self.tabs, 1)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)
        outer.addWidget(self._build_launch_bar())

        self._connect_signals()
        self._install_shortcuts()
        self.backdrop.image_error.connect(
            lambda message: self.log_page.log("warn", message)
        )
        self._apply_appearance()
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.setGeometry(self.rect())
        _values = self.settings_page.values()
        self.start_page.set_environment(
            str(_values.get("java_path", "") or ""),
            f"{_values.get('max_mem', '自动')} / {_values.get('min_mem', '自动')}",
        )
        if self.settings_page.memory_adjusted():
            corrected_min = self.settings_page.values().get("min_mem", "")
            self.log_page.log(
                "warn",
                f"内存参数已修正：Xms 不能大于 Xmx，已将最小内存调整为 {corrected_min}。",
            )
        self._last_background_path = str(
            self.settings_page.values().get("background_image", "")
        )
        self._refresh_account_ui()
        self._refresh_kernel(log=True)
        self.log_page.log("info", "CoreNextLauncher UI 已启动")
        self.log_page.log("muted", f"版本 {APP_VERSION} · PySide6 + 液态/毛玻璃/HTML 三套界面")
        self.log_page.log("muted", f"工作区：{workspace_root()}")
        self.log_page.log(
            "muted", "开发者模式与离线登录已在本次启动时重置为关闭（仅本次运行可临时开启）"
        )
        QTimer.singleShot(150, self.refresh_versions)
        QTimer.singleShot(2600, self._maybe_auto_check_update)

    # ------------------------------------------------------------------ UI
    def _build_header(self) -> QFrame:
        header = DragHeader()
        header.setObjectName("Header")
        row = QHBoxLayout(header)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(12)

        logo = QLabel("C")
        logo.setObjectName("Logo")
        logo.setFixedSize(42, 42)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(logo)

        text = QVBoxLayout()
        text.setSpacing(0)
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        subtitle = QLabel("Minecraft 启动器 · C++ 内核驱动")
        subtitle.setObjectName("AppSubtitle")
        text.addWidget(title)
        text.addWidget(subtitle)
        row.addLayout(text)
        row.addStretch(1)

        self.account_button = QPushButton("未登录")
        self.account_button.setObjectName("Ghost")
        self.account_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.account_button.setToolTip("Microsoft 正版登录")
        self.account_button.clicked.connect(self.open_account)
        row.addWidget(self.account_button)
        row.addSpacing(4)

        self.kernel_chip = QLabel("● 内核检测中")
        self.kernel_chip.setObjectName("KernelChip")
        row.addWidget(self.kernel_chip)
        row.addSpacing(10)

        self.min_button = QPushButton("—")
        self.min_button.setObjectName("WindowButton")
        self.min_button.setToolTip("最小化")
        self.min_button.setFixedSize(32, 30)
        self.min_button.clicked.connect(self.showMinimized)
        row.addWidget(self.min_button)
        self.max_button = QPushButton("□")
        self.max_button.setObjectName("WindowButton")
        self.max_button.setToolTip("最大化 / 还原")
        self.max_button.setFixedSize(32, 30)
        self.max_button.clicked.connect(self._toggle_maximized)
        row.addWidget(self.max_button)
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("WindowCloseButton")
        self.close_button.setToolTip("关闭")
        self.close_button.setFixedSize(32, 30)
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.close_button)
        return header

    def _build_sidebar(self) -> QFrame:
        frame = GlassFrame(radius=14.0)
        frame.setObjectName("Sidebar")
        frame.setFixedWidth(302)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("版本库")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("Ghost")
        self.refresh_button.clicked.connect(self.refresh_versions)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("SearchBox")
        self.search_box.setPlaceholderText("搜索版本…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter_versions)
        layout.addWidget(self.search_box)

        self.version_list = VersionList()
        self.version_list.currentItemChanged.connect(self._on_version_changed)
        layout.addWidget(self.version_list, 1)

        self.empty_hint = QLabel("未找到版本\n请检查“游戏目录”设置")
        self.empty_hint.setObjectName("EmptyHint")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.hide()
        layout.addWidget(self.empty_hint)

        self.footer = QLabel("共 0 个版本")
        self.footer.setObjectName("FooterHint")
        layout.addWidget(self.footer)
        return frame

    def _build_launch_bar(self) -> QFrame:
        bar = GlassFrame(radius=14.0, tint_alpha=188)
        bar.setObjectName("LaunchBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 12, 18, 12)
        row.setSpacing(14)

        text = QVBoxLayout()
        text.setSpacing(1)
        self.launch_title = QLabel("请选择版本")
        self.launch_title.setObjectName("LaunchTitle")
        self.launch_sub = QLabel("从左侧版本库选择一个版本后启动")
        self.launch_sub.setObjectName("SelectedPath")
        text.addWidget(self.launch_title)
        text.addWidget(self.launch_sub)
        row.addLayout(text, 1)

        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("DangerGhost")
        self.stop_button.hide()
        self.stop_button.clicked.connect(self.stop_launch)
        row.addWidget(self.stop_button)

        self.launch_wrapper = QWidget()
        self.launch_wrapper.setFixedSize(330, 120)
        self.launch_button = AnimatedLaunchButton(self.launch_wrapper)
        self.launch_button.setObjectName("LaunchButton")
        self.launch_button.setText("▶   启动游戏")
        self.launch_button.set_rest_geometry(QRect(45, 34, 240, 52))
        self.launch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_button.setEnabled(False)
        self.launch_button.clicked.connect(self.start_launch)
        row.addWidget(self.launch_wrapper)
        return bar

    def _connect_signals(self) -> None:
        self.settings_page.theme_changed.connect(self._on_theme_changed)
        self.settings_page.settings_saved.connect(self._on_settings_saved)
        self.settings_page.appearance_changed.connect(self._on_appearance_changed)
        self.settings_page.animation_speed_changed.connect(self._on_animation_speed_changed)
        self.settings_page.kernel_recheck.connect(lambda: self._refresh_kernel(log=True))
        self.settings_page.check_update_requested.connect(lambda: self.check_updates())
        self.install_page.log_requested.connect(self.log_page.log)
        self.install_page.install_requested.connect(self.start_install)
        self.toolbox_page.log_requested.connect(self.log_page.log)
        self.toolbox_page.join_requested.connect(self._join_server)
        self.toolbox_page.install_version_requested.connect(self._install_from_toolbox)
        self.toolbox_page.versions_changed.connect(self.refresh_versions)
        self.toolbox_page.profile_installed.connect(self._on_profile_installed)
        self.toolbox_page.java_selected.connect(self._on_java_selected)
        self.toolbox_page.settings_requested.connect(
            lambda: self.tabs.setCurrentWidget(self.settings_page)
        )
        self.settings_page.account_login_requested.connect(self.open_account)
        self.settings_page.account_logout_requested.connect(self.logout_account)
        self.settings_page.developer_unlocked.connect(self._on_developer_unlocked)
        self.settings_page.developer_state_changed.connect(
            self._on_developer_state_changed
        )
        self.instance_page.log_requested.connect(self.log_page.log)
        self.instance_page.instance_changed.connect(self._on_instance_changed)
        self.start_page.launch_requested.connect(self.start_launch)
        self.start_page.open_folder_requested.connect(self._open_game_folder)
        self.start_page.open_logs_requested.connect(
            lambda: self.tabs.setCurrentWidget(self.log_page)
        )
        self.start_page.log_requested.connect(self.log_page.log)
        self.start_page.skin_requested.connect(self.open_skin_page)
        self.install_page.browse_requested.connect(self.open_version_browser)
        self.toolbox_page.mods_tab.detail_requested.connect(self.open_mod_detail)

    def run_dialog(self, dialog, title: str = "") -> int:
        page = DialogPage(dialog, title or getattr(dialog, "windowTitle", lambda: "对话")())
        loop = QEventLoop()
        dialog.finished.connect(lambda _result: loop.quit())
        self.tabs.push_page(page)
        loop.exec()
        self.tabs.pop_page()
        return int(dialog.result())

    def push_subpage(self, page) -> None:
        page.back_requested.connect(self.tabs.pop_page)
        self.tabs.push_page(page)

    def open_version_browser(self) -> None:
        page = VersionBrowserPage()
        page.install_requested.connect(self.start_install)
        page.install_requested.connect(lambda _version: self.tabs.pop_page())
        self.push_subpage(page)

    def open_skin_page(self) -> None:
        page = SkinPage()
        page.set_account(self.account)
        page.log_requested.connect(self.log_page.log)
        self.push_subpage(page)

    def open_mod_detail(self, mod) -> None:
        page = ModDetailPage()
        page.set_mod(mod)
        self.push_subpage(page)

    def _open_game_folder(self) -> None:
        game_dir = str(self.settings_page.values().get("game_dir", "") or "")
        if not game_dir:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(game_dir))

    def _install_shortcuts(self) -> None:
        refresh = QAction(self)
        refresh.setShortcut(QKeySequence("F5"))
        refresh.triggered.connect(self.refresh_versions)
        self.addAction(refresh)

        launch = QAction(self)
        launch.setShortcut(QKeySequence("Ctrl+Return"))
        launch.triggered.connect(self.start_launch)
        self.addAction(launch)

    def _repolish(self, widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    # ------------------------------------------------------------- kernel
    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange and hasattr(self, "max_button"):
            self.max_button.setText("❐" if self.isMaximized() else "□")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        grip = getattr(self, "_size_grip", None)
        if grip is not None:
            grip.move(self.width() - grip.width() - 2, self.height() - grip.height() - 2)
            grip.raise_()
        overlay = getattr(self, "loading_overlay", None)
        if overlay is not None:
            overlay.setGeometry(self.rect())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not hasattr(self, "_size_grip"):
            self._size_grip = QSizeGrip(self)
            self._size_grip.setFixedSize(18, 18)
            self._size_grip.raise_()
        self._apply_appearance()

    def _refresh_kernel(self, log: bool = False) -> None:
        ok = self.client.available
        self.kernel_chip.setText("● 内核就绪" if ok else "● 内核缺失")
        self.kernel_chip.setProperty("state", "ok" if ok else "bad")
        self._repolish(self.kernel_chip)
        self.settings_page.set_kernel(ok, str(self.client.exe_path), self.client.kernel_size())
        if log:
            if ok:
                size = self.client.kernel_size() / (1024 * 1024)
                self.log_page.log("success", f"C++ 内核已就绪：{self.client.exe_path}（{size:.1f} MB）")
            else:
                self.log_page.log("error", f"未找到 C++ 内核：{self.client.exe_path}")
                self.log_page.log("muted", "请先编译 mc_core.exe，或使用 --exe 指定路径。")

    # -------------------------------------------------------------- scanning
    def refresh_versions(self) -> None:
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        game_dir = self.settings_page.values()["game_dir"]
        self._scan_game_dir = game_dir
        if not game_dir or not Path(game_dir).is_dir():
            self.version_list.clear()
            self.footer.setText("目录不存在")
            self.empty_hint.show()
            self.log_page.log("warn", f"游戏目录不存在：{game_dir or '(空)'}")
            return

        self.refresh_button.setEnabled(False)
        self.footer.setText("扫描中…")
        self.log_page.log("info", f"扫描版本目录：{game_dir}")
        self.toolbox_page.set_game_dir(game_dir)
        worker = ScanWorker(self.client, game_dir, self)
        worker.succeeded.connect(self._on_versions)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(lambda: self._scan_finished(worker))
        self._scan_worker = worker
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.show_loading("正在扫描版本文件…")
        worker.start()

    def _scan_finished(self, worker: ScanWorker) -> None:
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.stop()
        self.refresh_button.setEnabled(True)
        if self._scan_worker is worker:
            self._scan_worker = None
        worker.deleteLater()

    def _on_scan_failed(self, message: str) -> None:
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.stop()
        self.footer.setText("扫描失败")
        self.empty_hint.show()
        self.log_page.log("error", f"版本扫描失败：{message}")

    def _on_versions(self, entries: list) -> None:
        game_dir = Path(self._scan_game_dir)
        enriched: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            version_id = str(entry.get("id") or "")
            if not version_id:
                continue
            version_type = str(entry.get("type") or "release")
            version_path = game_dir / "versions" / version_id
            raw = None
            manifest = version_path / f"{version_id}.json"
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("type"):
                    version_type = str(raw["type"])
            except (OSError, json.JSONDecodeError):
                raw = None
            enriched.append(
                {
                    "id": version_id,
                    "type": version_type,
                    "dir": str(version_path),
                    "raw": raw,
                }
            )

        enriched.sort(key=lambda item: natural_key(item["id"]), reverse=True)
        self.version_list.clear()
        for item in enriched:
            self.version_list.add_version(item)

        self.footer.setText(f"共 {len(enriched)} 个版本")
        self.empty_hint.setVisible(not enriched)
        self.log_page.log(
            "success" if enriched else "warn",
            f"扫描完成：找到 {len(enriched)} 个版本" if enriched else "扫描完成：没有已安装版本",
        )

        if enriched:
            preferred = self._pending_select or self.config.last_version
            row = next((i for i, item in enumerate(enriched) if item["id"] == preferred), 0)
            self.version_list.setCurrentRow(row)
            if self._pending_select:
                self.log_page.log("success", f"已切换到新安装的版本：{self._pending_select}")
                self._pending_select = ""
        else:
            self._on_version_changed(None, None)

    def _filter_versions(self, text: str) -> None:
        visible = self.version_list.filter_items(text)
        if text:
            self.footer.setText(f"显示 {visible} / {self.version_list.count()} 个版本")
            self.empty_hint.setText("没有匹配的版本")
            self.empty_hint.setVisible(visible == 0)
        else:
            self.footer.setText(f"共 {self.version_list.count()} 个版本")
            self.empty_hint.setText("未找到版本\n请检查“游戏目录”设置")
            self.empty_hint.setVisible(self.version_list.count() == 0)

    def _on_version_changed(self, current, _previous) -> None:
        data = self.version_list.current_data() if current is not None else None
        if not data:
            self.launch_title.setText("请选择版本")
            self.launch_sub.setText("从左侧版本库选择一个版本后启动")
            self.install_page.set_version(self._scan_game_dir, None)
            self.launch_button.setEnabled(False)
            self.start_page.set_version("")
            return
        version_id = str(data.get("id", ""))
        version_type = str(data.get("type", "release"))
        label = TYPE_LABELS.get(version_type, version_type)
        self.launch_title.setText(version_id)
        self.launch_sub.setText(f"{label} · 已安装")
        self.launch_sub.setToolTip(str(data.get("dir", "")))
        self.start_page.set_version(version_id, label)
        self.install_page.set_version(self._scan_game_dir, data)
        self.config.last_version = version_id
        if not (self._launch_worker is not None and self._launch_worker.isRunning()):
            self.launch_button.setEnabled(True)

    # --------------------------------------------------------------- launch
    def start_launch(self, *_args, server=None) -> None:
        if self._preparing:
            return
        if self._launch_worker is not None and self._launch_worker.isRunning():
            return
        if self._install_worker is not None and self._install_worker.isRunning():
            self.tabs.setCurrentWidget(self.install_page)
            self.log_page.log("warn", "正在安装版本，请等待安装完成后再启动游戏。")
            return
        data = self.version_list.current_data()
        if not data:
            self.log_page.log("warn", "尚未选择版本，无法启动。")
            QMessageBox.information(self, "未选择版本", "请先在左侧版本库中选择一个版本。")
            return

        values = self.settings_page.values()
        if self.settings_page.memory_adjusted():
            self.log_page.log(
                "warn",
                f"最小内存大于最大内存，已自动将 Xms 调整为 {values['min_mem']}。",
            )
        version_id = str(data.get("id", ""))
        game_dir = Path(values["game_dir"])
        if not (game_dir / "versions" / version_id).is_dir():
            self.log_page.log("error", f"版本目录不存在：{game_dir / 'versions' / version_id}")
            QMessageBox.warning(self, "版本目录不存在", "请检查“游戏目录”设置后重新扫描。")
            return

        configured_java = values["java_path"]
        java_path = normalize_java_path(configured_java)
        if java_path != configured_java:
            self.log_page.log(
                "muted", f"Java 路径已自动补全：{configured_java} → {java_path}"
            )
        java_path = self._resolve_java(data, java_path)
        if not java_path:
            return
        if not Path(java_path).is_file():
            self.log_page.log("warn", f"Java 路径不存在：{java_path}")
            if self._offer_java_install(java_path, data, version_id):
                return

        # Keep the last used settings even when the user launches without
        # pressing the explicit "保存设置" button.
        self._save_config(silent=True)

        offline = self.settings_page.developer_state().offline_active
        if offline:
            self.log_page.log(
                "warn",
                "离线模式已启用（仅本次运行有效）：本次启动不会验证 Microsoft 账号。",
            )
        if offline:
            account_payload = {
                "name": values["player_name"],
                "uuid": "00000000-0000-0000-0000-000000000000",
                "access_token": "0",
                "user_type": "legacy",
            }
        else:
            online = self._ensure_account()
            if online is None:
                self.tabs.setCurrentWidget(self.log_page)
                self.log_page.log("warn", "未登录正版账号，已取消启动。")
                return
            account_payload = online.launch_account()

        launch_game_dir = isolation.game_dir(game_dir, version_id)
        if launch_game_dir != game_dir:
            self.log_page.log("info", f"版本隔离已启用：{launch_game_dir}")
        if not self._preflight_mods(launch_game_dir):
            return

        payload = {
            "mc_dir": str(game_dir),
            "game_dir": str(launch_game_dir),
            "version_id": version_id,
            "account": account_payload,
            "java_path": java_path,
            "max_mem": values["max_mem"],
            "min_mem": values["min_mem"],
            "offline_mode": offline,
            "jvm_args": values.get("jvm_args", []),
        }
        if server is not None:
            payload["extra_game_args"] = [
                "--server",
                str(server.address),
                "--port",
                str(server.port),
            ]

        self.tabs.setCurrentWidget(self.log_page)
        self.log_page.log("info", "────────────  启动会话  ────────────")
        self.log_page.log("info", f"版本：{version_id} · {TYPE_LABELS.get(str(data.get('type')), '版本')}")
        mode_text = "离线模式" if offline else f"正版账号 {account_payload.get('name', '')}"
        self.log_page.log("muted", f"Java：{java_path} · 内存：{values['max_mem']} · {mode_text}")
        if server is not None:
            self.log_page.log("info", f"一键进服：{server.address}:{server.port}")

        self._launch_payload = payload
        if self.config.auto_backup:
            self._backup_then_launch(payload)
        else:
            self._launch_now(payload)

    def _preflight_mods(self, game_dir: Path) -> bool:
        try:
            mod_list, _folder = mods_service.load_mods(game_dir)
        except Exception:
            return True
        if not mod_list:
            return True
        report = mods_service.scan_conflicts(mod_list)
        if report.healthy:
            self.log_page.log("muted", f"启动前 Mod 预扫描：{len(mod_list)} 个 Mod，未发现冲突。")
            return True
        lines = [f"启动前扫描到 {len(mod_list)} 个 Mod，存在以下问题：", ""]
        if report.duplicates:
            lines.append("重复 Mod ID：")
            for mod_id, names in sorted(report.duplicates.items()):
                lines.append(f"  · {mod_id}：{', '.join(sorted(names))}")
        if report.duplicate_files:
            lines.append("同名 jar 文件：")
            for _name, names in sorted(report.duplicate_files.items()):
                lines.append(f"  · {', '.join(sorted(names))}")
        if report.missing:
            lines.append("缺失前置：")
            for owner, dep, jar in report.missing[:25]:
                lines.append(f"  · {owner} 需要 {dep}（{jar}）")
        if report.disabled_deps:
            lines.append("前置已安装但被禁用：")
            for owner, dep, jar in report.disabled_deps[:15]:
                lines.append(f"  · {owner} 需要 {dep}（{jar}）")
        lines.append("")
        lines.append("仍要继续启动吗？")
        answer = QMessageBox.question(
            self,
            "Mod 启动前预检测",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.log_page.log("warn", "已取消启动：Mod 预检测发现问题。")
            return False
        self.log_page.log("warn", "用户选择忽略 Mod 预检测问题，继续启动。")
        return True

    # ------------------------------------------------------- pre-launch backup
    def _backup_then_launch(self, payload: dict) -> None:
        game_dir = Path(str(payload.get("game_dir") or payload.get("mc_dir", "")))
        if not (game_dir / "saves").is_dir():
            self._launch_now(payload)
            return
        self._preparing = True
        self.launch_button.setEnabled(False)
        self.launch_button.setText("备份存档…")
        keep = int(getattr(self.config, "backup_keep", 5) or 5)
        self.log_page.log("muted", f"正在静默备份存档（每个存档保留 {keep} 份）…")

        def work(progress, cancelled):
            return backup_service.backup_saves(
                game_dir,
                keep=keep,
                only_changed=True,
                progress=lambda message, done, total: progress(message, done, total),
                cancel=cancelled,
            )

        start_task(
            self,
            work,
            on_done=self._on_launch_backup_done,
            on_failed=self._on_launch_backup_failed,
        )

    def _on_launch_backup_done(self, result: backup_service.BackupResult) -> None:
        if result.created:
            self.log_page.log("success", "存档备份完成：" + backup_service.describe(result))
        else:
            self.log_page.log("muted", "存档没有变化，已跳过备份（节省启动时间）。")
        if result.failed:
            self.log_page.log("warn", "部分存档备份失败：" + "；".join(result.failed[:3]))
        self._launch_now(self._launch_payload)

    def _on_launch_backup_failed(self, message: str) -> None:
        # A failed backup must never block the game from starting.
        self.log_page.log("warn", f"存档备份失败，继续启动：{message}")
        self._launch_now(self._launch_payload)

    def _launch_now(self, payload: dict) -> None:
        self._preparing = False
        self._launch_started_at = time.time()
        self._launch_reported = False
        self.log_page.log("event", "已向 mc_core.exe 发送 launch 请求，等待内核事件…")
        worker = LaunchWorker(self.client, payload, self)
        worker.event.connect(self._on_core_event)
        worker.finished_with.connect(self._on_launch_finished)
        worker.failed.connect(self._on_launch_failed)
        worker.finished.connect(lambda: self._launch_worker_finished(worker))
        self._launch_worker = worker
        self._set_running(True)
        worker.start()

    # --------------------------------------------------------- auto java setup
    def _offer_java_install(
        self,
        java_path: str,
        version_data: dict,
        version_id: str,
        required_major: int = 0,
    ) -> bool:
        if not self.config.auto_java_install:
            return False
        manifest = version_data.get("raw") if isinstance(version_data.get("raw"), dict) else {}
        meta = manifest.get("javaVersion") if isinstance(manifest.get("javaVersion"), dict) else {}
        try:
            major = int(meta.get("majorVersion") or 0)
        except (TypeError, ValueError):
            major = 0
        major = required_major or major or java_install.suggested_major(version_id)
        answer = QMessageBox.question(
            self,
            "缺少匹配的 Java 运行时",
            f"当前 Java 不可用或版本不匹配：\n{java_path}\n\n"
            f"该版本需要 Java {major}。是否从 Adoptium 自动下载安装 "
            f"Temurin {major} 并设为启动 Java？\n"
            "（约 180 MB，需要联网，安装后自动填入设置）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._install_java_async(major)
        return True

    def _install_java_async(self, major: int) -> None:
        self.tabs.setCurrentWidget(self.log_page)
        self.log_page.log("info", f"开始从 Adoptium 下载 Temurin {major}…")
        self._preparing = True
        self.launch_button.setEnabled(False)
        self.launch_button.setText("安装 Java…")

        def work(progress, cancelled):
            return java_install.install_java(
                major,
                progress=lambda stage, done, total: progress(stage, done, total),
                cancel=cancelled,
            )

        start_task(
            self,
            work,
            on_done=self._on_java_installed,
            on_failed=self._on_java_install_failed,
            on_progress=lambda message, done, total: self.log_page.log(
                "muted", f"[Java] {message}"
            ),
        )

    def _on_java_installed(self, java_exe: Path) -> None:
        self._preparing = False
        self._set_running(False)
        self.settings_page.set_java_path(str(java_exe))
        self._save_config(silent=True)
        self.log_page.log("success", f"Java 已安装并设为启动运行时：{java_exe}")
        QMessageBox.information(
            self,
            "Java 安装完成",
            f"已自动配置 Java：\n{java_exe}\n\n请再次点击“启动游戏”。",
        )

    def _on_java_install_failed(self, message: str) -> None:
        self._preparing = False
        self._set_running(False)
        self.log_page.log("error", f"Java 自动安装失败：{message}")
        QMessageBox.warning(
            self,
            "Java 安装失败",
            f"{message}\n\n可以手动安装 Java 21，然后在设置页选择它。",
        )

    # ------------------------------------------------------ toolbox bridges
    def _join_server(self, entry) -> None:
        if getattr(entry, "version_id", ""):
            for row in range(self.version_list.count()):
                item = self.version_list.item(row)
                data = item.data(Qt.ItemDataRole.UserRole) or {}
                if str(data.get("id")) == entry.version_id:
                    self.version_list.setCurrentRow(row)
                    break
        if self.version_list.current_data() is None:
            QMessageBox.information(
                self,
                "未选择版本",
                "请先在左侧版本库选择要用于进服的版本（或在服务器条目里指定启动版本）。",
            )
            return
        self.start_launch(server=entry)

    def _install_from_toolbox(self, version_id: str) -> None:
        self.tabs.setCurrentWidget(self.install_page)
        self.install_page.install_version_edit.setText(version_id)
        self.start_install(version_id)

    def _on_profile_installed(self, profile_id: str) -> None:
        if not profile_id:
            return
        self._pending_select = profile_id
        self.refresh_versions()

    def _on_java_selected(self, path: str) -> None:
        if not path:
            return
        self.settings_page.set_java_path(path)
        self._save_config(silent=True)
        self.log_page.log("success", f"启动 Java 已设为：{path}")

    def _on_instance_changed(self, path: str) -> None:
        if not path:
            return
        self.settings_page.set_game_dir(path)
        self._save_config(silent=True)
        self.toolbox_page.set_game_dir(path)
        self.instance_page.set_active_dir(path)
        self.log_page.log("success", f"已切换实例：{path}")
        self.refresh_versions()

    # -------------------------------------------------------------- updates
    def _maybe_auto_check_update(self) -> None:
        if self._update_checked:
            return
        self._update_checked = True
        repo = str(getattr(self.config, "update_repo", "") or "")
        if not repo or not getattr(self.config, "auto_update_check", True):
            return
        self.check_updates(silent=True)

    def check_updates(self, silent: bool = False) -> None:
        repo = self.settings_page.values().get("update_repo", "")
        if not repo:
            if not silent:
                QMessageBox.information(
                    self,
                    "未配置更新源",
                    "请先在「设置 → 自动化」中填写发布 Release 的 GitHub 仓库（owner/repo）。",
                )
            return
        self.settings_page.check_update_button.setEnabled(False)
        if not silent:
            self.log_page.log("info", f"正在检查更新：{repo}")

        def work(progress, cancelled):
            return updater.check(repo, APP_VERSION)

        start_task(
            self,
            work,
            on_done=lambda info: self._on_update_checked(info, silent),
            on_failed=lambda message: self._on_update_check_failed(message, silent),
        )

    def _on_update_checked(self, info, silent: bool) -> None:
        self.settings_page.check_update_button.setEnabled(True)
        if info is None:
            self.log_page.log("success", f"已是最新版本 {APP_VERSION}")
            if not silent:
                QMessageBox.information(
                    self, "已是最新版本", f"当前版本 {APP_VERSION} 已经是最新版。"
                )
            return
        self.log_page.log("info", f"发现新版本 {info.version}（当前 {APP_VERSION}）")
        dialog = UpdateDialog(self, info, on_log=self.log_page.log)
        self.run_dialog(dialog, "启动器更新")

    def _on_update_check_failed(self, message: str, silent: bool) -> None:
        self.settings_page.check_update_button.setEnabled(True)
        self.log_page.log("warn", f"检查更新失败：{message}")
        if not silent:
            QMessageBox.warning(self, "检查更新失败", message)

    # ------------------------------------------------------------- drag/drop
    def _mrpack_paths(self, mime) -> list[str]:
        if not mime.hasUrls():
            return []
        return [
            url.toLocalFile()
            for url in mime.urls()
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".mrpack")
        ]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._mrpack_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = self._mrpack_paths(event.mimeData())
        if not paths:
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        self._open_modpack_dialog(paths[0])

    def _open_modpack_dialog(self, path: str) -> None:
        game_dir = self.settings_page.values().get("game_dir", "")
        if not game_dir or not Path(game_dir).is_dir():
            QMessageBox.warning(
                self, "未设置游戏目录", "请先在设置页选择 .minecraft 目录，再导入整合包。"
            )
            return
        self.log_page.log("info", f"准备导入整合包：{path}")
        dialog = ModpackImportDialog(
            self,
            path,
            game_dir,
            on_log=self.log_page.log,
            on_versions_changed=self.refresh_versions,
            on_install_version=self._install_from_toolbox,
        )
        self.run_dialog(dialog, "导入整合包")

    @staticmethod
    def _required_java_major(version_data: dict, version_id: str) -> int:
        manifest = version_data.get("raw")
        if isinstance(manifest, dict):
            meta = manifest.get("javaVersion")
            if isinstance(meta, dict):
                try:
                    major = int(meta.get("majorVersion") or 0)
                except (TypeError, ValueError):
                    major = 0
                if major:
                    return major
        match = re.search(r"(1\.\d+(?:\.\d+)?)", version_id or "")
        if not match:
            match = re.match(r"(\d+\.\d+(?:\.\d+)?)", version_id or "")
        if match:
            return java_install.suggested_major(match.group(1))
        return 0

    @staticmethod
    def _java_compatible(required: int, actual: int) -> bool:
        if not required or not actual:
            return True
        if required <= 8:
            return actual == required
        if required == 16:
            return actual in (16, 17)
        # Java 17+ runtimes can usually run versions that only require 17.
        return actual >= required

    def _resolve_java(self, version_data: dict, configured: str) -> str:
        """Pick a runtime that matches the Java major the version expects."""
        manifest = version_data.get("raw")
        meta = manifest.get("javaVersion") if isinstance(manifest, dict) else None
        component = ""
        major = 0
        if isinstance(meta, dict):
            component = str(meta.get("component") or "")
            try:
                major = int(meta.get("majorVersion") or 0)
            except (TypeError, ValueError):
                major = 0
        path = Path(configured)
        try:
            if (
                component
                and path.parent.name.lower() == "bin"
                and path.parent.parent.name.startswith("java-runtime-")
            ):
                runtime_root = path.parent.parent.parent
                exe_name = "javaw.exe" if path.name.lower() == "javaw.exe" else "java.exe"
                candidate = runtime_root / component / "bin" / exe_name
                if candidate.is_file() and candidate.resolve() != path.resolve():
                    self.log_page.log(
                        "info",
                        f"版本要求 Java {major or ''}（{component}），已自动切换运行时：{candidate}",
                    )
                    configured = str(candidate)
                    path = candidate
        except OSError:
            pass

        required = self._required_java_major(
            version_data, str(version_data.get("id", ""))
        )
        if not required or not path.is_file():
            return configured
        install = java_scan.probe_java(path)
        if install is None or not install.major:
            return configured
        if self._java_compatible(required, install.major):
            return configured

        self.log_page.log(
            "warn",
            f"该版本需要 Java {required}，当前为 Java {install.major}，正在查找本机运行时…",
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            installs = java_scan.scan_java(str(self.settings_page.values().get("game_dir", "")) or None)
        finally:
            QApplication.restoreOverrideCursor()
        match = java_scan.find_by_major(installs, required)
        if match is not None:
            self.log_page.log(
                "info", f"已自动切换 Java：{match.path}（Java {match.major}）"
            )
            return match.path

        self.log_page.log("warn", f"没有找到 Java {required}。")
        if self._offer_java_install(
            str(path), version_data, str(version_data.get("id", "")), required
        ):
            return ""
        return configured

    # -------------------------------------------------------------- install
    def start_install(self, version_id: str) -> None:
        if self._install_worker is not None and self._install_worker.isRunning():
            self.log_page.log("warn", "已有安装任务正在进行。")
            return
        if self._launch_worker is not None and self._launch_worker.isRunning():
            self.log_page.log("warn", "游戏启动中，暂时不能同时安装。")
            return

        values = self.settings_page.values()
        game_dir = Path(values["game_dir"])
        try:
            game_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.log_page.log("error", f"无法创建游戏目录：{exc}")
            self.install_page.set_install_status("游戏目录不可写", "error")
            return

        payload = {"mc_dir": str(game_dir), "version_id": version_id}
        self.tabs.setCurrentWidget(self.install_page)
        self.log_page.log("info", "────────────  官方源安装  ────────────")
        self.log_page.log("info", f"版本：{version_id} · 目录：{game_dir}")
        self.log_page.log("event", "仅访问 piston-meta.mojang.com 与 Mojang 官方资源 CDN")

        self._install_reported = False
        self._last_install_percent = -1
        self._install_completed_version = ""
        worker = InstallWorker(self.client, payload, self)
        worker.event.connect(self._on_install_event)
        worker.finished_with.connect(self._on_install_finished)
        worker.failed.connect(self._on_install_failed)
        worker.finished.connect(lambda: self._install_worker_finished(worker))
        self._install_worker = worker
        self.install_page.set_installing(True, version_id)
        worker.start()

    def _on_install_event(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        etype = str(event.get("type", "raw"))
        if etype == "status":
            text = str(event.get("text", ""))
            self.install_page.set_install_status(text, "running")
            self.log_page.log("info", f"[安装] {text}")
        elif etype == "progress":
            done = int(event.get("done", 0))
            total = int(event.get("total", 0))
            current = str(event.get("current", ""))
            self.install_page.set_install_progress(done, total, current)
            percent = int(done * 100 / total) if total > 0 else 0
            if percent >= self._last_install_percent + 10 or (total > 0 and done == total):
                self._last_install_percent = percent
                self.log_page.log("muted", f"[安装] {done}/{total} · {current}")
        elif etype == "log":
            self.log_page.log("info", f"[安装] {event.get('text', '')}")
        elif etype == "error":
            self._install_reported = True
            message = str(event.get("message", "安装失败"))
            self.install_page.set_install_status(message, "error")
            self.log_page.log("error", f"[安装] {message}")
        elif etype == "done":
            self._install_reported = True
            data = event.get("data")
            version = ""
            files = 0
            if isinstance(data, dict):
                version = str(data.get("version", ""))
                files = int(data.get("files", 0) or 0)
            self._install_completed_version = version
            if version:
                self.config.last_version = version
            self.install_page.set_install_status(
                f"安装完成：{version or '版本'}，共 {files} 个文件", "success"
            )
            self.log_page.log("success", f"[安装] 完成 {version}，共 {files} 个文件")
            QTimer.singleShot(600, self.refresh_versions)
        else:
            self.log_page.log("muted", f"[安装] {json.dumps(event, ensure_ascii=False)}")

    def _on_install_finished(self, code: int) -> None:
        self.install_page.set_installing(False)
        if not self._install_reported:
            self._install_reported = True
            if code == 0:
                self.install_page.set_install_status("安装进程结束", "success")
                self.log_page.log("success", "[安装] 内核进程结束（exit 0）")
            else:
                self.install_page.set_install_status(f"安装失败（exit {code}）", "error")
                self.log_page.log("error", f"[安装] 内核进程结束，退出码 {code}")
        if self._install_completed_version:
            self._save_config(silent=True)

    def _on_install_failed(self, message: str) -> None:
        self._install_reported = True
        self.install_page.set_installing(False)
        self.install_page.set_install_status("安装失败", "error")
        self.log_page.log("error", f"[安装] 内核调用失败：{message}")

    def _install_worker_finished(self, worker: InstallWorker) -> None:
        if self._install_worker is worker:
            self._install_worker = None
        worker.deleteLater()

    # --------------------------------------------------------------- account
    def _refresh_account_ui(self) -> None:
        if self.account is None:
            self.account_button.setText("未登录")
            self.account_button.setToolTip("点击使用 Microsoft 账号登录")
        else:
            self.account_button.setText(self.account.name)
            self.account_button.setToolTip(f"{self.account.name}\n{self.account.uuid}")
        self.settings_page.set_account(self.account)
        self.start_page.set_account(self.account)

    def open_account(self) -> None:
        dialog = AccountDialog(
            self, self.config.microsoft_client_id or DEFAULT_MS_CLIENT_ID, self.account
        )
        dialog.logged_in.connect(self._on_account_login)
        dialog.logged_out.connect(self.logout_account)
        self.run_dialog(dialog, "Microsoft 正版登录")

    def _on_account_login(self, account) -> None:
        self.account = account
        try:
            save_account(account)
        except OSError as exc:
            self.log_page.log("warn", f"保存登录信息失败：{exc}")
        self._refresh_account_ui()
        self.log_page.log("success", f"正版登录成功：{account.name}（{account.uuid}）")

    def logout_account(self) -> None:
        clear_account()
        self.account = None
        self._refresh_account_ui()
        self.log_page.log("info", "已退出 Microsoft 正版账号。")

    def _ensure_account(self):
        if self.account is None:
            self.log_page.log("info", "需要完成 Microsoft 正版登录后才能启动。")
            self.open_account()
            return self.account

        if not self.account.expired():
            return self.account

        self.log_page.log("info", f"正版登录即将过期，正在刷新 {self.account.name}…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            account = refresh_account(self.account)
            try:
                save_account(account)
            except OSError as exc:
                self.log_page.log("warn", f"保存刷新后的登录信息失败：{exc}")
            self.account = account
            self._refresh_account_ui()
            self.log_page.log("success", f"正版登录已刷新：{account.name}")
        except AuthNetworkError as exc:
            if self.account is not None and self.account.within_cache(30):
                self.log_page.log(
                    "warn",
                    f"网络不可用，使用本地缓存的正版凭证（30 天内有效）：{exc}",
                )
                self.log_page.log(
                    "muted", "缓存凭证仅用于断网时的单人游戏，联网后会自动重新校验。"
                )
            else:
                self.log_page.log("error", f"网络不可用且缓存已超过 30 天：{exc}")
                clear_account()
                self.account = None
                self._refresh_account_ui()
                self.open_account()
        except AuthError as exc:
            self.log_page.log("error", f"刷新正版登录失败：{exc}")
            clear_account()
            self.account = None
            self._refresh_account_ui()
            self.log_page.log("info", "请重新完成 Microsoft 登录。")
            self.open_account()
        except Exception as exc:
            self.log_page.log("error", f"刷新正版登录失败：{exc}")
        finally:
            QApplication.restoreOverrideCursor()
        return self.account

    # -------------------------------------------------------------- toolbox
    def open_toolbox(self) -> None:
        self.tabs.setCurrentWidget(self.toolbox_page)

    def stop_launch(self) -> None:
        if self._launch_worker is not None and self._launch_worker.isRunning():
            self.log_page.log("warn", "正在终止内核进程…")
            self._launch_worker.stop()

    def _on_core_event(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        etype = str(event.get("type", "raw"))
        if etype == "status":
            self.log_page.log("info", f"[内核] {event.get('text', '')}")
        elif etype == "log":
            self.log_page.log("info", f"[内核] {event.get('text', '')}")
        elif etype == "progress":
            self.log_page.log(
                "event",
                f"[内核] {event.get('done', 0)}/{event.get('total', 0)} {event.get('current', '')}",
            )
        elif etype == "error":
            self._launch_reported = True
            self.log_page.log("error", f"[内核] {event.get('message', '未知错误')}")
        elif etype == "raw":
            # Java inherits mc_core's stdout; show game console output as plain
            # text instead of dumping a JSON wrapper for every line.
            text = str(event.get("text", "")).rstrip()
            if text:
                self.log_page.log("muted", text)
        elif etype == "done":
            self._launch_reported = True
            data = event.get("data")
            if isinstance(data, dict) and "exit_code" in data:
                code = int(data.get("exit_code", 0))
                if code == 0:
                    self.log_page.log("success", "游戏进程已正常退出（exit 0）")
                elif code == -1:
                    self.log_page.log("error", "启动失败（exit -1）：无法创建游戏进程，请检查 Java 路径。")
                else:
                    self.log_page.log("error", f"游戏进程退出码：{code}")
            else:
                self.log_page.log("success", f"内核返回：{json.dumps(data, ensure_ascii=False)}")
        else:
            self.log_page.log("muted", f"[内核] {json.dumps(event, ensure_ascii=False)}")

    def _on_launch_finished(self, code: int) -> None:
        if not self._launch_reported:
            if code == 0:
                self.log_page.log("success", "内核进程结束（exit 0）")
            else:
                self.log_page.log("error", f"内核进程结束，退出码 {code}")
        self._set_running(False)
        if code != 0:
            QTimer.singleShot(600, self._analyze_crash_after_failure)

    def _current_game_dir(self) -> Path | None:
        game_dir = self.settings_page.values().get("game_dir", "")
        if not game_dir:
            return None
        path = Path(game_dir)
        return path if path.is_dir() else None

    def _analyze_crash_after_failure(self) -> None:
        game_dir = self._current_game_dir()
        if game_dir is None:
            return
        analysis = crash_service.analyze_since(game_dir, self._launch_started_at)
        if analysis is None:
            self.log_page.log(
                "muted",
                "没有找到本次启动产生的崩溃报告；可到「百宝箱 → 崩溃诊断」分析 latest.log。",
            )
            return
        self.log_page.log("warn", f"检测到崩溃：{analysis.title}")
        if analysis.suspect_mods:
            self.log_page.log(
                "muted", "可疑 Mod：" + "、".join(analysis.suspect_mods)
            )
        dialog = CrashDialog(self, analysis, game_dir)
        self.run_dialog(dialog, "崩溃诊断")

    def _on_launch_failed(self, message: str) -> None:
        self._launch_reported = True
        self.log_page.log("error", f"内核调用失败：{message}")
        self._set_running(False)
        QTimer.singleShot(600, self._analyze_crash_after_failure)

    def _launch_worker_finished(self, worker: LaunchWorker) -> None:
        if self._launch_worker is worker:
            self._launch_worker = None
        worker.deleteLater()

    def _set_running(self, running: bool) -> None:
        has_version = self.version_list.current_data() is not None
        self.launch_button.setEnabled(not running and has_version)
        self.launch_button.setText("启动中…" if running else "▶   启动游戏")
        self.start_page.set_running(running)
        self.stop_button.setVisible(running)
        self.log_page.set_running(running)

    # --------------------------------------------------------------- theme
    def _on_theme_changed(self, key: str) -> None:
        """Legacy theme signal kept for compatibility with older widgets."""
        style = key if key in ("liquid", "frosted", "html") else "liquid"
        self.settings_page.set_theme_key(style)
        self._apply_appearance()
        self._save_config(silent=True)

    def _on_animation_speed_changed(self, speed: float) -> None:
        self.config.animation_speed = anim.set_speed(speed)
        self._save_config(silent=True)
        self.log_page.log("muted", f"动画速度已调整为 {self.config.animation_speed:.2f}x")

    # ----------------------------------------------------------- appearance
    def _apply_appearance(self) -> None:
        values = self.settings_page.values()
        style = str(values.get("ui_style") or "liquid")
        profiles = values.get("theme_profiles") or {}
        profile = profiles.get(style) if isinstance(profiles, dict) else None
        if not isinstance(profile, dict):
            profile = {}
        base = str(profile.get("base") or ("light" if style in ("frosted", "html") else "dark"))
        primary = str(profile.get("primary") or "#4f8cff")
        accent = str(profile.get("accent") or "#7b5cff")
        glass = bool(profile.get("glass", True))
        system_blur = bool(profile.get("system_blur", True))
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, style, base, primary, accent)
        self.backdrop.set_theme(base)
        apply_window_effects(self, base)
        blur_value = int(profile.get("blur", 26) or 0)
        acrylic_ok = apply_acrylic(
            self,
            system_blur,
            tint=theme_colors_bg(base),
            alpha=min(185, 55 + blur_value) if glass else 45,
        )
        use_system_blur = system_blur and acrylic_ok
        if system_blur and not acrylic_ok:
            apply_acrylic(self, False)
        self.backdrop.apply_settings(
            image_path=str(profile.get("background") or ""),
            glass=glass,
            blur=int(profile.get("blur", 26) or 0),
            dim=int(profile.get("dim", 42) or 0),
            theme_key=base,
            system_blur=use_system_blur,
        )
        for frame in self.findChildren(GlassFrame):
            frame.update()
        self.tabs.tab_bar.update()
        self.tabs.stack.update()
        self.version_list.viewport().update()
        self.log_page.log_view.refresh_theme()
        self.settings_page.refresh_appearance_theme()
        self.update()

    def _on_appearance_changed(self) -> None:
        values = self.settings_page.values()
        path = str(values.get("background_image", ""))
        self._apply_appearance()
        if path != self._last_background_path:
            self._last_background_path = path
            if path:
                self.log_page.log("info", f"启动器背景已更新：{path}")
            else:
                self.log_page.log("info", "已恢复内置氛围背景。")
        # Sliders emit on every pixel; debounce the disk write.
        self._appearance_save_timer.start()

    # -------------------------------------------------------------- config
    def _collect_config(self) -> None:
        for key, value in self.settings_page.values().items():
            setattr(self.config, key, value)
        data = self.version_list.current_data()
        if data:
            self.config.last_version = str(data.get("id", ""))

    def _save_config(self, silent: bool = False) -> None:
        if self._appearance_save_timer.isActive():
            self._appearance_save_timer.stop()
        self._collect_config()
        try:
            save_config(self.config)
        except OSError as exc:
            self.log_page.log("warn", f"配置保存失败：{exc}")
            return
        if not silent:
            self.settings_page.flash_saved()

    def _on_settings_saved(self, values: dict) -> None:
        self._save_config()
        if self.settings_page.memory_adjusted():
            self.log_page.log(
                "warn",
                f"最小内存大于最大内存，已自动将 Xms 调整为 {values.get('min_mem', '')}。",
            )
        self.log_page.log("success", "设置已保存。")
        self.log_page.log("muted", f"游戏目录：{values.get('game_dir', '')}")
        self.toolbox_page.set_game_dir(values.get("game_dir", ""))
        self.instance_page.set_active_dir(values.get("game_dir", ""))
        self.start_page.set_environment(
            str(values.get("java_path", "") or ""),
            f"{values.get('max_mem', '自动')} / {values.get('min_mem', '自动')}",
        )
        self.refresh_versions()

    def _on_developer_unlocked(self) -> None:
        self.log_page.log(
            "success", "开发者模式已解锁（仅本次运行有效，关闭启动器后自动关闭）。"
        )

    def _on_developer_state_changed(self) -> None:
        self.dev_state = self.settings_page.developer_state()
        try:
            dev_state.save_state(self.dev_state)
        except OSError as exc:
            self.log_page.log("warn", f"开发者状态保存失败：{exc}")

    # --------------------------------------------------------------- close
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, lambda: apply_window_effects(self, self.config.theme))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._launch_worker is not None and self._launch_worker.isRunning():
            self._launch_worker.stop()
            self._launch_worker.wait(2000)
        if self._install_worker is not None and self._install_worker.isRunning():
            self._install_worker.stop()
            self._install_worker.wait(2000)
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._scan_worker.wait(1500)
        cancel_tasks(self, timeout=3.0)
        self._save_config(silent=True)
        # Never leave developer mode or offline login enabled on disk.
        self.dev_state = dev_state.reset_state()
        super().closeEvent(event)
