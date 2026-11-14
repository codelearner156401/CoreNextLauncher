"""Real multiplayer page: drives an EasyTier core child process.

No local room file, no simulated players. The room code only seeds the EasyTier
network name/secret; the link state shown here comes from the process itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..services import easytier
from .tasks import start_task

STATE_TEXT = {
    easytier.STATE_STOPPED: ("未连接", "muted"),
    easytier.STATE_STARTING: ("正在启动…", "running"),
    easytier.STATE_CONNECTING: ("正在连接…", "running"),
    easytier.STATE_CONNECTED: ("已连接", "ok"),
    easytier.STATE_ERROR: ("连接失败", "bad"),
}


class _Bridge(QObject):
    """Marshals worker-thread events onto the GUI thread."""

    event = Signal(object)


class MultiplayerPage(QWidget):
    log_requested = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.current_version = ""
        self.game_dir: Path | None = None
        self.config = None
        self._session = easytier.LinkSession()
        self._busy = False
        self._bridge = _Bridge()
        self._bridge.event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self._build()
        self._show_peers()
        self._refresh_state()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 8, 14, 10)
        layout.setSpacing(9)

        title = QLabel("联机")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        hint = QLabel(
            "用 EasyTier 组建临时虚拟局域网：把房间号发给朋友，双方加入同一房间即可在同一局域网里联机。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.admin_notice = QLabel("")
        self.admin_notice.setObjectName("Hint")
        self.admin_notice.setWordWrap(True)
        self.admin_notice.hide()
        layout.addWidget(self.admin_notice)

        self.admin_button = QPushButton("以管理员身份重启启动器")
        self.admin_button.setObjectName("Ghost")
        self.admin_button.clicked.connect(self._relaunch_admin)
        self.admin_button.hide()
        layout.addWidget(self.admin_button)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("房间号"))
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("3-32 位字母/数字，例如 cn-mc-2024")
        self.code_edit.returnPressed.connect(self.create_room)
        row.addWidget(self.code_edit, 1)
        self.create_button = QPushButton("创建房间")
        self.create_button.setObjectName("Primary")
        self.create_button.clicked.connect(self.create_room)
        row.addWidget(self.create_button)
        self.join_button = QPushButton("加入房间")
        self.join_button.setObjectName("Ghost")
        self.join_button.clicked.connect(self.join_room)
        row.addWidget(self.join_button)
        self.leave_button = QPushButton("断开")
        self.leave_button.setObjectName("Ghost")
        self.leave_button.setEnabled(False)
        self.leave_button.clicked.connect(self.leave_room)
        row.addWidget(self.leave_button)
        layout.addLayout(row)

        advanced = QLabel("高级选项")
        advanced.setObjectName("Hint")
        layout.addWidget(advanced)

        self.peer_nodes = QPlainTextEdit()
        self.peer_nodes.setObjectName("MultiplayerPeers")
        self.peer_nodes.setToolTip(
            "EasyTier 的会合/中转节点：每行一个，也可用逗号或分号分隔；"
            "清空后点「保存节点」即恢复内置默认节点。"
        )
        self.peer_nodes.setPlaceholderText("\n".join(easytier.DEFAULT_PEERS))
        self.peer_nodes.setFixedHeight(self.peer_nodes.fontMetrics().lineSpacing() * 3 + 14)
        self.peer_nodes.textChanged.connect(self._update_peer_count)
        layout.addWidget(self.peer_nodes)

        peer_row = QHBoxLayout()
        peer_row.setSpacing(8)
        self.peer_count = QLabel("共享节点：0 个")
        self.peer_count.setObjectName("Hint")
        peer_row.addWidget(self.peer_count)
        peer_row.addStretch(1)
        self.save_peers_button = QPushButton("保存节点")
        self.save_peers_button.setObjectName("Ghost")
        self.save_peers_button.clicked.connect(self.save_peer_nodes)
        peer_row.addWidget(self.save_peers_button)
        self.download_button = QPushButton("下载联机内核")
        self.download_button.setObjectName("Ghost")
        self.download_button.clicked.connect(self.download_core)
        peer_row.addWidget(self.download_button)
        layout.addLayout(peer_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.state_chip = QLabel("未连接")
        self.state_chip.setObjectName("Hint")
        status_row.addWidget(self.state_chip)
        self.tool_status = QLabel("")
        self.tool_status.setObjectName("Hint")
        self.tool_status.setWordWrap(True)
        status_row.addWidget(self.tool_status, 1)
        layout.addLayout(status_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("MultiplayerLog")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(600)
        layout.addWidget(self.log_view, 1)

        self._update_tool_status()

    # ------------------------------------------------------------ status glue
    def _update_tool_status(self) -> None:
        core = easytier.find_core()
        if core is None:
            self.tool_status.setText(
                "联机内核未就绪：点右侧「下载联机内核」，或按提示手动放入 easytier-core.exe。"
            )
            self.download_button.setEnabled(not self._busy)
        else:
            self.tool_status.setText("联机内核：" + str(core))
            self.download_button.setEnabled(False)
        if not easytier.is_admin():
            self.admin_notice.setText(
                "注意：EasyTier 需要管理员权限创建虚拟网卡，当前启动器不是管理员身份，"
                "创建/加入房间会被拒绝。"
            )
            self.admin_notice.show()
            self.admin_button.show()
        else:
            self.admin_notice.hide()
            self.admin_button.hide()

    # ------------------------------------------------------- shared nodes
    def set_config(self, config) -> None:
        """Adopt a live AppConfig; without one the real config file is used."""
        self.config = config
        self._show_peers()

    def _config(self):
        if self.config is not None:
            return self.config
        from ..config import load_config

        return load_config()

    def _stored_peers(self) -> list[str]:
        """Nodes persisted in the config; empty config means the defaults."""
        try:
            config = self._config()
        except Exception:  # noqa: BLE001 - an unreadable config must not block the page
            return list(easytier.DEFAULT_PEERS)
        return easytier.parse_peers(getattr(config, "mp_peer_nodes", "")) or list(
            easytier.DEFAULT_PEERS
        )

    def _active_peers(self) -> list[str]:
        """What the textarea currently says, falling back to the defaults."""
        return easytier.parse_peers(self.peer_nodes.toPlainText()) or list(
            easytier.DEFAULT_PEERS
        )

    def _show_peers(self) -> None:
        self.peer_nodes.setPlainText("\n".join(self._stored_peers()))
        self._update_peer_count()

    def _update_peer_count(self) -> None:
        self.peer_count.setText("共享节点：" + str(len(self._active_peers())) + " 个")

    def save_peer_nodes(self) -> None:
        """Persist the textarea into config mp_peer_nodes (one node per line)."""
        peers = easytier.parse_peers(self.peer_nodes.toPlainText())
        try:
            config = self._config()
            config.mp_peer_nodes = "\n".join(peers)
            self.config = config
            from ..config import save_config

            save_config(config)
        except OSError as exc:
            self._append_log("保存共享节点失败：" + str(exc))
            self._warn("保存失败", "无法写入配置文件：" + str(exc))
            return
        self._show_peers()
        self._append_log("已保存共享节点：" + str(len(self._active_peers())) + " 个")

    def _refresh_state(self) -> None:
        text, level = STATE_TEXT.get(self._session.state, ("未知", "muted"))
        self.state_chip.setText("● " + text)
        self.state_chip.setProperty("state", level)
        style = self.state_chip.style()
        style.unpolish(self.state_chip)
        style.polish(self.state_chip)
        running = self._session.is_running()
        self.leave_button.setEnabled(running)
        self.create_button.setEnabled(not running and not self._busy)
        self.join_button.setEnabled(not running and not self._busy)
        self.code_edit.setEnabled(not running)
        self.peer_nodes.setReadOnly(running)
        self.save_peers_button.setEnabled(not running)

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        self.log_requested.emit("info", "[联机] " + text)

    # ------------------------------------------------------------------ slots
    def create_room(self) -> None:
        self._start_link()

    def join_room(self) -> None:
        self._start_link()

    def _start_link(self) -> None:
        code = self.code_edit.text().strip()
        try:
            easytier.normalise_room(code)
        except easytier.EasyTierError as exc:
            self._warn("房间号无效", str(exc))
            return
        if easytier.find_core() is None:
            self._warn("缺少联机内核", easytier.manual_instructions())
            return
        if not easytier.is_admin():
            self._warn(
                "需要管理员权限",
                "EasyTier 需要创建虚拟网卡，请先以管理员身份重启启动器后重试。",
            )
            self._update_tool_status()
            return
        peers = self._active_peers()
        self._session.stop()
        self._session = easytier.LinkSession(code=code, peer=",".join(peers))
        try:
            self._session.start(self._bridge.event.emit)
        except easytier.EasyTierError as exc:
            self._append_log(str(exc))
            self._warn("无法启动联机", str(exc))
            self._refresh_state()
            return
        name, _secret = easytier.room_to_network(code)
        self._append_log(
            "已启动 EasyTier：房间 " + code + "（网络名 " + name + "），"
            + str(len(peers)) + " 个共享节点，等待对端加入…"
        )
        self._refresh_state()

    def leave_room(self) -> None:
        if self._session.is_running():
            self._append_log("正在断开联机…")
        self._session.stop()
        self._refresh_state()

    def download_core(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.download_button.setEnabled(False)
        self.tool_status.setText("正在从官方 Releases 下载联机内核…")
        self.log_requested.emit("info", "[联机] 开始下载 EasyTier 内核")

        def work(progress, cancel):
            return easytier.ensure_core(
                progress=lambda name, done, total: progress(name, done, total),
                cancel=cancel,
            )

        owner = self.parent() if isinstance(self.parent(), QWidget) else self
        start_task(owner, work, on_done=self._download_done, on_failed=self._download_failed)

    def _download_done(self, core) -> None:
        self._busy = False
        self._append_log("联机内核已就绪：" + str(core))
        self._update_tool_status()
        self._refresh_state()

    def _download_failed(self, message: str) -> None:
        self._busy = False
        self.tool_status.setText("下载失败，可手动放入 easytier-core.exe。")
        self._append_log("下载失败：" + str(message))
        self._update_tool_status()
        self._warn("下载联机内核失败", str(message))

    def _relaunch_admin(self) -> None:
        if easytier.relaunch_as_admin():
            self.log_requested.emit("warn", "[联机] 正在以管理员身份重启启动器…")
            window = self.window()
            if window is not None:
                window.close()
        else:
            self._warn("提权被取消", "未能以管理员身份重启，联机功能将无法使用。")

    # ----------------------------------------------------------------- events
    def _on_event(self, event) -> None:
        kind = getattr(event, "kind", "")
        text = getattr(event, "text", "")
        if kind == "log":
            self.log_view.appendPlainText(text)
        elif kind == "error":
            self.log_view.appendPlainText("! " + text)
            self.log_requested.emit("warn", "[联机] " + text)
            self._warn("联机出现异常", text)
        elif kind == "state":
            self._refresh_state()
            if getattr(event, "state", "") == easytier.STATE_CONNECTED:
                self.log_requested.emit("success", "[联机] 已连接，可以进入游戏联机了")
        elif kind == "exit":
            self._refresh_state()
            self.log_requested.emit("warn", "[联机] " + text)
            self._warn("联机已断开", text)

    def _warn(self, title: str, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(message)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        try:
            from .dialog_page import run_in_window

            run_in_window(self, box, title)
        except Exception:  # noqa: BLE001 - never raise from a dialog
            self.log_requested.emit("warn", title + "：" + message)

    # -------------------------------------------------------- main-window API
    def set_game_dir(self, path) -> None:
        self.game_dir = Path(str(path)) if path else None

    def shutdown(self) -> None:
        self._session.stop()


if __name__ == "__main__":  # pragma: no cover
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    page = MultiplayerPage()
    page.resize(900, 620)
    page.show()
    raise SystemExit(app.exec())
