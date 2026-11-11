"""整合包 tab: import ``.mrpack`` files by drop or file picker."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...services import modpack
from ...services.util import human_size
from ..widgets import ShimmerProgressBar
from .common import card, start_task


class ModpackTab(QWidget):
    log_requested = Signal(str, str)
    versions_changed = Signal()
    install_version_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_dir: Path | None = None
        self._pack: modpack.ModpackInfo | None = None
        self._busy = False
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(12)

        pick_card, pick_layout = card(
            "导入整合包（.mrpack）",
            "支持 Modrinth 整合包。文件会被下载到当前游戏目录，overrides 会解压到对应位置。",
        )
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("DropZone")
        self.drop_zone.setMinimumHeight(96)
        drop_layout = QVBoxLayout(self.drop_zone)
        drop_layout.setContentsMargins(18, 14, 18, 14)
        drop_layout.setSpacing(6)
        drop_title = QLabel("把 .mrpack 文件拖到这里")
        drop_title.setObjectName("DropZoneTitle")
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(drop_title)
        drop_hint = QLabel("或点击下方按钮选择文件；整个窗口都支持拖拽")
        drop_hint.setObjectName("Hint")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(drop_hint)
        pick_layout.addWidget(self.drop_zone)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        choose = QPushButton("选择 .mrpack 文件…")
        choose.setObjectName("Primary")
        choose.clicked.connect(self.choose_file)
        button_row.addWidget(choose)
        self.import_button = QPushButton("开始导入")
        self.import_button.setObjectName("Primary")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.start_import)
        button_row.addWidget(self.import_button)
        button_row.addStretch(1)
        pick_layout.addLayout(button_row)
        layout.addWidget(pick_card)

        info_card, info_layout = card("整合包信息")
        self.info_label = QLabel("尚未选择文件")
        self.info_label.setObjectName("Muted")
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(self.info_label)

        options = QHBoxLayout()
        options.setSpacing(14)
        self.overwrite_check = QCheckBox("覆盖已有的配置文件（默认保留本机设置）")
        options.addWidget(self.overwrite_check)
        self.loader_check = QCheckBox("自动安装所需的 Fabric 加载器")
        self.loader_check.setChecked(True)
        options.addWidget(self.loader_check)
        options.addStretch(1)
        info_layout.addLayout(options)

        self.install_base_button = QPushButton("安装对应的原版版本")
        self.install_base_button.setObjectName("Ghost")
        self.install_base_button.hide()
        self.install_base_button.clicked.connect(self._install_base)
        info_layout.addWidget(self.install_base_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(info_card)

        progress_card, progress_layout = card("导入进度")
        self.progress = ShimmerProgressBar()
        self.progress.setObjectName("InstallProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("等待开始")
        progress_layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setObjectName("InstallStatus")
        self.status.setWordWrap(True)
        progress_layout.addWidget(self.status)
        layout.addWidget(progress_card)
        layout.addStretch(1)

    # ----------------------------------------------------------------- file
    def set_game_dir(self, game_dir: str) -> None:
        self.game_dir = Path(game_dir) if game_dir else None
        self._refresh_base_hint()

    def choose_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择 Modrinth 整合包", str(Path.home()), "Modrinth 整合包 (*.mrpack);;所有文件 (*)"
        )
        if chosen:
            self.load_file(chosen)

    def load_file(self, path: str | Path) -> bool:
        if self._busy:
            QMessageBox.information(self, "正在导入", "请等待当前导入完成。")
            return False
        try:
            info = modpack.read_pack(path)
        except modpack.ModpackError as exc:
            QMessageBox.warning(self, "无法读取整合包", str(exc))
            self.log_requested.emit("error", f"整合包读取失败：{exc}")
            return False
        self._pack = info
        loader = f"{info.loader_label} {info.loader_version}".strip()
        self.info_label.setText(
            f"名称：{info.name}"
            + (f"（{info.version}）" if info.version else "")
            + f"\nMinecraft：{info.mc_version} · 加载器：{loader or '原版'}"
            + f"\n文件：{len(info.files)} 个（Mod {info.mod_count} 个），"
            f"共 {human_size(info.total_size)} · overrides {info.override_count} 个文件"
            + (f"\n{info.summary}" if info.summary else "")
            + f"\n来源：{info.archive}"
        )
        self.import_button.setEnabled(True)
        self.progress.setValue(0)
        self.progress.setFormat("等待开始")
        self.status.setText("确认信息后点击“开始导入”。")
        self.status.setProperty("level", "muted")
        self._refresh_base_hint()
        self.log_requested.emit("info", f"已读取整合包：{info.name}（{info.mc_version}）")
        return True

    def _refresh_base_hint(self) -> None:
        if self.game_dir is None or self._pack is None:
            self.install_base_button.hide()
            return
        base = self.game_dir / "versions" / self._pack.mc_version
        if (base / f"{self._pack.mc_version}.jar").is_file():
            self.install_base_button.hide()
        else:
            self.install_base_button.setText(f"先安装原版 {self._pack.mc_version}")
            self.install_base_button.show()

    def _install_base(self) -> None:
        if self._pack is not None:
            self.install_version_requested.emit(self._pack.mc_version)

    # -------------------------------------------------------------- import
    def start_import(self) -> None:
        if self._pack is None or self.game_dir is None:
            return
        if self._busy:
            return
        if not self.game_dir.is_dir():
            QMessageBox.warning(self, "目录不存在", f"游戏目录不存在：{self.game_dir}")
            return
        self._busy = True
        self.import_button.setEnabled(False)
        self.progress.set_shimmer(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setProperty("level", "running")
        self.status.setText("正在导入整合包…")
        self._repolish(self.status)
        pack_path = self._pack.archive
        overwrite = self.overwrite_check.isChecked()
        install_loader = self.loader_check.isChecked()

        def work(progress, cancelled):
            return modpack.import_pack(
                pack_path,
                self.game_dir,
                overwrite_overrides=overwrite,
                install_loader=install_loader,
                progress=lambda message, done, total: progress(message, done, total),
                cancel=cancelled,
            )

        start_task(
            self,
            work,
            on_done=self._on_imported,
            on_failed=self._on_failed,
            on_progress=self._on_progress,
        )

    def _on_progress(self, message: str, done: int, total: int) -> None:
        self.status.setText(message)
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
            self.progress.setFormat(f"{min(done, total)} / {total}")
        else:
            self.progress.setRange(0, 0)

    def _on_imported(self, result: modpack.ImportResult) -> None:
        self._busy = False
        self.import_button.setEnabled(True)
        self.progress.set_shimmer(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("导入完成")
        self.status.setProperty("level", "success" if not result.failed else "warn")
        self.status.setText(
            f"完成：下载 {result.downloaded} 个文件，跳过 {result.skipped_files} 个，"
            f"写入 {result.overrides} 个配置"
            + (f"，失败 {len(result.failed)} 个" if result.failed else "")
        )
        self._repolish(self.status)

        lines = [self.status.text()]
        if result.notes:
            lines.append("")
            lines.extend(result.notes)
        if result.failed:
            lines.append("")
            lines.append("失败项目：")
            lines.extend(result.failed[:30])
        box = QMessageBox(self)
        box.setWindowTitle("整合包导入结果")
        box.setIcon(
            QMessageBox.Icon.Information if not result.failed else QMessageBox.Icon.Warning
        )
        box.setText(f"{result.pack.name} 导入完成")
        box.setInformativeText("\n".join(lines))
        if result.failed:
            box.setDetailedText("\n".join(result.failed))
        run_in_window(self, box, "整合包导入结果")
        self.log_requested.emit(
            "success" if not result.failed else "warn",
            f"整合包导入：{result.pack.name} · 下载 {result.downloaded} 个文件",
        )
        if result.downloaded or result.overrides or result.loader_profile:
            self.versions_changed.emit()
            self._refresh_base_hint()

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.import_button.setEnabled(True)
        self.progress.set_shimmer(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("导入失败")
        self.status.setProperty("level", "error")
        self.status.setText(message)
        self._repolish(self.status)
        QMessageBox.warning(self, "整合包导入失败", message)
        self.log_requested.emit("error", f"整合包导入失败：{message}")

    # ----------------------------------------------------------------- drag
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = self._local_files(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.load_file(paths[0])

    @staticmethod
    def _local_files(mime) -> list[str]:
        if not mime.hasUrls():
            return []
        return [
            url.toLocalFile()
            for url in mime.urls()
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".mrpack")
        ]

    def _accepts(self, mime) -> bool:
        return bool(self._local_files(mime))

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)


class ModpackImportDialog(QDialog):
    """Standalone dialog used when a pack is dropped on the main window."""

    def __init__(
        self,
        parent,
        path: str | Path,
        game_dir: str,
        on_log=None,
        on_versions_changed=None,
        on_install_version=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToolboxDialog")
        self.setWindowTitle("导入 Modrinth 整合包")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        self.tab = ModpackTab(self)
        self.tab.set_game_dir(game_dir)
        if on_log is not None:
            self.tab.log_requested.connect(on_log)
        if on_versions_changed is not None:
            self.tab.versions_changed.connect(on_versions_changed)
        if on_install_version is not None:
            self.tab.install_version_requested.connect(on_install_version)
        layout.addWidget(self.tab)
        self.tab.load_file(path)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("Ghost")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        layout.addLayout(bottom)
