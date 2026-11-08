"""实例管理页：新建、克隆、切换与删除 ``instances/`` 下的游戏实例。"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..services import instances
from ..services.util import human_age
from .tasks import start_task
from .widgets import HoverCard, ShimmerProgressBar
from .toolbox.common import open_path


def _card(title: str, subtitle: str = ""):
    frame = HoverCard()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 15, 18, 15)
    layout.setSpacing(10)
    heading = QLabel(title)
    heading.setObjectName("SectionTitle")
    layout.addWidget(heading)
    if subtitle:
        note = QLabel(subtitle)
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
    return frame, layout


class InstancePage(QWidget):
    log_requested = Signal(str, str)
    instance_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.active_dir = ""
        self._instances: list[instances.InstanceInfo] = []
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 8, 14, 10)
        layout.setSpacing(12)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("实例管理")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        subtitle = QLabel(
            "每个实例都是独立的游戏目录，Mod、存档与配置互不影响。"
            f"实例统一存放在 {instances.root_dir()}。"
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        header.addWidget(subtitle)
        layout.addLayout(header)

        list_card, list_layout = _card(
            "实例列表", "克隆会复制版本、Mod 与配置，默认不复制日志、崩溃报告与旧备份。"
        )
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("InstanceTable")
        self.table.setHorizontalHeaderLabels(("实例名称", "版本", "最后修改", "状态"))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda _row, _col: self.activate_selected())
        list_layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.new_button = QPushButton("新建实例")
        self.new_button.setObjectName("Primary")
        self.new_button.clicked.connect(self.new_instance)
        buttons.addWidget(self.new_button)
        self.clone_button = QPushButton("克隆选中")
        self.clone_button.setObjectName("Primary")
        self.clone_button.setEnabled(False)
        self.clone_button.clicked.connect(self.clone_selected)
        buttons.addWidget(self.clone_button)
        self.activate_button = QPushButton("设为当前")
        self.activate_button.setEnabled(False)
        self.activate_button.clicked.connect(self.activate_selected)
        buttons.addWidget(self.activate_button)
        self.open_button = QPushButton("打开目录")
        self.open_button.setObjectName("Ghost")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected)
        buttons.addWidget(self.open_button)
        self.delete_button = QPushButton("删除选中")
        self.delete_button.setObjectName("DangerGhost")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_selected)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("Ghost")
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_button)
        list_layout.addLayout(buttons)

        self.progress = ShimmerProgressBar()
        self.progress.setObjectName("InstallProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("等待操作")
        self.progress.hide()
        list_layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setObjectName("InstallStatus")
        self.status.setWordWrap(True)
        list_layout.addWidget(self.status)
        layout.addWidget(list_card, 1)

        info_card, info_layout = _card(
            "工作区", "启动器会把配置、内核、实例、缓存和导出统一放在文档目录下。"
        )
        self.root_label = QLabel(str(instances.root_dir()))
        self.root_label.setObjectName("Muted")
        self.root_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.root_label.setWordWrap(True)
        info_layout.addWidget(self.root_label)
        root_row = QHBoxLayout()
        root_row.setSpacing(8)
        open_root = QPushButton("打开实例根目录")
        open_root.setObjectName("Ghost")
        open_root.clicked.connect(self.open_root)
        root_row.addWidget(open_root)
        root_row.addStretch(1)
        info_layout.addLayout(root_row)
        layout.addWidget(info_card)

    # ---------------------------------------------------------------- state
    def set_active_dir(self, path: str) -> None:
        self.active_dir = str(path or "")
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        self._instances = instances.list_instances()
        self.table.setRowCount(0)
        active = Path(self.active_dir).resolve() if self.active_dir else None
        for info in self._instances:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(info.name))
            self.table.setItem(row, 1, QTableWidgetItem(info.display_versions))
            modified = human_age(info.modified, time.time()) if info.modified else "—"
            self.table.setItem(row, 2, QTableWidgetItem(modified))
            try:
                is_active = active is not None and info.path.resolve() == active
            except OSError:
                is_active = False
            status = QTableWidgetItem("当前实例" if is_active else "—")
            if is_active:
                status.setForeground(QBrush(QColor("#3ddc97")))
            self.table.setItem(row, 3, status)
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, str(info.path))
        self._update_buttons()

    def _selected(self) -> instances.InstanceInfo | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._instances):
            return self._instances[row]
        return None

    def _update_buttons(self) -> None:
        has = self._selected() is not None
        for button in (
            self.clone_button,
            self.activate_button,
            self.open_button,
            self.delete_button,
        ):
            button.setEnabled(has and not self._busy)

    # -------------------------------------------------------------- actions
    def new_instance(self) -> None:
        name, ok = QInputDialog.getText(self, "新建实例", "实例名称：")
        if not ok:
            return
        try:
            path = instances.create_instance(name)
        except instances.InstanceError as exc:
            QMessageBox.warning(self, "无法创建实例", str(exc))
            return
        self.log_requested.emit("success", f"已创建实例：{path.name}")
        self.refresh()
        self._ask_activate(path, f"实例“{path.name}”创建成功，是否设为当前实例？")

    def clone_selected(self) -> None:
        source = self._selected()
        if source is None or self._busy:
            return
        dialog = CloneInstanceDialog(self, source.name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, include_saves = dialog.values()
        self._busy = True
        self._update_buttons()
        self.refresh_button.setEnabled(False)
        self.progress.show()
        self.progress.set_shimmer(True)
        self.progress.setRange(0, 0)
        self.status.setProperty("level", "running")
        self.status.setText(f"正在克隆 {source.name}…")
        self._repolish()

        def work(progress, cancelled):
            return instances.clone_instance(
                source.path,
                name,
                include_saves=include_saves,
                progress=lambda message, done, total: progress(message, done, total),
                cancel=cancelled,
            )

        start_task(
            self,
            work,
            on_done=self._on_cloned,
            on_failed=self._on_clone_failed,
            on_progress=self._on_clone_progress,
        )

    def _on_clone_progress(self, message: str, done: int, total: int) -> None:
        self.status.setText(message)
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
            self.progress.setFormat(f"{min(done, total)} / {total}")

    def _on_cloned(self, path: Path) -> None:
        self._finish_busy()
        self.progress.setShimmer(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("克隆完成")
        self.status.setProperty("level", "success")
        self.status.setText(f"已克隆到 {path}")
        self._repolish()
        self.log_requested.emit("success", f"实例已克隆：{path.name}")
        self.refresh()
        self._ask_activate(path, f"实例“{path.name}”克隆完成，是否设为当前实例？")

    def _on_clone_failed(self, message: str) -> None:
        self._finish_busy()
        self.progress.setShimmer(False)
        self.progress.hide()
        self.status.setProperty("level", "error")
        self.status.setText(f"克隆失败：{message}")
        self._repolish()
        self.log_requested.emit("error", f"实例克隆失败：{message}")
        QMessageBox.warning(self, "克隆失败", message)

    def _finish_busy(self) -> None:
        self._busy = False
        self.refresh_button.setEnabled(True)
        self._update_buttons()

    def activate_selected(self) -> None:
        info = self._selected()
        if info is not None:
            self.activate(info.path)

    def activate(self, path: Path) -> None:
        self.instance_changed.emit(str(path))
        self.active_dir = str(path)
        self.refresh()

    def open_selected(self) -> None:
        info = self._selected()
        if info is not None:
            open_path(info.path)

    def open_root(self) -> None:
        open_path(instances.root_dir(), create=True)

    def delete_selected(self) -> None:
        info = self._selected()
        if info is None:
            return
        try:
            is_active = bool(self.active_dir) and info.path.resolve() == Path(self.active_dir).resolve()
        except OSError:
            is_active = False
        if is_active:
            QMessageBox.warning(self, "无法删除", "这是当前实例，请先切换到其他实例再删除。")
            return
        answer = QMessageBox.question(
            self,
            "删除实例",
            f"确定删除实例“{info.name}”吗？\n"
            "该实例中的版本、Mod 与存档都会被永久删除，操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            instances.delete_instance(info.name)
        except instances.InstanceError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self.log_requested.emit("info", f"已删除实例：{info.name}")
        self.refresh()

    def _ask_activate(self, path: Path, text: str) -> None:
        answer = QMessageBox.question(
            self,
            "切换实例",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.activate(path)

    def _repolish(self) -> None:
        style = self.status.style()
        style.unpolish(self.status)
        style.polish(self.status)


class CloneInstanceDialog(QDialog):
    def __init__(self, parent, source_name: str) -> None:
        super().__init__(parent)
        self.setObjectName("ToolboxDialog")
        self.setWindowTitle("克隆实例")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        title = QLabel("克隆实例")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel(
            f"将复制“{source_name}”的版本、Mod 与配置。"
            "日志、崩溃报告与旧存档备份不会被复制。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.name_edit = QLineEdit(f"{source_name}-副本")
        self.name_edit.setPlaceholderText("新实例名称")
        layout.addWidget(self.name_edit)
        self.saves_check = QCheckBox("同时复制 saves 存档（体积较大）")
        self.saves_check.setChecked(True)
        layout.addWidget(self.saves_check)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton("开始克隆")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._accept)
        row.addWidget(ok)
        layout.addLayout(row)

    def _accept(self) -> None:
        try:
            instances.sanitize_name(self.name_edit.text())
        except instances.InstanceError as exc:
            QMessageBox.warning(self, "名称不可用", str(exc))
            return
        self.accept()

    def values(self) -> tuple[str, bool]:
        return self.name_edit.text().strip(), self.saves_check.isChecked()
