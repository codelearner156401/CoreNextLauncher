"""可视化 Mod 管理 tab: list, enable/disable, conflict and update checks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
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

from ...services import modrinth, mods as mods_service
from ...services.modrinth import ModUpdate
from ...services.util import human_size
from ..widgets import natural_key
from ..loading_overlay import LoadingOverlay
from .common import card, open_path, start_task

COLUMNS = ("启用", "名称", "版本", "作者", "Mod ID", "加载器", "大小", "文件")


class ModsTab(QWidget):
    log_requested = Signal(str, str)
    versions_changed = Signal()
    detail_requested = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_dir: Path | None = None
        self._mods: list[mods_service.ModFile] = []
        self._updating = False
        self._loaded_once = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(10)

        toolbar_card, toolbar_layout = card(
            "Mod 管理",
            "勾选即可启用；取消勾选会把文件重命名为 .disabled（不会删除）。",
        )

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索 Mod 名称 / ID / 文件名…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        filters.addWidget(self.search, 1)

        filters.addWidget(self._label("游戏版本"))
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(140)
        self.version_combo.setToolTip("用于检查 Mod 更新时的游戏版本")
        filters.addWidget(self.version_combo)

        filters.addWidget(self._label("加载器"))
        self.loader_combo = QComboBox()
        for key, label in (("fabric", "Fabric"), ("forge", "Forge"), ("neoforge", "NeoForge"), ("quilt", "Quilt")):
            self.loader_combo.addItem(label, key)
        filters.addWidget(self.loader_combo)
        toolbar_layout.addLayout(filters)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.refresh_button = QPushButton("刷新列表")
        self.refresh_button.setObjectName("Primary")
        self.refresh_button.clicked.connect(lambda: self.reload())
        buttons.addWidget(self.refresh_button)

        self.batch_enable_button = QPushButton("批量启用")
        self.batch_enable_button.setObjectName("Ghost")
        self.batch_enable_button.setEnabled(False)
        self.batch_enable_button.clicked.connect(lambda: self._batch_set_enabled(True))
        buttons.addWidget(self.batch_enable_button)

        self.batch_disable_button = QPushButton("批量禁用")
        self.batch_disable_button.setObjectName("DangerGhost")
        self.batch_disable_button.setEnabled(False)
        self.batch_disable_button.clicked.connect(lambda: self._batch_set_enabled(False))
        buttons.addWidget(self.batch_disable_button)

        self.select_all_button = QPushButton("全选")
        self.select_all_button.setObjectName("Ghost")
        self.select_all_button.clicked.connect(lambda: self.table.selectAll())
        buttons.addWidget(self.select_all_button)

        self.detail_button = QPushButton("查看详情")
        self.detail_button.setObjectName("Ghost")
        self.detail_button.setEnabled(False)
        self.detail_button.clicked.connect(self._show_detail)
        buttons.addWidget(self.detail_button)

        self.conflict_button = QPushButton("冲突检测")
        self.conflict_button.clicked.connect(self.check_conflicts)
        buttons.addWidget(self.conflict_button)

        self.update_button = QPushButton("检查更新")
        self.update_button.clicked.connect(self.check_updates)
        buttons.addWidget(self.update_button)

        open_button = QPushButton("打开 mods 目录")
        open_button.setObjectName("Ghost")
        open_button.clicked.connect(self.open_mods)
        buttons.addWidget(open_button)
        buttons.addStretch(1)
        toolbar_layout.addLayout(buttons)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.summary = QLabel("尚未加载")
        self.summary.setObjectName("Muted")
        status_row.addWidget(self.summary, 1)
        self.status = QLabel("")
        self.status.setObjectName("InstallStatus")
        status_row.addWidget(self.status)
        toolbar_layout.addLayout(status_row)
        layout.addWidget(toolbar_card)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("ModTable")
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 52)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._update_batch_buttons)
        self.table.itemSelectionChanged.connect(self._update_detail)
        self.table.cellDoubleClicked.connect(self._open_selected_file)
        layout.addWidget(self.table, 1)
        self.detail = QLabel("选中 Mod 可查看作者与简介。")
        self.detail.setObjectName("Hint")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        hint = QLabel(
            "提示：按住 Ctrl / Shift 可以多选，再用“批量启用 / 批量禁用”一次处理多个 Mod。"
            "双击某一行可以在资源管理器中定位该 jar。"
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.loading = LoadingOverlay(self)

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    # ---------------------------------------------------------------- data
    def set_game_dir(self, game_dir: str) -> None:
        changed = str(self.game_dir or "") != str(game_dir or "")
        self.game_dir = Path(game_dir) if game_dir else None
        self._refresh_versions()
        if changed:
            self._mods = []
            self._loaded_once = False
            self.table.setRowCount(0)
            self.summary.setText("尚未加载")
        if self.isVisible() and self.game_dir is not None:
            self.reload()

    def _refresh_versions(self) -> None:
        current = self.version_combo.currentText()
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        if self.game_dir is not None:
            folder = self.game_dir / "versions"
            if folder.is_dir():
                try:
                    ids = []
                    for path in folder.iterdir():
                        manifest = path / f"{path.name}.json"
                        if not manifest.is_file():
                            continue
                        try:
                            raw = json.loads(manifest.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            raw = {}
                        if raw.get("inheritsFrom"):
                            continue
                        ids.append(path.name)
                    ids.sort(key=natural_key, reverse=True)
                except OSError:
                    ids = []
                self.version_combo.addItems(ids)
        if current:
            index = self.version_combo.findText(current)
            if index >= 0:
                self.version_combo.setCurrentIndex(index)
        self.version_combo.blockSignals(False)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.game_dir is not None and not self._loaded_once:
            self.reload()

    def reload(self, silent: bool = False) -> None:
        if self.game_dir is None:
            self.summary.setText("请先在设置页选择 .minecraft 目录")
            return
        if not self.game_dir.is_dir():
            self.summary.setText("游戏目录不存在")
            return
        if not silent:
            self.status.setText("正在读取 Mod…")
        self.loading.show_loading("正在读取 Mod 文件…")
        self.refresh_button.setEnabled(False)

        def work(progress, cancelled):
            progress("正在读取 mods 目录…", 0, 0)
            return mods_service.load_mods(self.game_dir)

        start_task(
            self,
            work,
            on_done=self._on_loaded,
            on_failed=self._on_failed,
        )

    def _on_loaded(self, payload) -> None:
        self.loading.stop()
        mods, folder = payload
        self._mods = mods
        self._loaded_once = True
        self.refresh_button.setEnabled(True)
        self.status.setText("")
        self._render()
        if not folder.is_dir():
            self.summary.setText("没有 mods 目录（安装 Mod 或启动一次游戏后会自动生成）")
        self.log_requested.emit(
            "info" if mods else "muted",
            f"Mod 列表已刷新：{len(mods)} 个文件，启用 {sum(1 for m in mods if m.enabled)} 个",
        )

    def _on_failed(self, message: str) -> None:
        self.loading.stop()
        self.refresh_button.setEnabled(True)
        self.status.setText("读取失败")
        self.status.setProperty("level", "error")
        self._repolish(self.status)
        self.log_requested.emit("error", f"读取 Mod 失败：{message}")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        loading = getattr(self, "loading", None)
        if loading is not None:
            loading.setGeometry(self.rect())

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    def _render(self) -> None:
        self._updating = True
        self.table.setRowCount(0)
        enabled = sum(1 for mod in self._mods if mod.enabled)
        self.summary.setText(
            f"共 {len(self._mods)} 个 Mod，启用 {enabled} 个，禁用 {len(self._mods) - enabled} 个"
        )
        for mod in self._mods:
            row = self.table.rowCount()
            self.table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            check.setCheckState(Qt.CheckState.Checked if mod.enabled else Qt.CheckState.Unchecked)
            check.setToolTip("勾选启用 / 取消勾选禁用")
            self.table.setItem(row, 0, check)

            name_item = QTableWidgetItem(mod.display_name)
            name_item.setToolTip(
                f"作者：{mod.author or '未知'}\n{mod.description or mod.filename}"
            )
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, QTableWidgetItem(mod.display_version))
            self.table.setItem(row, 3, QTableWidgetItem(mod.author or "—"))
            self.table.setItem(row, 4, QTableWidgetItem(mod.mod_id or "—"))
            self.table.setItem(row, 5, QTableWidgetItem(mod.loader or "—"))
            self.table.setItem(row, 6, QTableWidgetItem(human_size(mod.size)))
            file_item = QTableWidgetItem(mod.filename)
            file_item.setToolTip(str(mod.path))
            if mod.error:
                file_item.setToolTip(f"{mod.path}\n{mod.error}")
            self.table.setItem(row, 7, file_item)

            if not mod.enabled:
                brush = QBrush(QColor("#7d8799"))
                for column in range(1, len(COLUMNS)):
                    item = self.table.item(row, column)
                    if item is not None:
                        item.setForeground(brush)
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, str(mod.path))
        self._updating = False
        self._apply_filter(self.search.text())

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        visible = 0
        for row in range(self.table.rowCount()):
            haystack = " ".join(
                self.table.item(row, column).text() if self.table.item(row, column) else ""
                for column in (1, 2, 3, 4, 5, 7)
            ).lower()
            hidden = bool(needle) and needle not in haystack
            self.table.setRowHidden(row, hidden)
            if not hidden:
                visible += 1
        if needle:
            self.summary.setToolTip(f"显示 {visible} / {self.table.rowCount()} 个 Mod")
        else:
            self.summary.setToolTip("")

    # ---------------------------------------------------------- batch toggle
    def _selected_rows(self) -> list[int]:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        return sorted(row for row in rows if not self.table.isRowHidden(row))

    def _update_batch_buttons(self) -> None:
        enabled = bool(self._selected_rows())
        self.batch_enable_button.setEnabled(enabled)
        self.batch_disable_button.setEnabled(enabled)
        if hasattr(self, "detail_button"):
            self.detail_button.setEnabled(enabled)

    def _show_detail(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        row = rows[0]
        file_item = self.table.item(row, 7)
        if file_item is None:
            return
        for mod in self._mods:
            if mod.filename == file_item.text():
                self.detail_requested.emit(mod)
                return

    def _update_detail(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self.detail.setText("选中 Mod 可查看作者与简介。")
            return
        row = rows[0]
        name = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
        version = self.table.item(row, 2).text() if self.table.item(row, 2) else ""
        author = self.table.item(row, 3).text() if self.table.item(row, 3) else ""
        file_item = self.table.item(row, 7)
        description = ""
        if file_item is not None:
            for mod in self._mods:
                if mod.filename == file_item.text():
                    description = mod.description
                    break
        self.detail.setText(
            f"{name} · {version} · 作者：{author or '未知'}\n{description or '暂无简介'}"
        )

    def _batch_set_enabled(self, enabled: bool) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(
                self,
                "未选择 Mod",
                "请先在列表中选中一个或多个 Mod（按住 Ctrl / Shift 多选）。",
            )
            return
        changed = 0
        skipped = 0
        failed: list[str] = []
        for row in rows:
            item = self.table.item(row, 0)
            raw = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if not raw:
                continue
            current = item.checkState() == Qt.CheckState.Checked
            if current == enabled:
                skipped += 1
                continue
            try:
                mods_service.set_mod_enabled(str(raw), enabled)
                changed += 1
            except (OSError, FileExistsError) as exc:
                failed.append(f"{Path(str(raw)).name}：{exc}")
        action = "启用" if enabled else "禁用"
        self.log_requested.emit(
            "success" if not failed else "warn",
            f"批量{action}完成：{changed} 个已{action}"
            + (f"，{skipped} 个无需变更" if skipped else "")
            + (f"，{len(failed)} 个失败" if failed else ""),
        )
        if failed:
            QMessageBox.warning(
                self,
                "部分 Mod 处理失败",
                f"{len(failed)} 个 Mod 无法{action}：\n" + "\n".join(failed[:10]),
            )
        self.reload(silent=True)

    # -------------------------------------------------------------- toggle
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != 0:
            return
        raw = item.data(Qt.ItemDataRole.UserRole)
        if not raw:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        try:
            new_path = mods_service.set_mod_enabled(str(raw), enabled)
        except (OSError, FileExistsError) as exc:
            self._updating = True
            item.setCheckState(
                Qt.CheckState.Unchecked if enabled else Qt.CheckState.Checked
            )
            self._updating = False
            QMessageBox.warning(
                self,
                "无法切换 Mod",
                f"文件正被占用或没有权限：\n{Path(str(raw)).name}\n\n{exc}",
            )
            return

        item.setData(Qt.ItemDataRole.UserRole, str(new_path))
        name = Path(str(raw)).name
        action = "启用" if enabled else "禁用"
        self.log_requested.emit("success", f"已{action} Mod：{name}")
        for mod in self._mods:
            if mod.path == Path(str(raw)):
                mod.path = new_path
                mod.enabled = enabled
                break
        enabled_count = sum(1 for mod in self._mods if mod.enabled)
        self.summary.setText(
            f"共 {len(self._mods)} 个 Mod，启用 {enabled_count} 个，"
            f"禁用 {len(self._mods) - enabled_count} 个"
        )
        # The filename column changes when the suffix is toggled.
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell is not None and cell.data(Qt.ItemDataRole.UserRole) == str(new_path):
                file_item = self.table.item(row, 7)
                if file_item is not None:
                    file_item.setText(new_path.name)
                brush = QBrush(QColor("#7d8799"))
                for column in range(1, len(COLUMNS)):
                    entry = self.table.item(row, column)
                    if entry is None:
                        continue
                    if enabled:
                        entry.setData(Qt.ItemDataRole.ForegroundRole, None)
                    else:
                        entry.setForeground(brush)
                break

    def _open_selected_file(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item is None:
            return
        raw = item.data(Qt.ItemDataRole.UserRole)
        if not raw:
            return
        path = Path(str(raw))
        try:
            subprocess.Popen(["explorer", "/select,", str(path)])
        except OSError:
            open_path(path.parent)

    def open_mods(self) -> None:
        if self.game_dir is None:
            QMessageBox.warning(self, "未设置目录", "请先在设置页选择 .minecraft 目录。")
            return
        open_path(self.game_dir / "mods", create=True)

    # ------------------------------------------------------------ conflicts
    def check_conflicts(self) -> None:
        if not self._mods:
            QMessageBox.information(self, "没有 Mod", "mods 目录中没有找到 jar 文件。")
            return
        report = mods_service.scan_conflicts(self._mods)
        lines: list[str] = [f"扫描 {len(self._mods)} 个 Mod："]
        if report.healthy:
            lines.append("没有发现重复 Mod、重复文件或缺失前置。")
        if report.duplicates:
            lines.append("")
            lines.append(f"重复 Mod ID（{len(report.duplicates)} 个）：")
            for mod_id, names in sorted(report.duplicates.items()):
                lines.append(f"  · {mod_id}：{', '.join(sorted(names))}")
        if report.duplicate_files:
            lines.append("")
            lines.append(f"同名文件（{len(report.duplicate_files)} 组）：")
            for _name, names in sorted(report.duplicate_files.items()):
                lines.append(f"  · {', '.join(sorted(names))}")
        if report.missing:
            lines.append("")
            lines.append(f"缺失前置（{len(report.missing)} 条）：")
            for owner, dep, jar in report.missing[:40]:
                lines.append(f"  · {owner} 需要 {dep}（来自 {jar}）")
            if len(report.missing) > 40:
                lines.append(f"  … 另有 {len(report.missing) - 40} 条")
        if report.disabled_deps:
            lines.append("")
            lines.append(f"前置已安装但被禁用（{len(report.disabled_deps)} 条）：")
            for owner, dep, jar in report.disabled_deps[:20]:
                lines.append(f"  · {owner} 需要 {dep}，但该前置当前处于禁用状态（来自 {jar}）")
        if report.unreadable:
            lines.append("")
            lines.append("无法识别的 jar：")
            for item in report.unreadable[:20]:
                lines.append(f"  · {item}")

        box = QMessageBox(self)
        box.setWindowTitle("Mod 冲突检测")
        box.setIcon(
            QMessageBox.Icon.Warning if not report.healthy else QMessageBox.Icon.Information
        )
        box.setText(
            "发现可修复的冲突"
            if not report.healthy
            else "没有发现 Mod 冲突"
        )
        box.setInformativeText(
            f"已检查 {len(self._mods)} 个 Mod。"
            + (
                f"重复 ID {len(report.duplicates)} 个，同名文件 {len(report.duplicate_files)} 组，"
                f"缺失前置 {len(report.missing)} 条，前置被禁用 {len(report.disabled_deps)} 条。"
                if not report.healthy
                else ""
            )
        )
        box.setDetailedText("\n".join(lines))
        run_in_window(self, box, "Mod 检测结果")
        self.log_requested.emit(
            "warn" if not report.healthy else "success",
            f"Mod 冲突检测：{'发现问题' if not report.healthy else '未发现问题'}（{len(self._mods)} 个 Mod）",
        )

    # -------------------------------------------------------------- updates
    def check_updates(self) -> None:
        if self.game_dir is None:
            QMessageBox.warning(self, "未设置目录", "请先在设置页选择 .minecraft 目录。")
            return
        if not self._mods:
            self.reload(silent=True)
            QMessageBox.information(self, "没有 Mod", "先刷新 Mod 列表后再检查更新。")
            return
        jars = [mod.path for mod in self._mods if mod.enabled]
        if not jars:
            QMessageBox.information(self, "没有启用的 Mod", "所有 Mod 都已禁用，没有可检查的内容。")
            return
        game_version = self.version_combo.currentText().strip()
        loader = str(self.loader_combo.currentData() or "")
        self.update_button.setEnabled(False)
        self.status.setText("正在查询 Modrinth…")
        self.status.setProperty("level", "running")
        self._repolish(self.status)

        def work(progress, cancelled):
            return modrinth.check_updates(
                jars,
                game_version=game_version,
                loader=loader,
                progress=lambda text: progress(text, 0, 0),
            )

        start_task(self, work, on_done=self._on_updates, on_failed=self._on_update_failed)

    def _on_updates(self, updates: list[ModUpdate]) -> None:
        self.update_button.setEnabled(True)
        self.status.setText("")
        if not updates:
            QMessageBox.information(
                self,
                "已是最新",
                "没有发现可用的 Mod 更新（Modrinth 只收录发布在 Modrinth 上的 Mod）。",
            )
            self.log_requested.emit("success", "Mod 更新检测：全部为最新版本")
            return
        self.log_requested.emit("info", f"发现 {len(updates)} 个 Mod 可更新")
        dialog = ModUpdateDialog(self, updates, self.game_dir / "mods")
        run_in_window(self, dialog, "Mod 更新")
        if dialog.updated_count:
            self.log_requested.emit(
                "success", f"已更新 {dialog.updated_count} 个 Mod，重新加载列表"
            )
            self.reload(silent=True)

    def _on_update_failed(self, message: str) -> None:
        self.update_button.setEnabled(True)
        self.status.setText("检查失败")
        self.status.setProperty("level", "error")
        self._repolish(self.status)
        QMessageBox.warning(self, "检查更新失败", message)
        self.log_requested.emit("error", f"Mod 更新检测失败：{message}")


class ModUpdateDialog(QDialog):
    """List of available updates with a batch-update action."""

    def __init__(self, parent, updates: list[ModUpdate], mods_dir: Path) -> None:
        super().__init__(parent)
        self.updates = updates
        self.mods_dir = mods_dir
        self.updated_count = 0
        self._busy = False
        self.setObjectName("ToolboxDialog")
        self.setWindowTitle("Mod 更新")
        self.resize(760, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        title = QLabel(f"发现 {len(updates)} 个可更新的 Mod")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            f"更新会下载到 {mods_dir}，旧文件保留为 .old 后缀（不会被加载）。"
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.table = QTableWidget(len(updates), 5)
        self.table.setObjectName("ModTable")
        self.table.setHorizontalHeaderLabels(("Mod", "当前版本", "最新版本", "大小", "状态"))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for row, update in enumerate(updates):
            self.table.setItem(row, 0, QTableWidgetItem(update.project_title))
            self.table.setItem(row, 1, QTableWidgetItem(update.current_version))
            self.table.setItem(row, 2, QTableWidgetItem(update.latest_version))
            self.table.setItem(row, 3, QTableWidgetItem(human_size(update.size)))
            status = QTableWidgetItem("等待更新")
            status.setToolTip(update.project_url)
            self.table.setItem(row, 4, status)
        self.table.cellDoubleClicked.connect(self._open_project)
        layout.addWidget(self.table, 1)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("Muted")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.update_all_button = QPushButton("全部更新")
        self.update_all_button.setObjectName("Primary")
        self.update_all_button.clicked.connect(self.update_all)
        buttons.addWidget(self.update_all_button)
        close = QPushButton("关闭")
        close.setObjectName("Ghost")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        buttons.addStretch(1)
        hint = QLabel("双击某一行可在浏览器打开 Modrinth 页面")
        hint.setObjectName("Hint")
        buttons.addWidget(hint)
        layout.addLayout(buttons)

    def _open_project(self, row: int, _column: int) -> None:
        if 0 <= row < len(self.updates):
            QDesktopServices.openUrl(QUrl(self.updates[row].project_url))

    def _set_status(self, row: int, text: str, level: str = "") -> None:
        item = self.table.item(row, 4)
        if item is not None:
            item.setText(text)
            if level == "ok":
                item.setForeground(QBrush(QColor("#3ddc97")))
            elif level == "error":
                item.setForeground(QBrush(QColor("#ff7b72")))

    def update_all(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.update_all_button.setEnabled(False)
        self.progress_label.setText("准备下载…")

        def work(progress, cancelled):
            updated: list[int] = []
            failures: list[str] = []
            for index, update in enumerate(self.updates):
                progress(f"正在更新 {update.project_title}…", index, len(self.updates))
                try:
                    modrinth.download_update(update, self.mods_dir)
                    updated.append(index)
                except Exception as exc:
                    failures.append(f"{update.project_title}：{exc}")
            return updated, failures

        start_task(
            self,
            work,
            on_done=self._on_updated,
            on_failed=self._on_update_error,
            on_progress=lambda message, _done, _total: self.progress_label.setText(message),
        )

    def _on_updated(self, payload) -> None:
        updated, failures = payload
        self._busy = False
        self.update_all_button.setEnabled(True)
        self.updated_count = len(updated)
        self.progress_label.setText(
            f"完成：成功 {len(updated)} 个" + (f"，失败 {len(failures)} 个" if failures else "")
        )
        for row in range(self.table.rowCount()):
            if row in updated:
                self._set_status(row, "已更新", "ok")
            elif any(item.startswith(self.updates[row].project_title + "：") for item in failures):
                self._set_status(row, "失败", "error")
        if failures:
            box = QMessageBox(self)
            box.setWindowTitle("部分 Mod 更新失败")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(f"{len(failures)} 个 Mod 更新失败")
            box.setDetailedText("\n".join(failures))
            run_in_window(self, box, "Mod 检测结果")

    def _on_update_error(self, message: str) -> None:
        self._busy = False
        self.update_all_button.setEnabled(True)
        self.progress_label.setText(f"更新失败：{message}")
