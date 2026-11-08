"""The three tab pages: 设置, 下载/安装 and 日志."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..constants import APP_NAME, APP_VERSION
from ..dev_state import DeveloperState
from ..services import ai_diag
from ..services import java_scan
from ..services import jvm
from .tasks import start_task
from .widgets import (
    TYPE_LABELS,
    ClickableLabel,
    HoverCard,
    LogView,
    ShimmerProgressBar,
)


def _card(title: str | None = None, subtitle: str | None = None) -> tuple[HoverCard, QVBoxLayout]:
    frame = HoverCard()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 15, 18, 15)
    layout.setSpacing(10)
    if title:
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
    if subtitle:
        note = QLabel(subtitle)
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
    return frame, layout


def _field(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("FieldLabel")
    return label


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def total_physical_memory_mb() -> int:
    """Return installed physical memory in MiB (Windows GlobalMemoryStatusEx)."""
    if os.name != "nt":
        return 8192
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys // (1024 * 1024))
    except Exception:
        pass
    return 8192


def _memory_text(combo: QComboBox) -> str:
    text = combo.currentText().strip()
    if not text or text.lower().startswith("auto") or text.startswith("自动"):
        return "auto"
    text = text.upper()
    if text.isdigit():
        text += "M"
    return text


def _progress_amount(value: int) -> str:
    """Format either a byte counter or a small file counter."""
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.0f} KB"
    return str(value)


def _memory_mb(value: str) -> int | None:
    text = str(value or "").strip().upper().replace(" ", "")
    if not text or text in {"AUTO", "自动"}:
        return None
    multiplier = 1
    if text.endswith("GB"):
        text, multiplier = text[:-2], 1024
    elif text.endswith("G"):
        text, multiplier = text[:-1], 1024
    elif text.endswith("MB"):
        text = text[:-2]
    elif text.endswith("M"):
        text = text[:-1]
    try:
        megabytes = float(text) * multiplier
    except ValueError:
        return None
    if megabytes < 1:
        return None
    return int(megabytes)


def _normalize_memory_pair(max_mem: str, min_mem: str) -> tuple[str, str, bool]:
    max_mb = _memory_mb(max_mem)
    min_mb = _memory_mb(min_mem)
    if max_mb is not None and min_mb is not None and min_mb > max_mb:
        return max_mem, max_mem, True
    return max_mem, min_mem, False


class SettingsPage(QWidget):
    theme_changed = Signal(str)
    settings_saved = Signal(dict)
    kernel_recheck = Signal()
    account_login_requested = Signal()
    account_logout_requested = Signal()
    developer_unlocked = Signal()
    developer_state_changed = Signal()
    appearance_changed = Signal()
    check_update_requested = Signal()

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._loading_settings = False
        self._offline_ack = False
        self._dev_unlocked = False
        self._version_clicks = 0
        self._background_path = ""
        self._memory_adjusted = False
        self._build()
        self.apply_config(config)
        QTimer.singleShot(300, lambda: self.scan_java(silent=True))

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        outer.addWidget(scroll)

        inner = QWidget()
        inner.setObjectName("PageInner")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(2, 8, 14, 10)
        layout.setSpacing(12)
        scroll.setWidget(inner)

        layout.addWidget(self._build_game_card())
        layout.addWidget(self._build_jvm_card())
        layout.addWidget(self._build_account_card())
        layout.addWidget(self._build_appearance_card())
        layout.addWidget(self._build_automation_card())
        layout.addWidget(self._build_ai_card())
        self.dev_card = self._build_developer_card()
        self.dev_card.hide()
        layout.addWidget(self.dev_card)
        layout.addWidget(self._build_kernel_card())
        layout.addWidget(self._build_about_card())

        save_row = QHBoxLayout()
        save_row.setSpacing(10)
        self.save_state = QLabel("")
        self.save_state.setObjectName("SaveState")
        save_row.addWidget(self.save_state)
        save_row.addStretch(1)
        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(lambda: self.settings_saved.emit(self.values()))
        save_row.addWidget(self.save_button)
        layout.addLayout(save_row)
        layout.addStretch(1)

        # A fixed footer keeps the version easter egg visible even while the
        # settings content is scrolled.
        footer = QHBoxLayout()
        footer.setContentsMargins(4, 2, 4, 2)
        footer.setSpacing(10)
        self.version_label = ClickableLabel(f"CoreNextLauncher v{APP_VERSION}")
        self.version_label.setObjectName("VersionLabel")
        self.version_label.setToolTip("连续点击 7 次解锁开发者选项")
        self.version_label.clicked.connect(self._on_version_clicked)
        footer.addWidget(self.version_label)
        self.dev_hint = QLabel("")
        self.dev_hint.setObjectName("Hint")
        self.dev_hint.setVisible(False)
        footer.addWidget(self.dev_hint)
        footer.addStretch(1)
        outer.addLayout(footer)

    def _make_memory_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(["自动", "2G", "4G", "6G", "8G", "12G", "16G"])
        combo.setCurrentText("自动")
        if combo.lineEdit() is not None:
            combo.lineEdit().setPlaceholderText("如 4G / 4096M")
        return combo

    def _build_game_card(self) -> QFrame:
        card, layout = _card("游戏与 Java", "指定 .minecraft 目录、Java 可执行文件与内存参数。")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)

        self.game_dir_edit = QLineEdit()
        self.game_dir_edit.setPlaceholderText("例如 文档\\CoreNextLauncher\\instances\\default")
        form.addRow(_field("游戏目录"), self._path_row(self.game_dir_edit, "浏览…", self._browse_game_dir))

        self.java_combo = QComboBox()
        self.java_combo.setEditable(True)
        self.java_combo.setMinimumWidth(300)
        self.java_combo.setToolTip(
            "可以填写 JDK 根目录（如 C:/Java/jdk-27）、bin 目录或 java.exe；\n"
            "启动时会自动补全 javaw.exe，兼容新版本 JDK。\n"
            "下拉列表来自本机自动扫描，选择后立即生效。"
        )
        if self.java_combo.lineEdit() is not None:
            self.java_combo.lineEdit().setPlaceholderText("JDK 根目录、bin 目录或 java.exe 均可")
        java_row = QWidget()
        java_layout = QHBoxLayout(java_row)
        java_layout.setContentsMargins(0, 0, 0, 0)
        java_layout.setSpacing(8)
        java_layout.addWidget(self.java_combo, 1)
        self.java_scan_button = QPushButton("自动扫描")
        self.java_scan_button.setObjectName("Ghost")
        self.java_scan_button.setToolTip("扫描注册表、安装目录、PATH 与 Minecraft 自带运行时")
        self.java_scan_button.clicked.connect(lambda: self.scan_java())
        java_layout.addWidget(self.java_scan_button)
        java_browse = QPushButton("浏览…")
        java_browse.clicked.connect(self._browse_java)
        java_layout.addWidget(java_browse)
        java_home = QPushButton("JDK 目录")
        java_home.setObjectName("Ghost")
        java_home.setToolTip("直接选择 JDK 根目录，启动时自动补全 bin/javaw.exe")
        java_home.clicked.connect(self._browse_java_home)
        java_layout.addWidget(java_home)
        form.addRow(_field("Java 路径"), java_row)

        self.java_status = QLabel("正在扫描本机 Java…")
        self.java_status.setObjectName("Hint")
        self.java_status.setWordWrap(True)
        layout.addWidget(self.java_status)

        self.max_mem_combo = self._make_memory_combo()
        form.addRow(_field("最大内存 (Xmx)"), self.max_mem_combo)

        self.min_mem_combo = self._make_memory_combo()
        form.addRow(_field("最小内存 (Xms)"), self.min_mem_combo)

        self.player_edit = QLineEdit()
        self.player_edit.setPlaceholderText("离线模式下的玩家名")
        form.addRow(_field("玩家名"), self.player_edit)

        layout.addLayout(form)

        optimize_row = QHBoxLayout()
        optimize_row.setSpacing(10)
        self.mem_optimize = QPushButton("一键智能内存优化")
        self.mem_optimize.clicked.connect(self._optimize_memory)
        optimize_row.addWidget(self.mem_optimize)
        self.memory_hint = QLabel("根据物理内存自动推荐 Xmx / Xms，并预留系统占用")
        self.memory_hint.setObjectName("Hint")
        self.memory_hint.setWordWrap(True)
        optimize_row.addWidget(self.memory_hint, 1)
        layout.addLayout(optimize_row)
        return card

    def _build_jvm_card(self) -> QFrame:
        card, layout = _card(
            "JVM 参数 · 可视化",
            "内存与垃圾回收参数会随启动传给 C++ 内核；不确定时保持“自动（内核默认）”。",
        )
        preset_row = QHBoxLayout()
        preset_row.setSpacing(10)
        preset_row.addWidget(_field("预设"))
        self.jvm_preset_combo = QComboBox()
        for key in jvm.PRESET_ORDER:
            self.jvm_preset_combo.addItem(jvm.PRESET_LABELS[key], key)
        self.jvm_preset_combo.setMinimumWidth(240)
        self.jvm_preset_combo.currentIndexChanged.connect(self._on_jvm_preset_changed)
        preset_row.addWidget(self.jvm_preset_combo)
        self.jvm_recommend_button = QPushButton("按硬件推荐")
        self.jvm_recommend_button.setObjectName("Primary")
        self.jvm_recommend_button.clicked.connect(self._recommend_jvm)
        preset_row.addWidget(self.jvm_recommend_button)
        preset_row.addStretch(1)
        layout.addLayout(preset_row)

        flags = QHBoxLayout()
        flags.setSpacing(12)
        self.jvm_g1_check = QCheckBox("G1GC（通用）")
        self.jvm_zgc_check = QCheckBox("ZGC（低延迟，Java 17+）")
        self.jvm_pretouch_check = QCheckBox("预触碰堆内存")
        self.jvm_parallel_check = QCheckBox("并行引用处理")
        self.jvm_utf8_check = QCheckBox("强制 UTF-8")
        self.jvm_utf8_check.setChecked(True)
        for widget in (
            self.jvm_g1_check,
            self.jvm_zgc_check,
            self.jvm_pretouch_check,
            self.jvm_parallel_check,
            self.jvm_utf8_check,
        ):
            widget.toggled.connect(self._on_jvm_flag_toggled)
            flags.addWidget(widget)
        flags.addStretch(1)
        layout.addLayout(flags)

        args_row = QHBoxLayout()
        args_row.setSpacing(8)
        self.jvm_args_list = QListWidget()
        self.jvm_args_list.setObjectName("JvmArgsList")
        self.jvm_args_list.setMaximumHeight(120)
        args_row.addWidget(self.jvm_args_list, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.jvm_arg_edit = QLineEdit()
        self.jvm_arg_edit.setPlaceholderText("追加参数，例如 -XX:+UseCompactObjectHeaders")
        self.jvm_arg_edit.returnPressed.connect(self._add_jvm_arg)
        side.addWidget(self.jvm_arg_edit)
        arg_buttons = QHBoxLayout()
        arg_buttons.setSpacing(6)
        add_arg = QPushButton("添加参数")
        add_arg.setObjectName("Ghost")
        add_arg.clicked.connect(self._add_jvm_arg)
        arg_buttons.addWidget(add_arg)
        remove_arg = QPushButton("删除选中")
        remove_arg.setObjectName("Ghost")
        remove_arg.clicked.connect(self._remove_jvm_arg)
        arg_buttons.addWidget(remove_arg)
        arg_buttons.addStretch(1)
        side.addLayout(arg_buttons)
        args_row.addLayout(side)
        layout.addLayout(args_row)

        self.jvm_hint = QLabel("")
        self.jvm_hint.setObjectName("Hint")
        self.jvm_hint.setWordWrap(True)
        layout.addWidget(self.jvm_hint)

        self._jvm_loading = False
        self._custom_jvm_args: list[str] = []
        self._sync_jvm_display()
        return card

    # ------------------------------------------------------------- JVM editor
    def _effective_jvm_args(self) -> list[str]:
        return jvm.arguments_from_flags(
            g1=self.jvm_g1_check.isChecked(),
            zgc=self.jvm_zgc_check.isChecked(),
            pretouch=self.jvm_pretouch_check.isChecked(),
            parallel_ref=self.jvm_parallel_check.isChecked(),
            utf8=self.jvm_utf8_check.isChecked(),
            extra=self._custom_jvm_args,
        )

    def _sync_jvm_display(self) -> None:
        args = self._effective_jvm_args()
        self.jvm_args_list.clear()
        if args:
            for arg in args:
                self.jvm_args_list.addItem(arg)
            self.jvm_hint.setText(
                f"当前 {len(args)} 个参数，将追加到启动命令；留空表示使用内核默认（G1GC）。"
            )
        else:
            item = QListWidgetItem("（使用内核默认 JVM 参数）")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.jvm_args_list.addItem(item)
            self.jvm_hint.setText("留空表示使用内核默认（G1GC）。")

    def _on_jvm_preset_changed(self, _index: int = -1) -> None:
        if self._jvm_loading:
            return
        key = str(self.jvm_preset_combo.currentData() or "auto")
        if key == "custom":
            return
        self._jvm_loading = True
        try:
            self.jvm_g1_check.setChecked(key in {"g1", "performance"})
            self.jvm_zgc_check.setChecked(key == "zgc")
            self.jvm_pretouch_check.setChecked(key == "performance")
            self.jvm_parallel_check.setChecked(key in {"g1", "performance"})
            self.jvm_utf8_check.setChecked(key != "auto")
            self._custom_jvm_args = []
        finally:
            self._jvm_loading = False
        self._sync_jvm_display()

    def _on_jvm_flag_toggled(self, checked: bool) -> None:
        if self._jvm_loading:
            return
        sender = self.sender()
        self._jvm_loading = True
        try:
            if sender is self.jvm_g1_check and checked:
                self.jvm_zgc_check.setChecked(False)
            elif sender is self.jvm_zgc_check and checked:
                self.jvm_g1_check.setChecked(False)
                self.jvm_parallel_check.setChecked(False)
        finally:
            self._jvm_loading = False
        args = self._effective_jvm_args()
        detected = jvm.preset_for_args(args)
        self.jvm_preset_combo.blockSignals(True)
        index = self.jvm_preset_combo.findData(detected)
        if index < 0:
            index = self.jvm_preset_combo.findData("custom")
        self.jvm_preset_combo.setCurrentIndex(max(0, index))
        self.jvm_preset_combo.blockSignals(False)
        self._sync_jvm_display()

    def _add_jvm_arg(self) -> None:
        text = self.jvm_arg_edit.text().strip()
        cleaned = jvm.sanitize_args([text])
        if not cleaned:
            QMessageBox.information(self, "参数无效", "请输入一个有效的 JVM 参数（不含换行）。")
            return
        value = cleaned[0]
        if value in self._effective_jvm_args():
            QMessageBox.information(self, "参数已存在", f"{value} 已经在参数列表中。")
            return
        self._custom_jvm_args.append(value)
        self.jvm_arg_edit.clear()
        self.jvm_preset_combo.blockSignals(True)
        index = self.jvm_preset_combo.findData("custom")
        if index >= 0:
            self.jvm_preset_combo.setCurrentIndex(index)
        self.jvm_preset_combo.blockSignals(False)
        self._sync_jvm_display()

    def _remove_jvm_arg(self) -> None:
        item = self.jvm_args_list.currentItem()
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        text = item.text()
        known = {
            "-XX:+UseG1GC",
            "-XX:MaxGCPauseMillis=200",
            "-XX:+UseZGC",
            "-XX:+AlwaysPreTouch",
            "-XX:+ParallelRefProcEnabled",
            "-Dfile.encoding=UTF-8",
        }
        self._jvm_loading = True
        try:
            if text == "-XX:+UseG1GC" or text == "-XX:MaxGCPauseMillis=200":
                self.jvm_g1_check.setChecked(False)
            elif text == "-XX:+UseZGC":
                self.jvm_zgc_check.setChecked(False)
            elif text == "-XX:+AlwaysPreTouch":
                self.jvm_pretouch_check.setChecked(False)
            elif text == "-XX:+ParallelRefProcEnabled":
                self.jvm_parallel_check.setChecked(False)
            elif text == "-Dfile.encoding=UTF-8":
                self.jvm_utf8_check.setChecked(False)
            elif text in self._custom_jvm_args:
                self._custom_jvm_args.remove(text)
            elif text not in known:
                self._custom_jvm_args = [
                    value for value in self._custom_jvm_args if value != text
                ]
        finally:
            self._jvm_loading = False
        self.jvm_preset_combo.blockSignals(True)
        index = self.jvm_preset_combo.findData("custom")
        if index >= 0:
            self.jvm_preset_combo.setCurrentIndex(index)
        self.jvm_preset_combo.blockSignals(False)
        self._sync_jvm_display()

    def _recommend_jvm(self) -> None:
        total = total_physical_memory_mb()
        advice = jvm.recommend_memory(total)
        self.max_mem_combo.setCurrentText(advice.max_text)
        self.min_mem_combo.setCurrentText(advice.min_text)
        self.memory_hint.setText(advice.reason)
        index = self.jvm_preset_combo.findData("auto")
        if index >= 0:
            self.jvm_preset_combo.setCurrentIndex(index)
        self._on_jvm_preset_changed()
        self.jvm_hint.setText(
            "已按物理内存推荐 Xmx/Xms；JVM 参数保持自动，由内核选择 G1GC。"
        )

    def _load_jvm_config(self, config) -> None:
        raw_args = jvm.sanitize_args(getattr(config, "jvm_args", []) or [])
        preset = str(getattr(config, "jvm_preset", "auto") or "auto")
        if preset not in jvm.PRESET_LABELS:
            preset = jvm.preset_for_args(raw_args)
        known = set(jvm.G1_ARGS) | set(jvm.ZGC_ARGS) | {
            "-XX:+AlwaysPreTouch",
            "-Dfile.encoding=UTF-8",
        }
        args = set(raw_args)
        self._jvm_loading = True
        try:
            self.jvm_g1_check.setChecked("-XX:+UseG1GC" in args)
            self.jvm_zgc_check.setChecked("-XX:+UseZGC" in args)
            self.jvm_pretouch_check.setChecked("-XX:+AlwaysPreTouch" in args)
            self.jvm_parallel_check.setChecked("-XX:+ParallelRefProcEnabled" in args)
            self.jvm_utf8_check.setChecked("-Dfile.encoding=UTF-8" in args)
            self._custom_jvm_args = [arg for arg in raw_args if arg not in known]
            index = self.jvm_preset_combo.findData(preset)
            if index < 0:
                index = self.jvm_preset_combo.findData(jvm.preset_for_args(raw_args))
            self.jvm_preset_combo.setCurrentIndex(max(0, index))
        finally:
            self._jvm_loading = False
        self._sync_jvm_display()

    # ------------------------------------------------------------- java scan
    def java_path(self) -> str:
        data = self.java_combo.currentData()
        if isinstance(data, str) and data:
            return data
        return self.java_combo.currentText().strip() or "java"

    def set_java_path(self, path: str) -> None:
        text = str(path or "").strip()
        self.java_combo.blockSignals(True)
        index = self.java_combo.findData(text) if text else -1
        if index >= 0:
            self.java_combo.setCurrentIndex(index)
        elif text:
            self.java_combo.insertItem(0, text, text)
            self.java_combo.setCurrentIndex(0)
        else:
            self.java_combo.setCurrentIndex(-1)
            self.java_combo.setEditText("")
        self.java_combo.blockSignals(False)

    def set_game_dir(self, path: str) -> None:
        """Update the game directory field (used by the instance page)."""
        self.game_dir_edit.setText(str(path or ""))

    def scan_java(self, silent: bool = False) -> None:
        if getattr(self, "_java_scanning", False):
            return
        self._java_scanning = True
        self.java_scan_button.setEnabled(False)
        self.java_status.setText("正在扫描本机 Java…")
        game_dir = self.game_dir_edit.text().strip()
        configured = str(getattr(self._config, "java_path", "") or "")
        self._java_configured = configured

        def work(progress, cancelled):
            return java_scan.scan_java(
                game_dir or None,
                on_progress=lambda done, total, text: progress(text, done, total),
            )

        start_task(self, work, on_done=self._on_java_scanned, on_failed=self._on_java_scan_failed)

    def _on_java_scanned(self, installs: list[java_scan.JavaInstall]) -> None:
        self._java_scanning = False
        self.java_scan_button.setEnabled(True)
        configured = getattr(self, "_java_configured", "") or ""
        keep_custom = configured not in ("", "java") and Path(configured).exists()
        self.java_combo.blockSignals(True)
        self.java_combo.clear()
        for install in installs:
            self.java_combo.addItem(install.label, install.path)
            self.java_combo.setItemData(
                self.java_combo.count() - 1, install.path, Qt.ItemDataRole.ToolTipRole
            )
        self.java_combo.blockSignals(False)

        if keep_custom:
            self.set_java_path(configured)
        else:
            best = java_scan.best_install(installs)
            if best is not None:
                self.set_java_path(best.path)

        if not installs:
            self.java_status.setText(
                "没有检测到本机 Java。可在「百宝箱 → 优化」里一键下载安装 Temurin。"
            )
        else:
            best = java_scan.best_install(installs)
            suffix = ""
            if not keep_custom and best is not None:
                suffix = f"；已自动选择 {best.label}"
            self.java_status.setText(f"已检测到 {len(installs)} 个 Java{suffix}")

    def _on_java_scan_failed(self, message: str) -> None:
        self._java_scanning = False
        self.java_scan_button.setEnabled(True)
        self.java_status.setText(f"Java 扫描失败：{message}")

    def _build_account_card(self) -> QFrame:
        card, layout = _card(
            "正版账号",
            "默认启动方式：Microsoft 正版登录。离线模式仅可在“开发者选项”中开启。",
        )
        row = QHBoxLayout()
        row.setSpacing(10)
        self.account_state = QLabel("● 未登录")
        self.account_state.setObjectName("AccountState")
        row.addWidget(self.account_state)
        row.addStretch(1)
        self.account_login_button = QPushButton("登录 / 切换账号")
        self.account_login_button.setObjectName("Primary")
        self.account_login_button.clicked.connect(self.account_login_requested.emit)
        row.addWidget(self.account_login_button)
        self.account_logout_button = QPushButton("退出登录")
        self.account_logout_button.setObjectName("Ghost")
        self.account_logout_button.setEnabled(False)
        self.account_logout_button.clicked.connect(self.account_logout_requested.emit)
        row.addWidget(self.account_logout_button)
        layout.addLayout(row)

        self.account_detail = QLabel("登录后可联机、使用正版皮肤与正版服务器。")
        self.account_detail.setObjectName("Muted")
        self.account_detail.setWordWrap(True)
        layout.addWidget(self.account_detail)
        return card

    def set_account(self, account) -> None:
        if account is None:
            self.account_state.setText("● 未登录")
            self.account_state.setProperty("state", "bad")
            self.account_detail.setText("启动游戏前需要完成 Microsoft 正版登录。")
            self.account_login_button.setText("登录 / 切换账号")
            self.account_logout_button.setEnabled(False)
        else:
            self.account_state.setText(f"● 已登录：{account.name}")
            self.account_state.setProperty("state", "ok")
            self.account_detail.setText(f"UUID：{account.uuid} · Microsoft 正版登录")
            self.account_login_button.setText("切换账号")
            self.account_logout_button.setEnabled(True)
        _repolish(self.account_state)

    def _build_appearance_card(self) -> QFrame:
        card, layout = _card(
            "外观 · 液态玻璃",
            "主题切换立即生效；选择一张本地图片作为背景，面板会像亚克力一样实时模糊身后的画面。",
        )
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(_field("界面主题"))
        self.theme_combo = QComboBox()
        for key in theme.THEME_ORDER:
            self.theme_combo.addItem(theme.THEME_LABELS[key], key)
        self.theme_combo.setFixedWidth(160)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_index)
        row.addWidget(self.theme_combo)
        row.addStretch(1)
        hint = QLabel("暗黑 / 明亮两套 QSS 主题")
        hint.setObjectName("Hint")
        row.addWidget(hint)
        layout.addLayout(row)

        glass_row = QHBoxLayout()
        glass_row.setSpacing(12)
        self.glass_check = QCheckBox("启用液态玻璃（实时背景模糊）")
        self.glass_check.setChecked(True)
        self.glass_check.toggled.connect(self._on_glass_toggled)
        glass_row.addWidget(self.glass_check)
        glass_row.addStretch(1)
        glass_hint = QLabel("关闭后恢复实色面板")
        glass_hint.setObjectName("Hint")
        glass_row.addWidget(glass_hint)
        layout.addLayout(glass_row)

        background_row = QHBoxLayout()
        background_row.setSpacing(14)
        self.background_preview = QLabel()
        self.background_preview.setObjectName("BackgroundPreview")
        self.background_preview.setFixedSize(156, 88)
        self.background_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.background_preview.setToolTip("当前背景预览")
        background_row.addWidget(self.background_preview)

        background_text = QVBoxLayout()
        background_text.setSpacing(4)
        self.background_label = QLabel("内置氛围背景")
        self.background_label.setObjectName("SectionTitle")
        background_text.addWidget(self.background_label)
        self.background_meta = QLabel("JPG / PNG / WebP / GIF · 建议 1920×1080 以上")
        self.background_meta.setObjectName("Hint")
        self.background_meta.setWordWrap(True)
        background_text.addWidget(self.background_meta)

        background_buttons = QHBoxLayout()
        background_buttons.setSpacing(8)
        self.background_button = QPushButton("选择图片…")
        self.background_button.setObjectName("Primary")
        self.background_button.clicked.connect(self._browse_background)
        background_buttons.addWidget(self.background_button)
        self.background_clear = QPushButton("清除背景")
        self.background_clear.setObjectName("Ghost")
        self.background_clear.clicked.connect(self._clear_background)
        background_buttons.addWidget(self.background_clear)
        background_buttons.addStretch(1)
        background_text.addLayout(background_buttons)
        background_row.addLayout(background_text, 1)
        layout.addLayout(background_row)

        self.blur_slider, self.blur_value = self._slider_row(
            layout, "玻璃模糊", 0, 48, 26, "px"
        )
        self.dim_slider, self.dim_value = self._slider_row(
            layout, "背景遮罩", 0, 80, 42, "%"
        )
        self.blur_slider.valueChanged.connect(self._on_blur_changed)
        self.dim_slider.valueChanged.connect(self._on_dim_changed)
        appearance_note = QLabel(
            "模糊强度决定玻璃面板身后画面的虚化程度；背景遮罩用于保证文字对比度。设置会即时预览并自动保存。"
        )
        appearance_note.setObjectName("Hint")
        appearance_note.setWordWrap(True)
        layout.addWidget(appearance_note)
        self._sync_appearance_widgets()
        return card

    def _slider_row(
        self,
        layout: QVBoxLayout,
        title: str,
        low: int,
        high: int,
        value: int,
        suffix: str,
    ) -> tuple[QSlider, QLabel]:
        row = QHBoxLayout()
        row.setSpacing(12)
        label = _field(title)
        label.setFixedWidth(64)
        row.addWidget(label)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setValue(value)
        slider.setMinimumWidth(180)
        slider.setMaximumWidth(340)
        row.addWidget(slider, 1)
        value_label = QLabel(f"{value}{suffix}")
        value_label.setObjectName("SliderValue")
        value_label.setFixedWidth(56)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(value_label)
        layout.addLayout(row)
        return slider, value_label

    def _sync_appearance_widgets(self) -> None:
        self.blur_slider.setEnabled(self.glass_check.isChecked())
        self.blur_value.setText(f"{self.blur_slider.value()}px")
        self.dim_value.setText(f"{self.dim_slider.value()}%")

    def _on_glass_toggled(self, _checked: bool) -> None:
        self._sync_appearance_widgets()
        if not self._loading_settings:
            self.appearance_changed.emit()

    def _on_blur_changed(self, value: int) -> None:
        self.blur_value.setText(f"{value}px")
        if not self._loading_settings:
            self.appearance_changed.emit()

    def _on_dim_changed(self, value: int) -> None:
        self.dim_value.setText(f"{value}%")
        if not self._loading_settings:
            self.appearance_changed.emit()

    def _browse_background(self) -> None:
        start = self._background_path or str(Path.home() / "Pictures")
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "选择启动器背景图片",
            start,
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*)",
        )
        if not chosen:
            return
        probe = QPixmap(chosen)
        if probe.isNull():
            QMessageBox.warning(
                self,
                "无法读取图片",
                "这个文件不是受支持的图片，或文件已经损坏。\n支持 JPG / PNG / WebP / BMP / GIF。",
            )
            return
        self._background_path = chosen
        self._update_background_preview()
        if not self._loading_settings:
            self.appearance_changed.emit()

    def _clear_background(self) -> None:
        if not self._background_path:
            return
        self._background_path = ""
        self._update_background_preview()
        if not self._loading_settings:
            self.appearance_changed.emit()

    def _update_background_preview(self) -> None:
        size = self.background_preview.size()
        path = self._background_path
        pixmap = QPixmap(path) if path else QPixmap()
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = max(0, (scaled.width() - size.width()) // 2)
            y = max(0, (scaled.height() - size.height()) // 2)
            self.background_preview.setPixmap(
                scaled.copy(x, y, size.width(), size.height())
            )
            self.background_label.setText(Path(path).name)
            self.background_label.setToolTip(path)
            self.background_meta.setText(
                f"{pixmap.width()}×{pixmap.height()} · 已应用，点击“选择图片…”可更换"
            )
            self.background_clear.setEnabled(True)
            return

        dark = theme.current_theme() != "light"
        thumb = QPixmap(size)
        thumb.fill(Qt.GlobalColor.transparent)
        painter = QPainter(thumb)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        gradient = QLinearGradient(0, 0, size.width(), size.height())
        if dark:
            gradient.setColorAt(0.0, QColor("#12203c"))
            gradient.setColorAt(1.0, QColor("#2a1745"))
            text_colour = QColor(220, 232, 255, 170)
        else:
            gradient.setColorAt(0.0, QColor("#dce7ff"))
            gradient.setColorAt(1.0, QColor("#f3e8ff"))
            text_colour = QColor(51, 65, 85, 190)
        painter.fillRect(thumb.rect(), gradient)
        painter.setPen(QPen(text_colour))
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(thumb.rect(), Qt.AlignmentFlag.AlignCenter, "内置氛围背景")
        painter.end()
        self.background_preview.setPixmap(thumb)
        self.background_label.setText("内置氛围背景")
        self.background_label.setToolTip("")
        self.background_meta.setText("未选择图片 · 支持 JPG / PNG / WebP / GIF")
        self.background_clear.setEnabled(False)

    def refresh_appearance_theme(self) -> None:
        self._update_background_preview()

    def _build_automation_card(self) -> QFrame:
        card, layout = _card(
            "自动化",
            "启动前静默备份存档；从 GitHub Releases 检查启动器更新。",
        )
        backup_row = QHBoxLayout()
        backup_row.setSpacing(10)
        self.auto_backup_check = QCheckBox("每次启动前静默备份存档")
        self.auto_backup_check.setChecked(True)
        self.auto_backup_check.setToolTip("只备份存档，不打包 Mod；只有存档变化时才会重新压缩")
        backup_row.addWidget(self.auto_backup_check)
        backup_row.addWidget(_field("每个存档保留"))
        self.backup_keep_spin = QSpinBox()
        self.backup_keep_spin.setRange(1, 30)
        self.backup_keep_spin.setValue(5)
        self.backup_keep_spin.setSuffix(" 份")
        backup_row.addWidget(self.backup_keep_spin)
        backup_row.addStretch(1)
        layout.addLayout(backup_row)

        update_row = QHBoxLayout()
        update_row.setSpacing(10)
        update_row.addWidget(_field("更新仓库"))
        self.update_repo_edit = QLineEdit()
        self.update_repo_edit.setPlaceholderText("owner/repo，例如 Notch/CoreNextLauncher")
        self.update_repo_edit.setToolTip(
            "填写发布 Release 的 GitHub 仓库（owner/repo）。\n"
            "Release 中需要附带一个 .exe 文件用于自动更新。"
        )
        update_row.addWidget(self.update_repo_edit, 1)
        self.check_update_button = QPushButton("检查更新")
        self.check_update_button.setObjectName("Primary")
        self.check_update_button.clicked.connect(self.check_update_requested.emit)
        update_row.addWidget(self.check_update_button)
        layout.addLayout(update_row)

        self.auto_update_check = QCheckBox("启动时静默检查更新（发现新版本才提示）")
        self.auto_update_check.setChecked(True)
        layout.addWidget(self.auto_update_check)

        hint = QLabel(
            "更新通过 GitHub API 比对版本号；下载新 exe 后由 .bat 脚本在本程序退出后替换并重启。"
            "源码运行时只提示，不会替换文件。"
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return card

    def _build_ai_card(self) -> QFrame:
        card, layout = _card(
            "AI 诊断 · DeepSeek",
            "填入 DeepSeek API Key 后，可在「百宝箱 → 崩溃诊断」让 AI 结合完整日志分析。"
            "密钥使用 Windows DPAPI 加密保存，不写入 launcher_config.json。",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self.ai_key_edit = QLineEdit()
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key_edit.setPlaceholderText("sk-…")
        row.addWidget(self.ai_key_edit, 1)

        self.ai_model_combo = QComboBox()
        self.ai_model_combo.setEditable(True)
        self.ai_model_combo.addItems(["deepseek-chat", "deepseek-reasoner"])
        self.ai_model_combo.setMinimumWidth(170)
        row.addWidget(self.ai_model_combo)

        self.ai_save_button = QPushButton("保存密钥")
        self.ai_save_button.setObjectName("Primary")
        self.ai_save_button.clicked.connect(self.save_ai_key)
        row.addWidget(self.ai_save_button)

        self.ai_test_button = QPushButton("测试连接")
        self.ai_test_button.setObjectName("Ghost")
        self.ai_test_button.clicked.connect(self.test_ai_key)
        row.addWidget(self.ai_test_button)
        layout.addLayout(row)

        self.ai_status = QLabel("")
        self.ai_status.setObjectName("AuthStatus")
        self.ai_status.setWordWrap(True)
        layout.addWidget(self.ai_status)

        note = QLabel(
            "调用时会把 latest.log、崩溃报告、Mod 列表与 Java/内存设置发送到 api.deepseek.com；"
            "日志里可能包含本机路径。"
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return card

    def save_ai_key(self) -> None:
        key = self.ai_key_edit.text().strip()
        try:
            ai_diag.save_api_key(key)
        except OSError as exc:
            self.ai_status.setProperty("level", "error")
            self.ai_status.setText(f"保存失败：{exc}")
            _repolish(self.ai_status)
            return
        if key:
            self.ai_status.setProperty("level", "success")
            self.ai_status.setText("● 密钥已加密保存，可以到「百宝箱 → 崩溃诊断」使用 AI 分析")
        else:
            self.ai_status.setProperty("level", "muted")
            self.ai_status.setText("已清除 DeepSeek API Key")
        _repolish(self.ai_status)

    def test_ai_key(self) -> None:
        key = self.ai_key_edit.text().strip() or ai_diag.load_api_key()
        if not key:
            self.ai_status.setProperty("level", "error")
            self.ai_status.setText("请先填写 API Key")
            _repolish(self.ai_status)
            return
        model = self.ai_model_combo.currentText().strip() or "deepseek-chat"
        self.ai_test_button.setEnabled(False)
        self.ai_status.setProperty("level", "running")
        self.ai_status.setText("正在连接 DeepSeek…")
        _repolish(self.ai_status)

        def work(progress, cancelled):
            return ai_diag.test_key(key, model)

        start_task(
            self,
            work,
            on_done=self._on_ai_test_done,
            on_failed=self._on_ai_test_failed,
        )

    def _on_ai_test_failed(self, message: str) -> None:
        self.ai_test_button.setEnabled(True)
        self.ai_status.setProperty("level", "error")
        self.ai_status.setText(f"● 连接失败：{message}")
        _repolish(self.ai_status)

    def _on_ai_test_done(self, reply: str) -> None:
        self.ai_test_button.setEnabled(True)
        self.ai_status.setProperty("level", "success")
        self.ai_status.setText(f"● 连接成功：{reply[:60]}")
        _repolish(self.ai_status)

    def _build_developer_card(self) -> QFrame:
        card, layout = _card()
        self.dev_toggle = QToolButton()
        self.dev_toggle.setObjectName("DevToggle")
        self.dev_toggle.setText("开发者选项")
        self.dev_toggle.setCheckable(True)
        self.dev_toggle.setChecked(False)
        self.dev_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.dev_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.dev_toggle.clicked.connect(self._toggle_developer)
        layout.addWidget(self.dev_toggle)

        self.dev_panel = QFrame()
        self.dev_panel.setObjectName("DevPanel")
        panel = QVBoxLayout(self.dev_panel)
        panel.setContentsMargins(12, 10, 12, 10)
        panel.setSpacing(8)
        self.offline_check = QCheckBox("启用离线模式（不进行正版登录）")
        self.offline_check.setChecked(False)
        self.offline_check.toggled.connect(self._on_offline_toggled)
        panel.addWidget(self.offline_check)
        warning = QLabel(
            "离线模式违反 Minecraft EULA，仅限本地测试与单机使用；启用前会再次弹出确认。"
            "开发者模式与离线模式仅在本次运行有效，关闭启动器后自动关闭。"
        )
        warning.setObjectName("Hint")
        warning.setWordWrap(True)
        panel.addWidget(warning)
        self.dev_panel.hide()
        layout.addWidget(self.dev_panel)
        return card

    def _build_about_card(self) -> QFrame:
        card, layout = _card("关于")
        row = QHBoxLayout()
        row.setSpacing(10)
        name = QLabel(APP_NAME)
        name.setObjectName("SectionTitle")
        row.addWidget(name)
        row.addStretch(1)
        layout.addLayout(row)

        description = QLabel("Minecraft 启动器 · Microsoft 正版登录 · C++ 内核驱动")
        description.setObjectName("Muted")
        description.setWordWrap(True)
        layout.addWidget(description)
        return card

    def _on_version_clicked(self) -> None:
        if self._dev_unlocked:
            return
        self._version_clicks += 1
        remaining = 7 - self._version_clicks
        self.dev_hint.setVisible(True)
        if remaining > 0:
            self.dev_hint.setText(f"再点 {remaining} 次进入开发者模式")
        else:
            self._unlock_developer()

    def _unlock_developer(self) -> None:
        self._dev_unlocked = True
        self.dev_hint.setVisible(True)
        self.dev_hint.setText("开发者模式已解锁（仅本次运行有效）")
        self._apply_developer_state(True)
        self.developer_unlocked.emit()
        self.developer_state_changed.emit()
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.dev_card))

    def _apply_developer_state(self, unlocked: bool) -> None:
        self._dev_unlocked = unlocked
        self.dev_card.setVisible(unlocked)
        if unlocked:
            self.dev_toggle.setChecked(True)
            self.dev_panel.setVisible(True)
            self.dev_toggle.setArrowType(Qt.ArrowType.DownArrow)
            self.dev_hint.setVisible(True)
            self.dev_hint.setText("开发者模式已解锁（仅本次运行有效）")
        else:
            self.dev_toggle.setChecked(False)
            self.dev_panel.hide()
            self.dev_toggle.setArrowType(Qt.ArrowType.RightArrow)
            self.dev_hint.hide()
            self.dev_hint.setText("")

    def developer_state(self) -> DeveloperState:
        """Current session-only developer/offline switches."""
        offline = self.offline_check.isChecked() and self._offline_ack
        return DeveloperState(
            developer_unlocked=bool(self._dev_unlocked),
            offline=bool(offline),
            offline_ack=bool(offline),
        ).normalized()

    def apply_developer_state(self, state: DeveloperState) -> None:
        """Apply a session state (normally the all-disabled state at startup)."""
        self._loading_settings = True
        try:
            active = bool(state.offline and state.offline_ack)
            self.offline_check.blockSignals(True)
            self.offline_check.setChecked(active)
            self.offline_check.blockSignals(False)
            self._offline_ack = active
            self._version_clicks = 0
            self._apply_developer_state(bool(state.developer_unlocked) or active)
        finally:
            self._loading_settings = False

    def _build_kernel_card(self) -> QFrame:
        card, layout = _card("C++ 内核", "图形界面通过 JSON 管道调用 build/mc_core.exe。")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.kernel_state = QLabel("检测中…")
        self.kernel_state.setObjectName("KernelState")
        row.addWidget(self.kernel_state)
        row.addStretch(1)
        recheck = QPushButton("重新检测")
        recheck.setObjectName("Ghost")
        recheck.clicked.connect(self.kernel_recheck.emit)
        row.addWidget(recheck)
        layout.addLayout(row)

        self.kernel_path = QLabel("")
        self.kernel_path.setObjectName("Hint")
        self.kernel_path.setWordWrap(True)
        self.kernel_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.kernel_path)
        return card

    def _path_row(self, edit: QLineEdit, button_text: str, callback) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(edit, 1)
        button = QPushButton(button_text)
        button.clicked.connect(callback)
        row.addWidget(button)
        return container

    # ------------------------------------------------------------- behaviour
    def _browse_game_dir(self) -> None:
        start = self.game_dir_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择 .minecraft 目录", start)
        if chosen:
            self.game_dir_edit.setText(chosen)
            self.settings_saved.emit(self.values())

    def _browse_java(self) -> None:
        start = self.java_combo.currentText().strip() or "C:/Program Files/Java"
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择 Java 可执行文件", start, "可执行文件 (*.exe);;所有文件 (*)"
        )
        if chosen:
            self.set_java_path(chosen)

    def _browse_java_home(self) -> None:
        start = self.java_combo.currentText().strip()
        if not start or not Path(start).is_dir():
            start = "C:/Program Files/Java" if os.name == "nt" else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "选择 JDK 根目录（或 bin 目录）", start
        )
        if chosen:
            self.set_java_path(chosen)

    def _optimize_memory(self) -> None:
        advice = jvm.recommend_memory(total_physical_memory_mb())
        self.max_mem_combo.setCurrentText(advice.max_text)
        self.min_mem_combo.setCurrentText(advice.min_text)
        self.memory_hint.setText(advice.reason)

    def _toggle_developer(self, checked: bool) -> None:
        self.dev_panel.setVisible(checked)
        self.dev_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )

    def _on_offline_toggled(self, checked: bool) -> None:
        if self._loading_settings:
            return
        if not checked:
            self._offline_ack = False
            self.developer_state_changed.emit()
            return

        box = QMessageBox(self)
        box.setWindowTitle("高风险警告：启用离线模式")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("离线模式违反 Minecraft 最终用户许可协议（EULA）。")
        box.setInformativeText(
            "此选项仅可用于本地开发、测试，或你已合法购买并仅自己游玩的单人环境。\n\n"
            "启用后启动器不会验证正版账号，请勿用于联机服务器、分发或商业用途。\n"
            "由此产生的账号风险、法律风险与安全风险由你自行承担。\n\n"
            "该设置仅在本次运行有效，关闭启动器后会自动关闭。\n\n"
            "确定要启用离线模式吗？"
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            self.offline_check.blockSignals(True)
            self.offline_check.setChecked(False)
            self.offline_check.blockSignals(False)
            self._offline_ack = False
        else:
            self._offline_ack = True
        self.developer_state_changed.emit()

    def _on_theme_index(self, _index: int) -> None:
        key = self.theme_combo.currentData()
        if key:
            self.theme_changed.emit(str(key))

    def set_theme_key(self, key: str) -> None:
        """Keep the combo in sync when the theme is changed externally."""
        index = self.theme_combo.findData(key)
        if index < 0:
            return
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(index)
        self.theme_combo.blockSignals(False)
        self.refresh_appearance_theme()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def flash_saved(self, text: str = "已保存 ✓") -> None:
        self.save_state.setText(text)
        QTimer.singleShot(2200, lambda: self.save_state.setText(""))

    # -------------------------------------------------------------- data API
    def values(self) -> dict:
        max_mem = _memory_text(self.max_mem_combo)
        min_mem = _memory_text(self.min_mem_combo)
        max_mem, min_mem, adjusted = _normalize_memory_pair(max_mem, min_mem)
        self._memory_adjusted = adjusted
        if adjusted:
            self.min_mem_combo.blockSignals(True)
            self.min_mem_combo.setCurrentText(max_mem)
            self.min_mem_combo.blockSignals(False)
        return {
            "game_dir": self.game_dir_edit.text().strip(),
            "java_path": self.java_path(),
            "max_mem": max_mem,
            "min_mem": min_mem,
            "player_name": self.player_edit.text().strip() or "Steve",
            "theme": self.theme_combo.currentData() or "dark",
            "background_image": self._background_path,
            "glass_enabled": self.glass_check.isChecked(),
            "glass_blur": self.blur_slider.value(),
            "background_dim": self.dim_slider.value(),
            "auto_backup": self.auto_backup_check.isChecked(),
            "backup_keep": self.backup_keep_spin.value(),
            "update_repo": self.update_repo_edit.text().strip(),
            "auto_update_check": self.auto_update_check.isChecked(),
            "ai_model": self.ai_model_combo.currentText().strip() or "deepseek-chat",
            "jvm_preset": self.jvm_preset_combo.currentData() or "auto",
            "jvm_args": self._effective_jvm_args(),
        }

    def memory_adjusted(self) -> bool:
        return self._memory_adjusted

    def apply_config(self, config) -> None:
        self._loading_settings = True
        try:
            self.game_dir_edit.setText(config.game_dir)
            self.set_java_path(config.java_path)
            self._set_memory(self.max_mem_combo, config.max_mem)
            self._set_memory(self.min_mem_combo, getattr(config, "min_mem", "auto"))
            self.player_edit.setText(config.player_name)
            theme_index = self.theme_combo.findData(config.theme)
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(max(0, theme_index))
            self.theme_combo.blockSignals(False)

            background = str(getattr(config, "background_image", "") or "")
            self._background_path = background if background and Path(background).is_file() else ""
            self.glass_check.blockSignals(True)
            self.glass_check.setChecked(bool(getattr(config, "glass_enabled", True)))
            self.glass_check.blockSignals(False)
            self.blur_slider.blockSignals(True)
            self.blur_slider.setValue(max(0, min(48, int(getattr(config, "glass_blur", 26)))))
            self.blur_slider.blockSignals(False)
            self.dim_slider.blockSignals(True)
            self.dim_slider.setValue(
                max(0, min(80, int(getattr(config, "background_dim", 42))))
            )
            self.dim_slider.blockSignals(False)
            self._update_background_preview()
            self._sync_appearance_widgets()

            self.auto_backup_check.setChecked(bool(getattr(config, "auto_backup", True)))
            self.backup_keep_spin.setValue(int(getattr(config, "backup_keep", 5) or 5))
            self.update_repo_edit.setText(str(getattr(config, "update_repo", "") or ""))
            self.auto_update_check.setChecked(
                bool(getattr(config, "auto_update_check", True))
            )
            model = str(getattr(config, "ai_model", "deepseek-chat") or "deepseek-chat")
            index = self.ai_model_combo.findText(model)
            if index < 0:
                self.ai_model_combo.addItem(model)
                index = self.ai_model_combo.findText(model)
            self.ai_model_combo.setCurrentIndex(max(0, index))
            key = ai_diag.load_api_key()
            self.ai_key_edit.setText(key)
            self.ai_status.setProperty("level", "success" if key else "muted")
            self.ai_status.setText(
                "● 密钥已配置，可在百宝箱中使用 AI 诊断"
                if key
                else "尚未配置 DeepSeek API Key（可留空，不影响其他功能）"
            )
            _repolish(self.ai_status)
            self._load_jvm_config(config)

        finally:
            self._loading_settings = False

    @staticmethod
    def _set_memory(combo: QComboBox, value: str) -> None:
        combo.blockSignals(True)
        combo.setCurrentText("自动" if not value or value == "auto" else str(value))
        combo.blockSignals(False)

    def set_kernel(self, ok: bool, path: str, size: int) -> None:
        self.kernel_state.setText("● 已就绪" if ok else "● 未找到")
        self.kernel_state.setProperty("state", "ok" if ok else "bad")
        _repolish(self.kernel_state)
        if ok:
            self.kernel_path.setText(f"{path}\n{size / (1024 * 1024):.1f} MB · 支持 scan / launch / install")
        else:
            self.kernel_path.setText(f"{path}\n请先编译 mc_core.exe，然后点击“重新检测”。")


class InstallPage(QWidget):
    log_requested = Signal(str, str)
    install_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mc_dir: Path | None = None
        self.info: dict | None = None
        self.version_dir: Path | None = None
        self._build()
        self.stack.setCurrentWidget(self.empty_page)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 8, 14, 10)
        outer.setSpacing(10)

        header = QVBoxLayout()
        header.setSpacing(3)
        title = QLabel("下载 / 安装")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        subtitle = QLabel("仅从 Mojang 官方源下载（piston-meta / 官方资源 CDN），不使用任何第三方镜像。")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        header.addWidget(subtitle)
        outer.addLayout(header)

        install_card, install_layout = _card("官方源安装")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.install_version_edit = QLineEdit()
        self.install_version_edit.setPlaceholderText("输入官方版本号，如 1.20.1、26.2 或 latest-release")
        self.install_version_edit.returnPressed.connect(self._emit_install)
        row.addWidget(self.install_version_edit, 1)
        self.install_button = QPushButton("开始下载")
        self.install_button.setObjectName("Primary")
        self.install_button.clicked.connect(self._emit_install)
        row.addWidget(self.install_button)
        install_layout.addLayout(row)

        self.install_progress = ShimmerProgressBar()
        self.install_progress.setObjectName("InstallProgress")
        self.install_progress.setRange(0, 100)
        self.install_progress.setValue(0)
        self.install_progress.setTextVisible(True)
        self.install_progress.setFormat("等待开始")
        install_layout.addWidget(self.install_progress)

        self.install_status = QLabel("输入版本号后开始下载；已存在的文件会自动跳过。")
        self.install_status.setObjectName("InstallStatus")
        self.install_status.setWordWrap(True)
        install_layout.addWidget(self.install_status)
        outer.addWidget(install_card)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(0, 12, 0, 0)
        empty = QLabel("在左侧选择一个已安装版本，可查看本地文件校验结果")
        empty.setObjectName("EmptyHint")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        empty_layout.addWidget(empty)
        empty_layout.addStretch(1)
        self.stack.addWidget(self.empty_page)

        self.content_page = QWidget()
        content = QVBoxLayout(self.content_page)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(10)

        summary, summary_layout = _card()
        self.version_name = QLabel("—")
        self.version_name.setObjectName("PageTitle")
        summary_layout.addWidget(self.version_name)
        self.version_meta = QLabel("")
        self.version_meta.setObjectName("Muted")
        summary_layout.addWidget(self.version_meta)
        self.detail_labels: dict[str, QLabel] = {}
        detail_form = QFormLayout()
        detail_form.setHorizontalSpacing(18)
        detail_form.setVerticalSpacing(5)
        for key, label in (("type", "版本类型"), ("main_class", "主类"), ("path", "版本目录")):
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            detail_form.addRow(_field(label), value)
            self.detail_labels[key] = value
        summary_layout.addLayout(detail_form)
        content.addWidget(summary)

        checks_card, checks_layout = _card("本地文件校验", "校验结果来自磁盘上的真实文件，不会修改任何内容。")
        self.checks_host = QWidget()
        self.checks_layout = QVBoxLayout(self.checks_host)
        self.checks_layout.setContentsMargins(0, 0, 0, 0)
        self.checks_layout.setSpacing(7)
        checks_layout.addWidget(self.checks_host)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        validate = QPushButton("校验文件")
        validate.clicked.connect(self.validate)
        actions.addWidget(validate)
        open_dir = QPushButton("打开版本目录")
        open_dir.setObjectName("Ghost")
        open_dir.clicked.connect(self.open_version_dir)
        actions.addWidget(open_dir)
        actions.addStretch(1)
        note = QLabel("安装完成后建议重新校验")
        note.setObjectName("Hint")
        actions.addWidget(note)
        checks_layout.addLayout(actions)
        content.addWidget(checks_card)
        content.addStretch(1)

        # The validation list needs more height than a short window can give;
        # scroll it instead of squeezing the labels into unreadable slivers.
        self.content_scroll = QScrollArea()
        self.content_scroll.setObjectName("InstallContentScroll")
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_scroll.setWidget(self.content_page)
        self.stack.addWidget(self.content_scroll)

    # -------------------------------------------------------------- install UI
    def _emit_install(self) -> None:
        version_id = self.install_version_edit.text().strip()
        if not version_id:
            QMessageBox.information(self, "缺少版本号", "请输入 Mojang 官方版本号，例如 1.20.1。")
            return
        self.install_requested.emit(version_id)

    def set_installing(self, running: bool, version: str = "") -> None:
        self.install_button.setEnabled(not running)
        self.install_button.setText("下载中…" if running else "开始下载")
        self.install_version_edit.setEnabled(not running)
        if running:
            self.install_progress.setRange(0, 0)
            self.install_progress.setValue(0)
            self.install_progress.setFormat("下载中…")
            self.install_progress.set_shimmer(True)
            self.install_status.setProperty("level", "running")
            self.install_status.setText(f"正在从 Mojang 官方源下载 {version} …")
        else:
            self.install_progress.set_shimmer(False)
            if self.install_progress.maximum() <= 0:
                self.install_progress.setRange(0, 100)
                self.install_progress.setValue(0)
            self.install_progress.setFormat("%p%")
        _repolish(self.install_status)

    def set_install_progress(self, done: int, total: int, current: str) -> None:
        self.install_progress.set_shimmer(True)
        if total <= 0:
            self.install_progress.setRange(0, 0)
            self.install_progress.setFormat("下载中…")
        else:
            self.install_progress.setRange(0, total)
            value = min(done, total)
            # Progress events arrive from several download threads; never move
            # the bar backwards even if they are emitted slightly out of order.
            value = max(value, self.install_progress.value())
            value = min(value, total)
            self.install_progress.setValue(value)
            if total >= 1_000_000:
                # The C++ core reports aggregate bytes for libraries/assets and
                # the client JAR; show a readable MB/KB counter.
                label = f"{_progress_amount(value)} / {_progress_amount(total)}"
            else:
                # Small totals (including the final file count) are counts.
                label = f"{value} / {total}"
            self.install_progress.setFormat(label)
        if current:
            self.install_status.setText(f"正在下载：{current}")

    def set_install_status(self, text: str, level: str = "muted") -> None:
        self.install_status.setText(text)
        self.install_status.setProperty("level", level)
        _repolish(self.install_status)

    # -------------------------------------------------------------- data API
    def set_version(self, mc_dir: str, info: dict | None) -> None:
        if not info:
            self.info = None
            self.stack.setCurrentWidget(self.empty_page)
            return
        self.mc_dir = Path(mc_dir)
        self.info = info
        version_id = str(info.get("id", "—"))
        self.version_dir = self.mc_dir / "versions" / version_id
        raw = info.get("raw") if isinstance(info.get("raw"), dict) else {}
        vtype = str(raw.get("type") or info.get("type") or "release")

        self.version_name.setText(version_id)
        self.version_meta.setText("Minecraft 版本 · " + TYPE_LABELS.get(vtype, vtype))
        self.detail_labels["type"].setText(TYPE_LABELS.get(vtype, vtype))
        self.detail_labels["main_class"].setText(str(raw.get("mainClass") or "—"))
        self.detail_labels["path"].setText(str(self.version_dir))
        self.stack.setCurrentWidget(self.content_scroll)
        if self.content_scroll.verticalScrollBar() is not None:
            self.content_scroll.verticalScrollBar().setValue(0)
        self.run_checks()

    # ----------------------------------------------------------------- checks
    def _collect_checks(self) -> list[dict]:
        checks: list[dict] = []
        if not self.version_dir or not self.mc_dir or not self.info:
            return checks
        version_id = str(self.info.get("id", ""))
        manifest = self.version_dir / f"{version_id}.json"
        client_jar = self.version_dir / f"{version_id}.jar"
        raw = self.info.get("raw") if isinstance(self.info.get("raw"), dict) else {}
        libraries = raw.get("libraries") if isinstance(raw.get("libraries"), list) else []
        expected = 0
        present = 0
        for lib in libraries:
            if not isinstance(lib, dict):
                continue
            downloads = lib.get("downloads") if isinstance(lib.get("downloads"), dict) else {}
            artifact = downloads.get("artifact") if isinstance(downloads.get("artifact"), dict) else {}
            rel = artifact.get("path")
            if not rel:
                continue
            expected += 1
            if (self.mc_dir / "libraries" / str(rel)).is_file():
                present += 1

        checks.append(
            {
                "title": "版本清单",
                "detail": manifest.name,
                "state": "ok" if manifest.is_file() else "bad",
            }
        )
        checks.append(
            {
                "title": "客户端 JAR",
                "detail": client_jar.name,
                "state": "ok" if client_jar.is_file() else "bad",
            }
        )
        if expected == 0:
            checks.append({"title": "依赖库", "detail": "版本 JSON 未声明依赖库", "state": "warn"})
        else:
            checks.append(
                {
                    "title": "依赖库",
                    "detail": f"{present} / {expected} 已存在",
                    "state": "ok" if present == expected else "bad",
                }
            )
        natives = self.version_dir / "natives"
        checks.append(
            {
                "title": "Natives 目录",
                "detail": natives.name,
                "state": "ok" if natives.is_dir() else "warn",
            }
        )
        return checks

    def run_checks(self) -> list[dict]:
        checks = self._collect_checks()
        while self.checks_layout.count():
            item = self.checks_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        symbols = {"ok": "✓", "bad": "✗", "warn": "!"}
        for check in checks:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(10)
            symbol = QLabel(symbols.get(check["state"], "?"))
            symbol.setObjectName("CheckState")
            symbol.setProperty("state", check["state"])
            line.addWidget(symbol)
            title = QLabel(check["title"])
            title.setMinimumWidth(90)
            line.addWidget(title)
            detail = QLabel(check["detail"])
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            line.addWidget(detail, 1)
            self.checks_layout.addWidget(row)
        return checks

    def validate(self) -> None:
        if not self.info:
            return
        checks = self.run_checks()
        version_id = self.info.get("id", "?")
        self.log_requested.emit("info", f"开始校验版本 {version_id} 的本地文件…")
        for check in checks:
            level = {"ok": "success", "warn": "warn", "bad": "error"}[check["state"]]
            mark = {"ok": "✓", "warn": "!", "bad": "✗"}[check["state"]]
            self.log_requested.emit(level, f"{mark} {check['title']}：{check['detail']}")
        ok = all(check["state"] == "ok" for check in checks)
        self.log_requested.emit("success" if ok else "warn", "校验完成：文件完整" if ok else "校验完成：存在缺失或未确认的文件")

    def open_version_dir(self) -> None:
        if not self.version_dir or not self.version_dir.is_dir():
            self.log_requested.emit("warn", "版本目录不存在，无法打开。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.version_dir)))


class LogPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 8, 14, 10)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        title = QLabel("运行日志")
        title.setObjectName("SectionTitle")
        toolbar.addWidget(title)
        toolbar.addStretch(1)

        self.auto_check = QCheckBox("自动滚动")
        self.auto_check.setChecked(True)
        self.auto_check.toggled.connect(self._set_auto_scroll)
        toolbar.addWidget(self.auto_check)

        copy_button = QPushButton("复制")
        copy_button.setObjectName("Ghost")
        copy_button.clicked.connect(self.copy_all)
        toolbar.addWidget(copy_button)

        clear_button = QPushButton("清空")
        clear_button.setObjectName("Ghost")
        clear_button.clicked.connect(self.clear_logs)
        toolbar.addWidget(clear_button)
        layout.addLayout(toolbar)

        self.log_view = LogView()
        layout.addWidget(self.log_view, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.state = QLabel("空闲")
        self.state.setObjectName("RunState")
        footer.addWidget(self.state)
        footer.addStretch(1)
        hint = QLabel("mc_core.exe 的 stdout 事件会实时显示在这里")
        hint.setObjectName("FooterHint")
        footer.addWidget(hint)
        layout.addLayout(footer)

    def log(self, level: str, message: str) -> None:
        self.log_view.add(level, message)

    def clear_logs(self) -> None:
        self.log_view.clear_logs()

    def copy_all(self) -> None:
        QApplication.clipboard().setText(self.log_view.toPlainText())

    def _set_auto_scroll(self, enabled: bool) -> None:
        self.log_view.auto_scroll = enabled
        if enabled:
            self.log_view.scroll_to_bottom()

    def set_running(self, running: bool) -> None:
        self.state.setText("运行中…" if running else "空闲")
        self.state.setProperty("state", "running" if running else "")
        _repolish(self.state)
