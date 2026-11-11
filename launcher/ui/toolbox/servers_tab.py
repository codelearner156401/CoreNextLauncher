"""服务器 tab: ping monitor, bookmarks and one-click join."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...services import servers
from ..widgets import natural_key
from .common import card, start_task

COLUMNS = ("状态", "名称", "地址", "玩家", "延迟", "版本", "MOTD")
_COLOR_CODES = re.compile(r"§[0-9a-fk-or]")


def _strip_codes(text: str) -> str:
    return _COLOR_CODES.sub("", text or "").strip()


class ServersTab(QWidget):
    log_requested = Signal(str, str)
    join_requested = Signal(object)
    versions_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_dir: Path | None = None
        self.entries: list[servers.ServerEntry] = []
        self.statuses: dict[str, servers.ServerStatus] = {}
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(10)

        toolbar_card, toolbar_layout = card(
            "服务器监控",
            "添加常用服务器后可以实时查看延迟、在线人数和 MOTD，并一键带参进服。",
        )
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        add = QPushButton("添加服务器")
        add.setObjectName("Primary")
        add.clicked.connect(self.add_server)
        buttons.addWidget(add)
        self.refresh_button = QPushButton("刷新全部")
        self.refresh_button.clicked.connect(self.refresh_all)
        buttons.addWidget(self.refresh_button)
        remove = QPushButton("删除选中")
        remove.setObjectName("DangerGhost")
        remove.clicked.connect(self.remove_selected)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        self.join_button = QPushButton("一键进服")
        self.join_button.setObjectName("Primary")
        self.join_button.clicked.connect(self.join_selected)
        buttons.addWidget(self.join_button)
        toolbar_layout.addLayout(buttons)
        layout.addWidget(toolbar_card, 0)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("ServerTable")
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 54)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for column in (3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_details)
        self.table.cellDoubleClicked.connect(lambda _row, _col: self.join_selected())
        layout.addWidget(self.table, 1)

        self.details = QLabel("选择一行查看服务器详情；双击可以直接进服。")
        self.details.setObjectName("Hint")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)

        self.status = QLabel("")
        self.status.setObjectName("InstallStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._load()

    # --------------------------------------------------------------- store
    def _load(self) -> None:
        self.entries = servers.load_servers()
        self._render()
        if self.entries and self.isVisible():
            self.refresh_all()

    def _save(self) -> None:
        try:
            servers.save_servers(self.entries)
        except OSError as exc:
            self.log_requested.emit("error", f"保存服务器列表失败：{exc}")

    def _render(self) -> None:
        self.table.setRowCount(0)
        for entry in self.entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            status = self.statuses.get(entry.target)
            dot = "●"
            if status is None:
                dot, colour = "○", "#7d8799"
            elif status.online:
                dot, colour = "●", "#3ddc97" if status.latency_ms < 120 else "#f5c451"
            else:
                dot, colour = "●", "#ff7b72"
            status_item = QTableWidgetItem(dot)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(QBrush(QColor(colour)))
            self.table.setItem(row, 0, status_item)
            self.table.setItem(row, 1, QTableWidgetItem(entry.name))
            address_item = QTableWidgetItem(entry.target)
            address_item.setToolTip(entry.note or entry.target)
            self.table.setItem(row, 2, address_item)
            if status is not None and status.online:
                players = f"{status.players_online}/{status.players_max}"
                latency = f"{status.latency_ms} ms"
                version = status.version_name or "—"
                motd = _strip_codes(status.motd) or "—"
            elif status is not None:
                players, latency, version, motd = "—", "—", "—", status.error or "离线"
            else:
                players, latency, version, motd = "—", "—", "—", "点击“刷新全部”检测"
            self.table.setItem(row, 3, QTableWidgetItem(players))
            self.table.setItem(row, 4, QTableWidgetItem(latency))
            self.table.setItem(row, 5, QTableWidgetItem(version))
            motd_item = QTableWidgetItem(motd)
            if status is not None and status.online and status.motd:
                motd_item.setToolTip(_strip_codes(status.motd))
            self.table.setItem(row, 6, motd_item)

    def set_game_dir(self, game_dir: str) -> None:
        self.game_dir = Path(game_dir) if game_dir else None

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.entries and not self.statuses and not self._busy:
            self.refresh_all()

    # -------------------------------------------------------------- actions
    def add_server(self) -> None:
        versions: list[str] = []
        if self.game_dir is not None:
            folder = self.game_dir / "versions"
            if folder.is_dir():
                try:
                    versions = sorted(
                        [p.name for p in folder.iterdir() if (p / f"{p.name}.json").is_file()],
                        key=natural_key,
                        reverse=True,
                    )
                except OSError:
                    versions = []
        dialog = AddServerDialog(self, versions)
        if run_in_window(self, dialog, "服务器配置") != QDialog.DialogCode.Accepted:
            return
        entry = dialog.entry()
        if not entry.address:
            return
        if any(item.target == entry.target for item in self.entries):
            QMessageBox.information(self, "已存在", f"服务器 {entry.target} 已经在列表里。")
            return
        self.entries.append(entry)
        self._save()
        self._render()
        self.statuses.pop(entry.target, None)
        self.log_requested.emit("success", f"已添加服务器：{entry.name}（{entry.target}）")
        self.refresh_all()

    def remove_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.entries):
            return
        entry = self.entries[row]
        answer = QMessageBox.question(
            self,
            "删除服务器",
            f"从列表里删除 {entry.name}（{entry.target}）？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.entries.pop(row)
        self.statuses.pop(entry.target, None)
        self._save()
        self._render()
        self.log_requested.emit("info", f"已删除服务器：{entry.name}")

    def refresh_all(self) -> None:
        if self._busy:
            return
        if not self.entries:
            self.status.setText("还没有添加服务器。")
            return
        self._busy = True
        self.refresh_button.setEnabled(False)
        self.status.setProperty("level", "running")
        self.status.setText("正在 Ping 所有服务器…")
        self._repolish(self.status)
        targets = list(self.entries)

        def work(progress, cancelled):
            progress("正在 Ping 服务器…", 0, len(targets))
            return servers.ping_many(targets, timeout=3.5, workers=6)

        start_task(self, work, on_done=self._on_pinged, on_failed=self._on_ping_failed)

    def _on_pinged(self, statuses: list[servers.ServerStatus]) -> None:
        self._busy = False
        self.refresh_button.setEnabled(True)
        online = 0
        for status in statuses:
            self.statuses[status.entry.target] = status
            if status.online:
                online += 1
        self._render()
        self.status.setProperty("level", "success" if online else "warn")
        self.status.setText(f"Ping 完成：{online}/{len(statuses)} 台服务器在线")
        self._repolish(self.status)
        self._update_details()
        self.log_requested.emit(
            "info" if online else "warn",
            f"服务器状态：{online}/{len(statuses)} 在线",
        )

    def _on_ping_failed(self, message: str) -> None:
        self._busy = False
        self.refresh_button.setEnabled(True)
        self.status.setProperty("level", "error")
        self.status.setText(f"Ping 失败：{message}")
        self._repolish(self.status)

    def _update_details(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.entries):
            self.details.setText("选择一行查看服务器详情；双击可以直接进服。")
            return
        entry = self.entries[row]
        status = self.statuses.get(entry.target)
        if status is None:
            self.details.setText(f"{entry.name} · {entry.target} · 尚未检测")
            return
        if not status.online:
            self.details.setText(f"{entry.name} · {entry.target} · 离线：{status.error}")
            return
        sample = "、".join(status.sample) if status.sample else "—"
        self.details.setText(
            f"{entry.name} · {entry.target} · {status.version_name} · "
            f"{status.players_online}/{status.players_max} 在线 · {status.latency_ms} ms\n"
            f"MOTD：{_strip_codes(status.motd) or '—'}\n在线玩家：{sample}"
        )

    def join_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.entries):
            QMessageBox.information(self, "未选择服务器", "请先在列表中选中一个服务器。")
            return
        entry = self.entries[row]
        status = self.statuses.get(entry.target)
        if status is not None and not status.online:
            answer = QMessageBox.question(
                self,
                "服务器离线",
                f"{entry.target} 现在无法连接（{status.error}），仍然尝试进服吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.join_requested.emit(entry)

    def _repolish(self, widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)


class AddServerDialog(QDialog):
    def __init__(self, parent, versions: list[str]) -> None:
        super().__init__(parent)
        self.setObjectName("ToolboxDialog")
        self.setWindowTitle("添加服务器")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        title = QLabel("添加服务器")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        hint = QLabel("支持 域名:端口、IP 或带端口的地址；不填端口时使用 25565。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：朋友的生存服")
        form.addRow("名称", self.name_edit)
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("play.example.com 或 1.2.3.4:25565")
        form.addRow("地址", self.address_edit)
        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("默认 25565")
        form.addRow("端口", self.port_edit)
        self.version_combo = QComboBox()
        self.version_combo.addItem("（启动时使用当前选中版本）", "")
        for version in versions:
            self.version_combo.addItem(version, version)
        form.addRow("启动版本", self.version_combo)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("添加")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._accept)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def _accept(self) -> None:
        if not self.address_edit.text().strip():
            QMessageBox.warning(self, "缺少地址", "请填写服务器地址。")
            return
        self.accept()

    def entry(self) -> servers.ServerEntry:
        address_text = self.address_edit.text().strip()
        host, parsed_port = servers.split_address(address_text)
        port_text = self.port_edit.text().strip()
        port = int(port_text) if port_text.isdigit() else parsed_port
        port = max(1, min(65535, int(port)))
        return servers.ServerEntry(
            name=self.name_edit.text().strip() or host,
            address=host,
            port=port,
            version_id=str(self.version_combo.currentData() or ""),
        )
