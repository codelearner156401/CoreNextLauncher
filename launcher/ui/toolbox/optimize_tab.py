"""优化 tab: Java auto-management and one-click Fabric + Sodium + Iris setup."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...services import fabric_install, java_install, java_scan
from ..widgets import ShimmerProgressBar, natural_key
from .common import card, human_size, start_task
from ..dialog_page import run_in_window

JAVA_MAJORS = (21, 17, 25, 8)


class OptimizeTab(QWidget):
    log_requested = Signal(str, str)
    java_selected = Signal(str)
    versions_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_dir: Path | None = None
        self._java: list[java_scan.JavaInstall] = []
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(12)

        # ------------------------------------------------------------- Java
        java_card, java_layout = card(
            "Java 自动托管",
            "自动扫描本机所有 Java；缺失时可以从 Adoptium 官方下载 Temurin 并自动配置。",
        )
        self.java_list = QListWidget()
        self.java_list.setObjectName("SaveList")
        self.java_list.setMaximumHeight(150)
        java_layout.addWidget(self.java_list)

        java_buttons = QHBoxLayout()
        java_buttons.setSpacing(8)
        self.scan_button = QPushButton("重新扫描")
        self.scan_button.clicked.connect(lambda: self.scan_java())
        java_buttons.addWidget(self.scan_button)
        self.use_button = QPushButton("设为启动 Java")
        self.use_button.setObjectName("Primary")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self.use_selected_java)
        java_buttons.addWidget(self.use_button)
        self.install_java_button = QPushButton("下载安装 Java…")
        java_buttons.addWidget(self.install_java_button)
        self.install_java_button.clicked.connect(self.install_java)
        java_buttons.addStretch(1)
        java_layout.addLayout(java_buttons)

        self.java_status = QLabel("尚未扫描")
        self.java_status.setObjectName("Muted")
        self.java_status.setWordWrap(True)
        java_layout.addWidget(self.java_status)
        self.java_list.currentRowChanged.connect(
            lambda row: self.use_button.setEnabled(0 <= row < len(self._java))
        )
        layout.addWidget(java_card)

        # ------------------------------------------------------- optimization
        opt_card, opt_layout = card(
            "一键智能优化",
            "安装 Fabric 加载器，并从 Modrinth 下载 Sodium（渲染优化）、Iris（光影）与 Fabric API。",
        )
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._field("游戏版本"))
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(180)
        row.addWidget(self.version_combo)
        self.fabric_api_check = QCheckBox("同时安装 Fabric API（推荐）")
        self.fabric_api_check.setChecked(True)
        row.addWidget(self.fabric_api_check)
        row.addStretch(1)
        self.optimize_button = QPushButton("一键安装优化 Mod")
        self.optimize_button.setObjectName("Primary")
        self.optimize_button.clicked.connect(self.optimize)
        row.addWidget(self.optimize_button)
        opt_layout.addLayout(row)

        self.opt_progress = ShimmerProgressBar()
        self.opt_progress.setObjectName("InstallProgress")
        self.opt_progress.setRange(0, 100)
        self.opt_progress.setValue(0)
        self.opt_progress.setTextVisible(True)
        self.opt_progress.setFormat("等待开始")
        opt_layout.addWidget(self.opt_progress)

        self.opt_status = QLabel("选择游戏版本后点击“一键安装优化 Mod”。")
        self.opt_status.setObjectName("InstallStatus")
        self.opt_status.setWordWrap(True)
        opt_layout.addWidget(self.opt_status)

        note = QLabel(
            "优化目标：Fabric 加载器 + Sodium（大幅提升帧率）+ Iris（光影支持）。"
            "安装完成后，版本库会多出一个 fabric-loader-… 版本，请用它启动游戏。"
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        opt_layout.addWidget(note)
        layout.addWidget(opt_card)
        layout.addStretch(1)

    @staticmethod
    def _field(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    # ---------------------------------------------------------------- state
    def set_game_dir(self, game_dir: str) -> None:
        changed = str(self.game_dir or "") != str(game_dir or "")
        self.game_dir = Path(game_dir) if game_dir else None
        self._refresh_versions()
        if changed:
            self._java = []
            self.java_list.clear()
            self.java_status.setText("尚未扫描")
        if self.isVisible():
            self.scan_java(silent=True)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._java:
            self.scan_java(silent=True)

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

    # ----------------------------------------------------------------- Java
    def scan_java(self, silent: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self.scan_button.setEnabled(False)
        self.java_status.setText("正在扫描本机 Java…")
        game_dir = self.game_dir

        def work(progress, cancelled):
            return java_scan.scan_java(
                game_dir,
                on_progress=lambda done, total, text: progress(text, done, total),
            )

        start_task(self, work, on_done=self._on_java, on_failed=self._on_java_failed)

    def _on_java(self, installs: list[java_scan.JavaInstall]) -> None:
        self._busy = False
        self.scan_button.setEnabled(True)
        self._java = installs
        self.java_list.clear()
        for install in installs:
            self.java_list.addItem(install.label + "\n    " + install.path)
        if not installs:
            self.java_status.setText(
                "没有找到可用的 Java。点击“下载安装 Java…”自动安装 Temurin。"
            )
            self.install_java_button.setEnabled(True)
            return
        best = java_scan.best_install(installs)
        self.java_status.setText(
            f"找到 {len(installs)} 个 Java"
            + (f"，推荐使用 {best.label}" if best is not None else "")
        )
        if best is not None:
            for row, install in enumerate(installs):
                if install.path == best.path:
                    self.java_list.setCurrentRow(row)
                    break
        self.log_requested.emit("info", f"Java 扫描完成：找到 {len(installs)} 个运行时")

    def _on_java_failed(self, message: str) -> None:
        self._busy = False
        self.scan_button.setEnabled(True)
        self.java_status.setText(f"扫描失败：{message}")
        self.log_requested.emit("error", f"Java 扫描失败：{message}")

    def use_selected_java(self) -> None:
        row = self.java_list.currentRow()
        if not (0 <= row < len(self._java)):
            return
        install = self._java[row]
        self.java_selected.emit(install.path)
        self.log_requested.emit("success", f"已选择启动 Java：{install.path}")

    def install_java(self) -> None:
        if self._busy:
            return
        from PySide6.QtWidgets import QInputDialog

        labels = [f"Java {major}" for major in JAVA_MAJORS]
        label, ok = QInputDialog.getItem(
            self,
            "下载安装 Java",
            "选择要安装的 Java 版本（推荐 Java 21）：",
            labels,
            0,
            False,
        )
        if not ok:
            return
        try:
            major = JAVA_MAJORS[labels.index(label)]
        except ValueError:
            major = 21
        answer = QMessageBox.question(
            self,
            "从 Adoptium 下载 Java",
            f"将从 Adoptium 官方源下载 Temurin {major}（约 180 MB），"
            "解压到启动器数据目录并自动填入设置。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._busy = True
        self.install_java_button.setEnabled(False)
        self.opt_progress.set_shimmer(True)
        self.opt_progress.setRange(0, 0)
        self.java_status.setText(f"正在安装 Java {major}…")

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
            on_progress=self._on_java_progress,
        )

    def _on_java_progress(self, message: str, done: int, total: int) -> None:
        self.java_status.setText(message)
        if total > 0:
            self.opt_progress.setRange(0, total)
            self.opt_progress.setValue(min(done, total))
            self.opt_progress.setFormat(
                f"{human_size(done)} / {human_size(total)}"
            )
        else:
            self.opt_progress.setRange(0, 0)

    def _on_java_installed(self, java_exe: Path) -> None:
        self._busy = False
        self.install_java_button.setEnabled(True)
        self.opt_progress.set_shimmer(False)
        self.opt_progress.setRange(0, 100)
        self.opt_progress.setValue(100)
        self.opt_progress.setFormat("Java 安装完成")
        self.java_selected.emit(str(java_exe))
        self.log_requested.emit("success", f"Java 已安装并设为启动运行时：{java_exe}")
        QMessageBox.information(
            self,
            "Java 安装完成",
            f"已安装并自动设为启动 Java：\n{java_exe}\n\n可以在设置页看到这个路径。",
        )
        self.scan_java()

    def _on_java_install_failed(self, message: str) -> None:
        self._busy = False
        self.install_java_button.setEnabled(True)
        self.opt_progress.set_shimmer(False)
        self.opt_progress.setRange(0, 100)
        self.opt_progress.setValue(0)
        self.opt_progress.setFormat("安装失败")
        self.java_status.setText(f"Java 安装失败：{message}")
        self.log_requested.emit("error", f"Java 安装失败：{message}")
        QMessageBox.warning(self, "Java 安装失败", message)

    # ----------------------------------------------------------- optimization
    def optimize(self) -> None:
        if self._busy:
            return
        if self.game_dir is None:
            QMessageBox.warning(self, "未设置目录", "请先在设置页选择 .minecraft 目录。")
            return
        version = self.version_combo.currentText().strip()
        if not version:
            QMessageBox.information(
                self,
                "没有原版版本",
                "游戏目录里还没有已安装的原版版本，请先在「下载 / 安装」页安装。",
            )
            return
        base_jar = self.game_dir / "versions" / version / f"{version}.jar"
        if not base_jar.is_file():
            QMessageBox.information(
                self,
                "需要先安装原版",
                f"Minecraft {version} 的客户端 jar 还不完整，请先在「下载 / 安装」页安装该版本。",
            )
            return

        self._busy = True
        self.optimize_button.setEnabled(False)
        self.opt_progress.set_shimmer(True)
        self.opt_progress.setRange(0, 0)
        self.opt_progress.setFormat("准备中…")
        self.opt_status.setProperty("level", "running")
        self.opt_status.setText(f"正在为 {version} 安装 Fabric 与优化 Mod…")
        self._repolish(self.opt_status)
        include_api = self.fabric_api_check.isChecked()

        def work(progress, cancelled):
            return fabric_install.optimize(
                self.game_dir,
                version,
                include_fabric_api=include_api,
                progress=lambda message, done, total: progress(message, done, total),
                cancel=cancelled,
            )

        start_task(
            self,
            work,
            on_done=self._on_optimized,
            on_failed=self._on_optimize_failed,
            on_progress=self._on_optimize_progress,
        )

    def _on_optimize_progress(self, message: str, done: int, total: int) -> None:
        self.opt_status.setText(message)
        if total > 0:
            self.opt_progress.setRange(0, total)
            self.opt_progress.setValue(min(done, total))
            self.opt_progress.setFormat(f"{min(done, total)} / {total}")
        else:
            self.opt_progress.setRange(0, 0)

    def _on_optimized(self, result: fabric_install.OptimizationResult) -> None:
        self._busy = False
        self.optimize_button.setEnabled(True)
        self.opt_progress.set_shimmer(False)
        self.opt_progress.setRange(0, 100)
        self.opt_progress.setValue(100)
        self.opt_progress.setFormat("优化完成")
        self.opt_status.setProperty("level", "success")
        self.opt_status.setText(
            f"已安装 Fabric {result.fabric.loader_version}，"
            f"下载 {len(result.mods)} 个优化 Mod。"
            f"请在版本库选择 {result.fabric.profile_id} 启动。"
        )
        self._repolish(self.opt_status)
        details = "\n".join(str(path.name) for path in result.mods)
        box = QMessageBox(self)
        box.setWindowTitle("优化完成")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"Fabric {result.fabric.loader_version} 已安装")
        box.setInformativeText(
            f"新版本：{result.fabric.profile_id}\n"
            f"依赖库：{result.fabric.libraries} 个已下载，{result.fabric.skipped} 个已存在\n"
            f"优化 Mod：\n{details}"
        )
        run_in_window(self, box, "优化完成")
        self.log_requested.emit(
            "success",
            f"一键优化完成：Fabric {result.fabric.loader_version} + {len(result.mods)} 个 Mod",
        )
        self.versions_changed.emit(result.fabric.profile_id)

    def _on_optimize_failed(self, message: str) -> None:
        self._busy = False
        self.optimize_button.setEnabled(True)
        self.opt_progress.set_shimmer(False)
        self.opt_progress.setRange(0, 100)
        self.opt_progress.setValue(0)
        self.opt_progress.setFormat("安装失败")
        self.opt_status.setProperty("level", "error")
        self.opt_status.setText(message)
        self._repolish(self.opt_status)
        self.log_requested.emit("error", f"一键优化失败：{message}")
        QMessageBox.warning(self, "优化失败", message)

    def _repolish(self, widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
