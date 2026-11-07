"""快捷目录、缓存清理与手动存档备份 tab."""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...services import backup as backup_service
from ...services import util
from ..widgets import ShimmerProgressBar
from .common import card, human_size, open_path, start_task

LOG_FOLDERS = ("logs", "crash-reports")


class ShortcutsTab(QWidget):
    log_requested = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_dir: Path | None = None
        self._cleanup_files: list[tuple[Path, int, float]] = []
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(12)
        scroll.setWidget(inner)

        folder_card, folder_layout = card(
            "快捷目录", "直接打开 .minecraft 下的常用文件夹；不存在的目录会自动创建。"
        )
        self._rows: dict[str, QLabel] = {}
        for label, relative in (
            ("打开 .minecraft", ""),
            ("打开 mods", "mods"),
            ("打开 logs", "logs"),
            ("打开存档 saves", "saves"),
            ("打开截图 screenshots", "screenshots"),
        ):
            folder_layout.addLayout(self._directory_row(label, relative))
        layout.addWidget(folder_card)

        clean_card, clean_layout = card(
            "一键清理缓存",
            "清理旧日志与崩溃报告不会影响游戏和存档；可以只删除 7 天前的文件。",
        )
        self.clean_old_only = QCheckBox("仅清理 7 天前的文件")
        self.clean_old_only.setChecked(True)
        self.clean_old_only.toggled.connect(self.refresh_stats)
        clean_layout.addWidget(self.clean_old_only)

        self.clean_debug = QCheckBox("同时清理调试日志（logs/debug 与 .gz 压缩日志）")
        self.clean_debug.setChecked(True)
        self.clean_debug.toggled.connect(self.refresh_stats)
        clean_layout.addWidget(self.clean_debug)

        stat_row = QHBoxLayout()
        stat_row.setSpacing(10)
        self.stats = QLabel("尚未扫描")
        self.stats.setObjectName("Muted")
        self.stats.setWordWrap(True)
        stat_row.addWidget(self.stats, 1)
        refresh = QPushButton("刷新统计")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.refresh_stats)
        stat_row.addWidget(refresh)
        self.clean_button = QPushButton("立即清理")
        self.clean_button.setObjectName("Primary")
        self.clean_button.clicked.connect(self.clean)
        stat_row.addWidget(self.clean_button)
        clean_layout.addLayout(stat_row)

        note = QLabel(
            "日志和崩溃报告只是诊断信息；清理后游戏日志会重新生成。"
            "存档、Mod、资源包不会被删除。"
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        clean_layout.addWidget(note)
        layout.addWidget(clean_card)

        layout.addWidget(self._build_backup_card())
        layout.addStretch(1)

    # ------------------------------------------------------------- folders
    def _directory_row(self, label: str, relative: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        text = QLabel(label)
        text.setMinimumWidth(170)
        row.addWidget(text)
        path_label = QLabel("（未设置游戏目录）")
        path_label.setObjectName("Muted")
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(path_label, 1)
        button = QPushButton("打开")
        button.setObjectName("Ghost")
        button.clicked.connect(lambda _=False, rel=relative: self.open_directory(rel))
        row.addWidget(button)
        self._rows[relative] = path_label
        return row

    def set_game_dir(self, game_dir: str) -> None:
        self.game_dir = Path(game_dir) if game_dir else None
        for relative, label in self._rows.items():
            if self.game_dir is None:
                label.setText("（未设置游戏目录）")
            else:
                target = self.game_dir / relative if relative else self.game_dir
                label.setText(str(target))
                label.setToolTip(str(target))
        self.refresh_stats()
        self.refresh_saves()

    def open_directory(self, relative: str) -> None:
        if self.game_dir is None:
            QMessageBox.warning(self, "未设置目录", "请先在设置页选择 .minecraft 目录。")
            return
        target = self.game_dir / relative if relative else self.game_dir
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.log_requested.emit("error", f"无法创建目录：{exc}")
            return
        if open_path(target):
            self.log_requested.emit("info", f"打开目录：{target}")
        else:
            QMessageBox.warning(self, "目录不可用", f"无法打开：{target}")

    # ------------------------------------------------------------- cleanup
    def _collect(self) -> list[tuple[Path, int, float]]:
        if self.game_dir is None:
            return []
        now = time.time()
        old_only = self.clean_old_only.isChecked()
        include_debug = self.clean_debug.isChecked()
        result: list[tuple[Path, int, float]] = []
        seen: set[Path] = set()

        for folder in LOG_FOLDERS:
            root = self.game_dir / folder
            if not root.is_dir():
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    path = Path(dirpath) / name
                    if not include_debug and (
                        "debug" in path.parts
                        or path.suffix.lower() in {".gz", ".log.gz"}
                    ):
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    if old_only and now - stat.st_mtime <= 7 * 86400:
                        continue
                    if path not in seen:
                        seen.add(path)
                        result.append((path, stat.st_size, stat.st_mtime))

        mixin = self.game_dir / ".mixin.out"
        if mixin.is_dir():
            for dirpath, _dirnames, filenames in os.walk(mixin):
                for name in filenames:
                    path = Path(dirpath) / name
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    if path not in seen:
                        seen.add(path)
                        result.append((path, stat.st_size, stat.st_mtime))
        return result

    def refresh_stats(self) -> None:
        self._cleanup_files = self._collect()
        if self.game_dir is None:
            self.stats.setText("尚未设置游戏目录")
            return
        total = sum(size for _path, size, _mtime in self._cleanup_files)
        if not self._cleanup_files:
            self.stats.setText("没有需要清理的日志文件")
        else:
            self.stats.setText(
                f"待清理 {len(self._cleanup_files)} 个文件，共 {human_size(total)}"
            )

    def clean(self) -> None:
        self.refresh_stats()
        if not self._cleanup_files:
            QMessageBox.information(self, "没有可清理的文件", "当前目录下没有符合条件的日志文件。")
            return
        total = sum(size for _path, size, _mtime in self._cleanup_files)
        answer = QMessageBox.question(
            self,
            "确认清理",
            f"将删除 {len(self._cleanup_files)} 个日志/崩溃文件，共 {human_size(total)}。\n"
            "存档、Mod、资源包不受影响。该操作不可撤销，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = 0
        failed = 0
        freed = 0
        for path, size, _mtime in self._cleanup_files:
            try:
                path.unlink()
                removed += 1
                freed += size
            except OSError:
                failed += 1
        self.refresh_stats()
        self.log_requested.emit(
            "success" if failed == 0 else "warn",
            f"缓存清理完成：删除 {removed} 个文件，释放 {util.human_size(freed)}"
            + (f"，{failed} 个失败" if failed else ""),
        )
        QMessageBox.information(
            self,
            "清理完成",
            f"已删除 {removed} 个文件，释放 {util.human_size(freed)}。"
            + (f"\n{failed} 个文件被占用或没有权限，未删除。" if failed else ""),
        )

    # ------------------------------------------------------------- backups
    def _build_backup_card(self) -> QFrame:
        card_frame, layout = card(
            "存档备份",
            "勾选存档后手动打包到 backups/saves；启动游戏前也会自动静默备份变化的存档。",
        )
        self.save_list = QListWidget()
        self.save_list.setObjectName("SaveList")
        self.save_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.save_list.setMaximumHeight(170)
        layout.addWidget(self.save_list)

        row = QHBoxLayout()
        row.setSpacing(8)
        refresh = QPushButton("刷新列表")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.refresh_saves)
        row.addWidget(refresh)
        select_all = QPushButton("全选")
        select_all.setObjectName("Ghost")
        select_all.clicked.connect(self.select_all_saves)
        row.addWidget(select_all)
        backup = QPushButton("备份选中")
        backup.setObjectName("Primary")
        backup.clicked.connect(self.backup_selected)
        row.addWidget(backup)
        open_backups = QPushButton("打开 backups")
        open_backups.setObjectName("Ghost")
        open_backups.clicked.connect(self.open_backups)
        row.addWidget(open_backups)
        row.addStretch(1)
        layout.addLayout(row)

        self.backup_progress = ShimmerProgressBar()
        self.backup_progress.setObjectName("InstallProgress")
        self.backup_progress.setRange(0, 100)
        self.backup_progress.setValue(0)
        self.backup_progress.setFormat("等待开始")
        self.backup_progress.setTextVisible(True)
        self.backup_progress.hide()
        layout.addWidget(self.backup_progress)

        self.backup_status = QLabel("")
        self.backup_status.setObjectName("InstallStatus")
        self.backup_status.setWordWrap(True)
        layout.addWidget(self.backup_status)
        return card_frame

    def refresh_saves(self) -> None:
        self.save_list.clear()
        if self.game_dir is None:
            return
        worlds = backup_service.list_worlds(self.game_dir)
        for world in worlds:
            try:
                size = sum(f.stat().st_size for f in world.rglob("*") if f.is_file())
            except OSError:
                size = 0
            latest = backup_service.latest_backup(world.name, self.game_dir)
            suffix = f" · 上次备份 {util.human_age(latest.stat().st_mtime)}" if latest else " · 尚未备份"
            item = QListWidgetItem(f"{world.name}   ·   {human_size(size)}{suffix}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, world.name)
            item.setToolTip(str(world))
            self.save_list.addItem(item)
        if not worlds:
            item = QListWidgetItem("saves 目录中没有存档（进入一次单人游戏后会生成）")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.save_list.addItem(item)

    def select_all_saves(self) -> None:
        for index in range(self.save_list.count()):
            item = self.save_list.item(index)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked)

    def open_backups(self) -> None:
        if self.game_dir is None:
            QMessageBox.warning(self, "未设置目录", "请先在设置页选择 .minecraft 目录。")
            return
        open_path(self.game_dir / "backups", create=True)

    def backup_selected(self) -> None:
        if self.game_dir is None or self._busy:
            return
        selected: list[str] = []
        for index in range(self.save_list.count()):
            item = self.save_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                name = item.data(Qt.ItemDataRole.UserRole)
                if name:
                    selected.append(str(name))
        if not selected:
            QMessageBox.information(self, "未选择存档", "请先勾选要备份的存档。")
            return
        self._busy = True
        self.backup_progress.show()
        self.backup_progress.set_shimmer(True)
        self.backup_progress.setRange(0, len(selected))
        self.backup_progress.setValue(0)
        self.backup_progress.setFormat(f"0 / {len(selected)}")
        self.backup_status.setText("正在打包存档…")
        self.backup_status.setProperty("level", "running")
        self._repolish(self.backup_status)

        def work(progress, cancelled):
            return backup_service.backup_worlds(
                self.game_dir,
                selected,
                keep=99,
                only_changed=False,
                progress=lambda message, done, total: progress(message, done, total),
                cancel=cancelled,
            )

        start_task(
            self,
            work,
            on_done=self._on_backed_up,
            on_failed=self._on_backup_failed,
            on_progress=lambda message, done, total: self._on_backup_progress(message, done, total),
        )

    def _on_backup_progress(self, message: str, done: int, total: int) -> None:
        self.backup_status.setText(message)
        if total > 0:
            self.backup_progress.setRange(0, total)
            self.backup_progress.setValue(min(done, total))
            self.backup_progress.setFormat(f"{min(done, total)} / {total}")

    def _on_backed_up(self, result: backup_service.BackupResult) -> None:
        self._busy = False
        self.backup_progress.set_shimmer(False)
        self.backup_progress.setRange(0, 100)
        self.backup_progress.setValue(100)
        self.backup_progress.setFormat("备份完成")
        self.backup_status.setProperty("level", "success" if not result.failed else "warn")
        self.backup_status.setText(backup_service.describe(result))
        self._repolish(self.backup_status)
        self.log_requested.emit(
            "success" if not result.failed else "warn",
            "手动存档备份：" + backup_service.describe(result),
        )
        QMessageBox.information(
            self,
            "备份完成",
            backup_service.describe(result)
            + (
                "\n\n文件：\n" + "\n".join(path.name for path in result.created)
                if result.created
                else ""
            ),
        )
        self.refresh_saves()

    def _on_backup_failed(self, message: str) -> None:
        self._busy = False
        self.backup_progress.set_shimmer(False)
        self.backup_progress.hide()
        self.backup_status.setProperty("level", "error")
        self.backup_status.setText(f"备份失败：{message}")
        self._repolish(self.backup_status)
        QMessageBox.warning(self, "备份失败", message)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
