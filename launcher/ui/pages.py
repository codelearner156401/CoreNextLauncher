"""The three tab pages: 设置, 下载/安装 and 日志."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QSize,
    QTimer,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import anim, theme
from ..constants import APP_NAME, APP_VERSION
from ..dev_state import DeveloperState
from ..services import ai_diag
from ..services import catalog
from ..services import fabric_install
from ..services import java_scan
from ..services import jvm
from ..services import net
from .tasks import start_task
from .dialog_page import run_in_window
from .loading_overlay import LoadingOverlay
from .version_card import VersionCardDelegate
from .widgets import (
    TYPE_LABELS,
    ClickableLabel,
    HoverCard,
    LogView,
    ShimmerProgressBar,
)

try:  # launcher/config.py is owned elsewhere: degrade instead of failing to import.
    from .. import config as _config_module
except ImportError:  # pragma: no cover - config.py always ships
    _config_module = None

_NORMALIZE_DIRECTORY = (
    getattr(_config_module, "normalize_directory", None) if _config_module else None
)

# Fabric keeps the maven host on each entry's url; this is only the fallback for
# libraries whose JSON carries neither an artifact nor a url of its own.
FABRIC_MAVEN = "https://maven.fabricmc.net/"
ASSET_RESOURCE_BASE = "https://resources.download.minecraft.net"
INHERIT_MAX_DEPTH = 5
FULL_HASH_LIMIT = 64 * 1024 * 1024


def _normalize_directory(value: str) -> str:
    """Absolute directory from *value*, or "" when it cannot be one."""
    if _NORMALIZE_DIRECTORY is not None:
        try:
            return str(_NORMALIZE_DIRECTORY(value) or "")
        except (OSError, TypeError, ValueError):
            return ""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        candidate = Path(os.path.expanduser(text))
        return str(candidate.resolve()) if candidate.is_absolute() else ""
    except OSError:
        return ""


def _normalize_image_path(value: str) -> str:
    """expanduser + resolve, keeping the real absolute drive (D:/, E:/ …)."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve())
    except OSError:
        return text


def _int_or_zero(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


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


def _tab_scroll(widget: QWidget, name: str, min_height: int) -> QScrollArea:
    """Wrap one tab card in its own scroll area (one per tab, never nested)."""
    area = QScrollArea()
    area.setObjectName(name)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setMinimumHeight(min_height)
    # The card has to grow with the viewport; at its default Preferred policy
    # the scroll area keeps it at the size hint and leaves a blank strip below.
    widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    area.setWidget(widget)
    return area


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
    animation_speed_changed = Signal(float)
    debug_action = Signal(str, str)
    skin_requested = Signal()
    multiplayer_requested = Signal()
    check_update_requested = Signal()
    log_requested = Signal(str, str)

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._loading_settings = False
        self._offline_ack = False
        self._dev_unlocked = False
        self._version_clicks = 0
        self._background_path = ""
        self._splash_path = ""
        self._theme_profiles: dict = {}
        self._active_style = "liquid"
        self.animation_speed = 1.0
        self._primary_color = "#4f8cff"
        self._accent_color = "#7b5cff"
        self._memory_adjusted = False
        # Decoding a wallpaper costs far more than the blur/dim tick, so both the
        # decoded image and the cropped preview are cached by (path, mtime).
        self._image_cache: dict[tuple, QPixmap] = {}
        self._preview_cache: dict[tuple, QPixmap] = {}
        self._image_stats = {"decode": 0, "scale": 0}
        self._warned_game_dir = ""
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
        version_column = QVBoxLayout()
        version_column.setContentsMargins(0, 0, 0, 0)
        version_column.setSpacing(2)
        version_column.addWidget(self.version_label)
        self.third_party_label = QLabel()
        self.third_party_label.setObjectName("Hint")
        self.third_party_label.setTextFormat(Qt.TextFormat.RichText)
        self.third_party_label.setText(
            '<a href="third-party">Powered by PySide6 (LGPLv3) &amp; EasyTier (Apache-2.0)</a>'
        )
        self.third_party_label.setOpenExternalLinks(False)
        self.third_party_label.linkActivated.connect(self._open_third_party_notices)
        version_column.addWidget(self.third_party_label)
        footer.addLayout(version_column)
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
            "主题 · 界面风格",
            "三套界面独立保存配色、毛玻璃开关、模糊强度与启动图；切换立即生效。",
        )

        style_row = QHBoxLayout()
        style_row.setSpacing(12)
        style_row.addWidget(_field("界面风格"))
        self.style_combo = QComboBox()
        for key in theme.STYLE_ORDER:
            self.style_combo.addItem(theme.STYLE_LABELS[key], key)
        self.style_combo.setFixedWidth(160)
        self.style_combo.currentIndexChanged.connect(self._on_style_changed)
        style_row.addWidget(self.style_combo)
        style_row.addStretch(1)
        style_hint = QLabel("液态玻璃 / 毛玻璃 / HTML 界面，各自独立记忆设置")
        style_hint.setObjectName("Hint")
        style_row.addWidget(style_hint)
        layout.addLayout(style_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(10)
        color_row.addWidget(_field("配色"))
        self.color_preset = QComboBox()
        for key in theme.COLOR_PRESETS:
            self.color_preset.addItem(theme.PRESET_LABELS.get(key, key), key)
        self.color_preset.setFixedWidth(120)
        self.color_preset.currentIndexChanged.connect(self._on_preset_changed)
        color_row.addWidget(self.color_preset)
        self.primary_button = QPushButton("#4f8cff")
        self.primary_button.setObjectName("ColorSwatch")
        self.primary_button.setFixedWidth(110)
        self.primary_button.setToolTip("自定义主色调")
        self.primary_button.clicked.connect(self._pick_primary)
        color_row.addWidget(self.primary_button)
        self.accent_button = QPushButton("#7b5cff")
        self.accent_button.setObjectName("ColorSwatch")
        self.accent_button.setFixedWidth(110)
        self.accent_button.setToolTip("自定义辅助色")
        self.accent_button.clicked.connect(self._pick_accent)
        color_row.addWidget(self.accent_button)
        color_row.addStretch(1)
        layout.addLayout(color_row)

        glass_row = QHBoxLayout()
        glass_row.setSpacing(14)
        self.glass_check = QCheckBox("面板毛玻璃")
        self.glass_check.setChecked(True)
        self.glass_check.toggled.connect(self._on_glass_toggled)
        glass_row.addWidget(self.glass_check)
        self.system_blur_check = QCheckBox("全局背景模糊（系统级）")
        self.system_blur_check.setChecked(True)
        self.system_blur_check.toggled.connect(self._on_glass_toggled)
        glass_row.addWidget(self.system_blur_check)
        self.loading_check = QCheckBox("启用启动加载动画")
        self.loading_check.setChecked(True)
        self.loading_check.toggled.connect(self._on_loading_toggled)
        glass_row.addWidget(self.loading_check)
        glass_row.addStretch(1)
        layout.addLayout(glass_row)

        self.blur_slider, self.blur_value = self._slider_row(
            layout, "玻璃模糊", 0, 48, 26, "px"
        )
        self.dim_slider, self.dim_value = self._slider_row(
            layout, "背景遮罩", 0, 80, 42, "%"
        )
        self.blur_slider.valueChanged.connect(self._on_blur_changed)
        self.dim_slider.valueChanged.connect(self._on_dim_changed)

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
        self.background_button = QPushButton("选择背景…")
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

        splash_row = QHBoxLayout()
        splash_row.setSpacing(14)
        self.splash_preview = QLabel()
        self.splash_preview.setObjectName("BackgroundPreview")
        self.splash_preview.setFixedSize(156, 88)
        self.splash_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.splash_preview.setToolTip("启动画面预览")
        splash_row.addWidget(self.splash_preview)
        splash_text = QVBoxLayout()
        splash_text.setSpacing(4)
        self.splash_label = QLabel("默认加载动画")
        self.splash_label.setObjectName("SectionTitle")
        splash_text.addWidget(self.splash_label)
        splash_meta = QLabel("启动时展示自定义图片；未设置时显示旋转 C 图标加载动画")
        splash_meta.setObjectName("Hint")
        splash_meta.setWordWrap(True)
        splash_text.addWidget(splash_meta)
        splash_buttons = QHBoxLayout()
        splash_buttons.setSpacing(8)
        self.splash_button = QPushButton("选择启动图…")
        self.splash_button.setObjectName("Primary")
        self.splash_button.clicked.connect(self._browse_splash)
        splash_buttons.addWidget(self.splash_button)
        self.splash_clear = QPushButton("恢复默认")
        self.splash_clear.setObjectName("Ghost")
        self.splash_clear.clicked.connect(self._clear_splash)
        splash_buttons.addWidget(self.splash_clear)
        splash_buttons.addStretch(1)
        splash_text.addLayout(splash_buttons)
        splash_row.addLayout(splash_text, 1)
        layout.addLayout(splash_row)

        skin_row = QHBoxLayout()
        skin_row.setSpacing(8)
        self.skin_button = QPushButton("皮肤预览")
        self.skin_button.setObjectName("Ghost")
        self.skin_button.setToolTip("打开皮肤管理页面，查看 2D 纸娃娃皮肤预览")
        self.skin_button.clicked.connect(self.skin_requested.emit)
        skin_row.addWidget(self.skin_button)
        skin_hint = QLabel("随时打开皮肤管理，切换 Steve / Alex 与本地皮肤")
        skin_hint.setObjectName("Hint")
        skin_row.addWidget(skin_hint)
        skin_row.addStretch(1)
        layout.addLayout(skin_row)

        note = QLabel(
            "无自定义背景时默认开启系统级背景模糊；上传背景图后可自行选择是否保留模糊。"
            "所有主题参数按风格独立保存，关闭程序后仍会保留。"
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
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

    # --------------------------------------------------------- theme model
    def _profile_defaults(self, style: str) -> dict:
        base = theme.STYLE_BASE.get(style, "dark")
        primary, accent = theme.COLOR_PRESETS["blue"]
        if style in ("frosted", "html"):
            primary, accent = "#2563eb", "#7c3aed"
        return {
            "color": "blue",
            "primary": primary,
            "accent": accent,
            "base": base,
            "glass": style != "html",
            "system_blur": True,
            "blur": 26 if style == "liquid" else (34 if style == "frosted" else 0),
            "dim": 42 if style == "liquid" else (30 if style == "frosted" else 0),
            "background": "",
            "splash": "",
        }

    def _profile(self) -> dict:
        style = self._active_style
        profile = self._theme_profiles.get(style)
        if not isinstance(profile, dict):
            profile = self._profile_defaults(style)
            self._theme_profiles[style] = profile
        return profile

    def _save_profile(self) -> None:
        if not hasattr(self, "glass_check"):
            return
        style = self._active_style
        profile = self._profile()
        profile.update(
            {
                "color": str(self.color_preset.currentData() or "custom"),
                "primary": self._primary_color,
                "accent": self._accent_color,
                "base": theme.STYLE_BASE.get(style, "dark"),
                "glass": self.glass_check.isChecked(),
                "system_blur": self.system_blur_check.isChecked(),
                "blur": self.blur_slider.value(),
                "dim": self.dim_slider.value(),
                "background": self._background_path,
                "splash": self._splash_path,
            }
        )

    def _apply_profile(self) -> None:
        profile = self._profile()
        self._loading_settings = True
        try:
            index = self.style_combo.findData(self._active_style)
            self.style_combo.blockSignals(True)
            self.style_combo.setCurrentIndex(max(0, index))
            self.style_combo.blockSignals(False)
            self._primary_color = str(profile.get("primary") or "#4f8cff")
            self._accent_color = str(profile.get("accent") or "#7b5cff")
            self._set_color_button(self.primary_button, self._primary_color)
            self._set_color_button(self.accent_button, self._accent_color)
            color = str(profile.get("color") or "custom")
            ci = self.color_preset.findData(color)
            self.color_preset.blockSignals(True)
            if ci < 0:
                self.color_preset.addItem("自定义", "custom")
                ci = self.color_preset.findData("custom")
            self.color_preset.setCurrentIndex(max(0, ci))
            self.color_preset.blockSignals(False)
            self.glass_check.blockSignals(True)
            self.glass_check.setChecked(bool(profile.get("glass", True)))
            self.glass_check.blockSignals(False)
            self.system_blur_check.blockSignals(True)
            self.system_blur_check.setChecked(bool(profile.get("system_blur", True)))
            self.system_blur_check.blockSignals(False)
            self.blur_slider.blockSignals(True)
            self.blur_slider.setValue(int(profile.get("blur", 26) or 0))
            self.blur_slider.blockSignals(False)
            self.dim_slider.blockSignals(True)
            self.dim_slider.setValue(int(profile.get("dim", 42) or 0))
            self.dim_slider.blockSignals(False)
            self._background_path = _normalize_image_path(str(profile.get("background") or ""))
            self._splash_path = _normalize_image_path(str(profile.get("splash") or ""))
            self._update_background_preview()
            self._update_splash_preview()
            self._sync_appearance_widgets()
        finally:
            self._loading_settings = False

    def _set_color_button(self, button: QPushButton, color: str) -> None:
        value = QColor(color)
        text = "#ffffff" if value.lightness() < 150 else "#0f172a"
        button.setText(color.upper())
        button.setStyleSheet(
            f"QPushButton#ColorSwatch {{ background-color: {color}; color: {text}; "
            f"border: 1px solid rgba(255,255,255,0.25); border-radius: 8px; "
            f"padding: 6px 10px; font-weight: 600; }}"
        )

    def _on_style_changed(self, _index: int) -> None:
        if self._loading_settings:
            return
        style = str(self.style_combo.currentData() or "liquid")
        if style == self._active_style:
            return
        self._save_profile()
        self._active_style = style
        self._apply_profile()
        self.appearance_changed.emit()

    def _on_preset_changed(self, _index: int) -> None:
        if self._loading_settings:
            return
        key = str(self.color_preset.currentData() or "custom")
        if key == "custom":
            return
        base = theme.STYLE_BASE.get(self._active_style, "dark")
        self._primary_color, self._accent_color = theme.preset_colors(key, base)
        self._set_color_button(self.primary_button, self._primary_color)
        self._set_color_button(self.accent_button, self._accent_color)
        self._save_profile()
        self.appearance_changed.emit()

    def _pick_color(self, which: str) -> None:
        current = self._primary_color if which == "primary" else self._accent_color
        color = QColorDialog.getColor(QColor(current), self, "选择颜色")
        if not color.isValid():
            return
        if which == "primary":
            self._primary_color = color.name()
            self._set_color_button(self.primary_button, self._primary_color)
        else:
            self._accent_color = color.name()
            self._set_color_button(self.accent_button, self._accent_color)
        self.color_preset.blockSignals(True)
        ci = self.color_preset.findData("custom")
        if ci < 0:
            self.color_preset.addItem("自定义", "custom")
            ci = self.color_preset.findData("custom")
        self.color_preset.setCurrentIndex(max(0, ci))
        self.color_preset.blockSignals(False)
        self._save_profile()
        self.appearance_changed.emit()

    def _pick_primary(self) -> None:
        self._pick_color("primary")

    def _pick_accent(self) -> None:
        self._pick_color("accent")

    def _on_loading_toggled(self, _checked: bool) -> None:
        if not self._loading_settings:
            self.appearance_changed.emit()

    def _sync_appearance_widgets(self) -> None:
        enabled = self.glass_check.isChecked() or self.system_blur_check.isChecked()
        self.blur_slider.setEnabled(enabled)
        self.dim_slider.setEnabled(enabled)
        self.blur_value.setText(f"{self.blur_slider.value()}px")
        self.dim_value.setText(f"{self.dim_slider.value()}%")

    def _on_glass_toggled(self, _checked: bool) -> None:
        self._sync_appearance_widgets()
        if not self._loading_settings:
            self._save_profile()
            self.appearance_changed.emit()

    def _on_blur_changed(self, value: int) -> None:
        self.blur_value.setText(f"{value}px")
        if not self._loading_settings:
            self._save_profile()
            self.appearance_changed.emit()

    def _on_dim_changed(self, value: int) -> None:
        self.dim_value.setText(f"{value}%")
        if not self._loading_settings:
            self._save_profile()
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
        # Keep the real absolute path (D:/, E:/ ...); never let a path be
        # rebased against the C: working directory.
        path = _normalize_image_path(chosen)
        # Probe through the cache: the preview below then reuses this decode (U-03).
        probe = self._decode_image(path)
        if probe.isNull():
            QMessageBox.warning(
                self,
                "无法读取图片",
                "这个文件不是受支持的图片，或文件已经损坏。\n支持 JPG / PNG / WebP / BMP / GIF。",
            )
            return
        self._background_path = path
        self._update_background_preview()
        if not self.glass_check.isChecked() and not self.system_blur_check.isChecked():
            answer = QMessageBox.question(
                self,
                "启用毛玻璃模糊",
                "已设置自定义背景图。是否同时开启毛玻璃 / 系统背景模糊？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.glass_check.setChecked(True)
                self.system_blur_check.setChecked(True)
        self._save_profile()
        if not self._loading_settings:
            self.appearance_changed.emit()

    def _clear_background(self) -> None:
        if not self._background_path:
            return
        self._background_path = ""
        self._update_background_preview()
        self._save_profile()
        if not self._loading_settings:
            self.appearance_changed.emit()

    def _image_mtime(self, path: str) -> int:
        try:
            return int(os.stat(path).st_mtime_ns)
        except OSError:
            return 0

    def _decode_image(self, path: str) -> QPixmap:
        """Decode *path* once per (path, mtime); every preview reuses it."""
        if not path:
            return QPixmap()
        key = (path, self._image_mtime(path))
        cached = self._image_cache.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap(path)
        self._image_stats["decode"] += 1
        if len(self._image_cache) >= 8:
            self._image_cache.clear()
        self._image_cache[key] = pixmap
        return pixmap

    @staticmethod
    def _device_size(label: QLabel) -> tuple[QSize, float]:
        """Label size in device pixels plus the ratio that produced it."""
        size = label.size()
        ratio = float(label.devicePixelRatioF()) or 1.0
        return (
            QSize(
                max(1, int(round(size.width() * ratio))),
                max(1, int(round(size.height() * ratio))),
            ),
            ratio,
        )

    def _preview_pixmap(self, path: str, label: QLabel) -> QPixmap:
        """Centre-cropped preview rendered at the label's device pixel size.

        Scaling to logical pixels and letting Qt upscale the result made every
        preview soft on a 2x display (R-06), so the pixmap is built at
        size * devicePixelRatio and tagged with that ratio.
        """
        target, ratio = self._device_size(label)
        source = self._decode_image(path)
        if source.isNull():
            return QPixmap()
        key = (path, self._image_mtime(path), target.width(), target.height())
        cached = self._preview_cache.get(key)
        if cached is not None:
            return cached
        scaled = source.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - target.width()) // 2)
        y = max(0, (scaled.height() - target.height()) // 2)
        preview = scaled.copy(x, y, target.width(), target.height())
        preview.setDevicePixelRatio(ratio)
        self._image_stats["scale"] += 1
        if len(self._preview_cache) >= 8:
            self._preview_cache.clear()
        self._preview_cache[key] = preview
        return preview

    def _update_background_preview(self) -> None:
        path = self._background_path
        pixmap = self._decode_image(path) if path else QPixmap()
        preview = self._preview_pixmap(path, self.background_preview)
        if not pixmap.isNull() and not preview.isNull():
            self.background_preview.setPixmap(preview)
            self.background_label.setText(Path(path).name)
            self.background_label.setToolTip(path)
            self.background_meta.setText(
                f"{pixmap.width()}×{pixmap.height()} · 已应用，点击“选择背景…”可更换"
            )
            self.background_clear.setEnabled(True)
            return

        dark = theme.current_theme() != "light"
        target, ratio = self._device_size(self.background_preview)
        rect = self.background_preview.rect()
        thumb = QPixmap(target)
        thumb.fill(Qt.GlobalColor.transparent)
        thumb.setDevicePixelRatio(ratio)
        painter = QPainter(thumb)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(ratio, ratio)
        gradient = QLinearGradient(0, 0, rect.width(), rect.height())
        if dark:
            gradient.setColorAt(0.0, QColor("#12203c"))
            gradient.setColorAt(1.0, QColor("#2a1745"))
            text_colour = QColor(220, 232, 255, 170)
        else:
            gradient.setColorAt(0.0, QColor("#dce7ff"))
            gradient.setColorAt(1.0, QColor("#f3e8ff"))
            text_colour = QColor(51, 65, 85, 190)
        painter.fillRect(rect, gradient)
        painter.setPen(QPen(text_colour))
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "内置氛围背景")
        painter.end()
        self.background_preview.setPixmap(thumb)
        self.background_label.setText("内置氛围背景")
        self.background_label.setToolTip("")
        self.background_meta.setText("未选择图片 · 支持 JPG / PNG / WebP / GIF")
        self.background_clear.setEnabled(False)

    def _browse_splash(self) -> None:
        start = self._splash_path or str(Path.home() / "Pictures")
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "选择启动画面图片",
            start,
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)",
        )
        if not chosen:
            return
        path = _normalize_image_path(chosen)
        if self._decode_image(path).isNull():
            QMessageBox.warning(self, "无法读取图片", "这个文件不是受支持的图片。")
            return
        self._splash_path = path
        self._update_splash_preview()
        self._save_profile()
        if not self._loading_settings:
            self.appearance_changed.emit()

    def _clear_splash(self) -> None:
        self._splash_path = ""
        self._update_splash_preview()
        self._save_profile()
        if not self._loading_settings:
            self.appearance_changed.emit()

    def _update_splash_preview(self) -> None:
        path = self._splash_path
        preview = self._preview_pixmap(path, self.splash_preview)
        if not preview.isNull():
            self.splash_preview.setPixmap(preview)
            self.splash_label.setText(Path(path).name)
            self.splash_clear.setEnabled(True)
            return
        target, ratio = self._device_size(self.splash_preview)
        rect = self.splash_preview.rect()
        thumb = QPixmap(target)
        thumb.fill(Qt.GlobalColor.transparent)
        thumb.setDevicePixelRatio(ratio)
        painter = QPainter(thumb)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(ratio, ratio)
        gradient = QLinearGradient(0, 0, rect.width(), rect.height())
        gradient.setColorAt(0.0, QColor(self._primary_color))
        gradient.setColorAt(1.0, QColor(self._accent_color))
        painter.fillRect(rect, gradient)
        painter.setPen(QColor("#ffffff"))
        font = painter.font()
        font.setPointSize(18)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "C")
        painter.end()
        self.splash_preview.setPixmap(thumb)
        self.splash_label.setText("默认加载动画")
        self.splash_clear.setEnabled(False)

    def refresh_appearance_theme(self) -> None:
        self._update_background_preview()
        self._update_splash_preview()

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
        speed_row = QHBoxLayout()
        speed_row.setSpacing(10)
        speed_row.addWidget(_field("动画速度"))
        self.animation_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.animation_speed_slider.setRange(20, 250)
        self.animation_speed_slider.setValue(100)
        self.animation_speed_slider.setMinimumWidth(200)
        self.animation_speed_slider.valueChanged.connect(self._on_animation_speed_changed)
        speed_row.addWidget(self.animation_speed_slider, 1)
        self.animation_speed_label = QLabel("1.0x")
        self.animation_speed_label.setObjectName("SliderValue")
        self.animation_speed_label.setFixedWidth(54)
        speed_row.addWidget(self.animation_speed_label)
        panel.addLayout(speed_row)
        speed_hint = QLabel("范围 0.2x–2.5x；过快可能导致界面卡顿，过慢会影响操作。")
        speed_hint.setObjectName("Hint")
        speed_hint.setWordWrap(True)
        panel.addWidget(speed_hint)

        self.cached_login_check = QCheckBox(
            "断网时允许使用 30 天本地登录缓存（默认关闭，仅本次运行有效）"
        )
        self.cached_login_check.setChecked(False)
        self.cached_login_check.setToolTip(
            "关闭时，正版服务器不可达将无法启动；开启后才允许读取本地加密凭证。"
        )
        panel.addWidget(self.cached_login_check)
        cached_hint = QLabel(
            "安全提示：缓存登录仅用于断网单人调试；联网后仍会优先校验正版。"
        )
        cached_hint.setObjectName("Hint")
        cached_hint.setWordWrap(True)
        panel.addWidget(cached_hint)

        debug_title = QLabel("调试面板 · 仅开发调试用途，非普通用户功能")
        debug_title.setObjectName("SectionTitle")
        panel.addWidget(debug_title)
        debug_row = QHBoxLayout()
        debug_row.setSpacing(8)
        self.debug_all_button = QPushButton("一键模拟全部报错")
        self.debug_all_button.setObjectName("DangerGhost")
        self.debug_all_button.clicked.connect(lambda: self.debug_action.emit("all", ""))
        debug_row.addWidget(self.debug_all_button)
        self.debug_scenario = QComboBox()
        for key, label in (
            ("version_scan", "版本扫描失败"),
            ("config", "配置损坏"),
            ("java", "Java 缺失"),
            ("download", "下载校验失败"),
            ("mod", "Mod 前置缺失"),
            ("skin", "皮肤文件损坏"),
            ("update", "在线更新失败"),
            ("terracotta_create", "联机房间创建失败"),
            ("terracotta_join", "联机加入失败"),
        ):
            self.debug_scenario.addItem(label, key)
        debug_row.addWidget(self.debug_scenario)
        self.debug_single_button = QPushButton("单项模拟")
        self.debug_single_button.setObjectName("Ghost")
        self.debug_single_button.clicked.connect(
            lambda: self.debug_action.emit("single", str(self.debug_scenario.currentData() or ""))
        )
        debug_row.addWidget(self.debug_single_button)
        panel.addLayout(debug_row)
        debug_row2 = QHBoxLayout()
        debug_row2.setSpacing(8)
        for text, action in (
            ("重置调试状态", "reset"),
            ("导出调试日志", "export"),
            ("动画预览", "preview"),
            ("模拟缓存过期", "cache_expiry"),
        ):
            button = QPushButton(text)
            button.setObjectName("Ghost")
            button.clicked.connect(lambda _checked=False, a=action: self.debug_action.emit(a, ""))
            debug_row2.addWidget(button)
        debug_row2.addStretch(1)
        panel.addLayout(debug_row2)
        self.dev_skin_button = QPushButton("皮肤管理（开发调试）")
        self.dev_skin_button.setObjectName("Ghost")
        self.dev_skin_button.clicked.connect(self.skin_requested.emit)
        panel.addWidget(self.dev_skin_button)
        self.dev_multiplayer_button = QPushButton("联机（EasyTier）")
        self.dev_multiplayer_button.setObjectName("Ghost")
        self.dev_multiplayer_button.clicked.connect(
            self.multiplayer_requested.emit
        )
        panel.addWidget(self.dev_multiplayer_button)

        self.debug_status = QLabel("等待模拟…（仅开发调试用途）")
        self.debug_status.setObjectName("Hint")
        self.debug_status.setWordWrap(True)
        panel.addWidget(self.debug_status)

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
        self.dev_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        card.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
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

    def _open_third_party_notices(self, _link: str = "") -> None:
        notice = Path(__file__).resolve().parents[2] / "THIRD_PARTY_NOTICES.md"
        if not notice.is_file():
            self.log_requested.emit("warn", f"未找到第三方许可说明文件：{notice}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(notice)))

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

    def cached_login_enabled(self) -> bool:
        """Session-only developer switch; default off on every launch."""
        if not self._dev_unlocked:
            return False
        return bool(getattr(self, "cached_login_check", None) and self.cached_login_check.isChecked())

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

    def _on_animation_speed_changed(self, value: int) -> None:
        self.animation_speed = anim.set_speed(value / 100.0)
        self._update_animation_speed_label()
        if not self._loading_settings:
            self.animation_speed_changed.emit(self.animation_speed)

    def _update_animation_speed_label(self) -> None:
        if hasattr(self, "animation_speed_label"):
            self.animation_speed_label.setText(f"{self.animation_speed:.2f}x")

    def _toggle_developer(self, checked: bool) -> None:
        self.dev_panel.setVisible(checked)
        self.dev_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        if checked:
            self.dev_panel.adjustSize()
            self.dev_card.adjustSize()
            QTimer.singleShot(0, self.dev_card.adjustSize)
            QTimer.singleShot(0, lambda: self.scroll.widget().adjustSize())

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
        if run_in_window(self, box, "离线模式警告") != QMessageBox.StandardButton.Yes:
            self.offline_check.blockSignals(True)
            self.offline_check.setChecked(False)
            self.offline_check.blockSignals(False)
            self._offline_ack = False
        else:
            self._offline_ack = True
        self.developer_state_changed.emit()

    def set_theme_key(self, key: str) -> None:
        """Legacy API: map old dark/light keys onto the liquid style."""
        style = key if key in theme.STYLE_ORDER else "liquid"
        if style == self._active_style:
            return
        self._save_profile()
        self._active_style = style
        self._apply_profile()

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
        self._save_profile()
        active = self._profile()
        return {
            "game_dir": self._game_directory(),
            "java_path": self.java_path(),
            "max_mem": max_mem,
            "min_mem": min_mem,
            "player_name": self.player_edit.text().strip() or "Steve",
            "theme": str(active.get("base") or "dark"),
            "background_image": str(active.get("background") or ""),
            "glass_enabled": bool(active.get("glass", True)),
            "glass_blur": int(active.get("blur", 26) or 0),
            "background_dim": int(active.get("dim", 42) or 0),
            "ui_style": self._active_style,
            "theme_profiles": {k: dict(v) for k, v in self._theme_profiles.items()},
            "loading_animation": self.loading_check.isChecked(),
            "animation_speed": float(
                getattr(self, "animation_speed", 1.0)
            ),
            "auto_backup": self.auto_backup_check.isChecked(),
            "backup_keep": self.backup_keep_spin.value(),
            "update_repo": self.update_repo_edit.text().strip(),
            "auto_update_check": self.auto_update_check.isChecked(),
            "ai_model": self.ai_model_combo.currentText().strip() or "deepseek-chat",
            "jvm_preset": self.jvm_preset_combo.currentData() or "auto",
            "jvm_args": self._effective_jvm_args(),
        }

    def _game_directory(self) -> str:
        """Absolute game dir; an unusable path is warned about, never dropped."""
        raw = self.game_dir_edit.text().strip()
        if not raw:
            return ""
        normalized = _normalize_directory(raw)
        if normalized:
            return normalized
        if raw != self._warned_game_dir:
            self._warned_game_dir = raw
            self.flash_saved("游戏目录需要绝对路径，已保留原值")
            self.game_dir_edit.setToolTip(
                f"无法解析为绝对路径：{raw}\n请填写例如 D:/Minecraft 或 C:/Users/<你>/AppData/Roaming/.minecraft"
            )
            self.game_dir_edit.setText(raw)
        return raw

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
            profiles = getattr(config, "theme_profiles", {})
            self._theme_profiles = {
                key: dict(value) for key, value in profiles.items() if isinstance(value, dict)
            } if isinstance(profiles, dict) else {}
            style = str(getattr(config, "ui_style", "liquid") or "liquid")
            if style not in theme.STYLE_ORDER:
                style = "liquid"
            self._active_style = style
            self._apply_profile()
            self.loading_check.blockSignals(True)
            self.loading_check.setChecked(bool(getattr(config, "loading_animation", True)))
            self.loading_check.blockSignals(False)
            self.animation_speed = anim.clamp(getattr(config, "animation_speed", 1.0))
            if hasattr(self, "animation_speed_slider"):
                self.animation_speed_slider.blockSignals(True)
                self.animation_speed_slider.setValue(int(round(self.animation_speed * 100)))
                self.animation_speed_slider.blockSignals(False)
                self._update_animation_speed_label()
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


def _os_key() -> str:
    return {"win32": "windows", "darwin": "osx"}.get(sys.platform, "linux")


def _file_ok(path: Path, size: int = 0, sha1: str = "") -> bool:
    """Is path the complete file its metadata describes?

    A zero-byte or truncated jar is never "present"; the sha1 is only compared up
    to FULL_HASH_LIMIT so a repair cannot end up hashing gigabytes of libraries.
    """
    try:
        actual = path.stat().st_size
    except OSError:
        return False
    if actual <= 0:
        return False
    if size and actual != size:
        return False
    if sha1 and actual <= FULL_HASH_LIMIT:
        try:
            return net.file_sha1(path).lower() == sha1.lower()
        except OSError:
            return False
    return True


def _sha1_skipped(size: int, sha1: str) -> bool:
    """True when a declared sha1 was skipped because the file is very large."""
    return bool(sha1) and size > FULL_HASH_LIMIT


def _library_files(libraries) -> list[dict]:
    """Resolve library entries to the files they stand for under libraries/.

    Fabric-style entries only carry name/url, so the Maven path is derived from
    the coordinates; natives entries also contribute their classifier file.  The
    checks panel and the repair plan share this resolver, so both always agree
    with what actually lands in libraries/.
    """
    files: list[dict] = []
    seen: set[str] = set()
    os_name = _os_key()
    for library in libraries or []:
        if not isinstance(library, dict):
            continue
        name = str(library.get("name") or "")
        downloads = library.get("downloads") if isinstance(library.get("downloads"), dict) else {}
        artifact = downloads.get("artifact") if isinstance(downloads.get("artifact"), dict) else {}
        candidates: list[tuple[str, int, str, str]] = []
        if artifact.get("path"):
            candidates.append(
                (
                    str(artifact["path"]),
                    _int_or_zero(artifact.get("size")),
                    str(artifact.get("sha1") or ""),
                    str(artifact.get("url") or ""),
                )
            )
        natives = library.get("natives") if isinstance(library.get("natives"), dict) else {}
        classifier = str(natives.get(os_name) or "").replace("${arch}", "64")
        if classifier:
            classifiers = (
                downloads.get("classifiers")
                if isinstance(downloads.get("classifiers"), dict)
                else {}
            )
            entry = (
                classifiers.get(classifier)
                if isinstance(classifiers.get(classifier), dict)
                else {}
            )
            rel = str(entry.get("path") or "") or fabric_install.maven_path(
                f"{name}:{classifier}"
            )
            if rel:
                candidates.append(
                    (
                        rel,
                        _int_or_zero(entry.get("size")),
                        str(entry.get("sha1") or ""),
                        str(entry.get("url") or ""),
                    )
                )
        if not candidates and name:
            rel = fabric_install.maven_path(name)
            if rel:
                candidates.append((rel, 0, "", ""))
        for rel, size, sha1, url in candidates:
            if rel in seen:
                continue
            seen.add(rel)
            files.append(
                {
                    "rel": rel,
                    "size": size,
                    "sha1": sha1,
                    "url": url,
                    "base": str(library.get("url") or ""),
                }
            )
    return files


def _read_version_json(versions_dir: Path, version_id: str) -> dict:
    path = versions_dir / version_id / f"{version_id}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FileNotFoundError(f"缺少版本 JSON：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"版本 JSON 无法解析：{path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"版本 JSON 结构异常：{path}")
    return raw


def _version_chain(versions_dir: Path, version_id: str, own_raw: dict | None = None) -> list[dict]:
    """[child, parent, …] version JSONs, child first, at most INHERIT_MAX_DEPTH parents."""
    chain: list[dict] = []
    seen: set[str] = set()
    current = version_id
    while current and current not in seen and len(chain) <= INHERIT_MAX_DEPTH:
        seen.add(current)
        raw = own_raw if (not chain and own_raw) else _read_version_json(versions_dir, current)
        raw = dict(raw)
        raw.setdefault("id", current)
        chain.append(raw)
        current = str(raw.get("inheritsFrom") or "")
    return chain


def _merge_chain(chain: list[dict]) -> dict:
    """Child-wins merge of the chain; libraries keep child-to-parent order."""
    merged: dict = {}
    libraries: list = []
    seen_libs: set[str] = set()
    for raw in chain:
        for library in raw.get("libraries") or []:
            if not isinstance(library, dict):
                continue
            key = str(library.get("name") or json.dumps(library, sort_keys=True))
            if key in seen_libs:
                continue
            seen_libs.add(key)
            libraries.append(library)
        for key, value in raw.items():
            if key in ("id", "inheritsFrom", "libraries"):
                continue
            if key not in merged and value:
                merged[key] = value
    merged["libraries"] = libraries
    return merged


def _asset_targets(mc_dir: Path, index_file: Path) -> list[dict]:
    """Asset objects declared by an asset index already on disk."""
    try:
        data = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    objects = data.get("objects") if isinstance(data, dict) else None
    if not isinstance(objects, dict):
        return []
    targets: list[dict] = []
    for entry in objects.values():
        if not isinstance(entry, dict):
            continue
        digest = str(entry.get("hash") or "")
        if len(digest) < 2:
            continue
        targets.append(
            {
                "dest": mc_dir / "assets" / "objects" / digest[:2] / digest,
                "url": f"{ASSET_RESOURCE_BASE}/{digest[:2]}/{digest}",
                "size": _int_or_zero(entry.get("size")),
                "sha1": digest,
                "label": f"assets/{digest[:2]}/{digest}",
            }
        )
    return targets


def _repair_targets(mc_dir: Path, versions_dir: Path, chain: list[dict]) -> tuple[list[dict], Path]:
    """Every file a complete install needs, plus the asset index path."""
    targets: list[dict] = []
    for raw in chain:
        downloads = raw.get("downloads") if isinstance(raw.get("downloads"), dict) else {}
        client = downloads.get("client") if isinstance(downloads.get("client"), dict) else {}
        url = str(client.get("url") or "")
        if not url:
            continue
        owner = str(raw.get("id") or "")
        targets.append(
            {
                "dest": versions_dir / owner / f"{owner}.jar",
                "url": url,
                "size": _int_or_zero(client.get("size")),
                "sha1": str(client.get("sha1") or ""),
                "label": f"versions/{owner}/{owner}.jar",
            }
        )
        break

    merged = _merge_chain(chain)
    index = merged.get("assetIndex") if isinstance(merged.get("assetIndex"), dict) else {}
    index_id = str(index.get("id") or "")
    index_file = mc_dir / "assets" / "indexes" / f"{index_id}.json"
    if index_id and index.get("url"):
        targets.append(
            {
                "dest": index_file,
                "url": str(index["url"]),
                "size": _int_or_zero(index.get("size")),
                "sha1": str(index.get("sha1") or ""),
                "label": f"assets/indexes/{index_id}.json",
            }
        )
    for item in _library_files(merged.get("libraries") or []):
        url = item["url"]
        if not url and item["base"]:
            url = item["base"].rstrip("/") + "/" + item["rel"]
        if not url:
            url = FABRIC_MAVEN + item["rel"]
        targets.append(
            {
                "dest": mc_dir / "libraries" / item["rel"],
                "url": url,
                "size": item["size"],
                "sha1": item["sha1"],
                "label": f"libraries/{item['rel']}",
            }
        )
    return targets, index_file


def _repair_local_files(
    mc_dir: Path,
    versions_dir: Path,
    version_id: str,
    own_raw: dict | None,
    progress,
    cancel,
) -> dict:
    """Fetch whatever a (possibly inherited) version is missing, locally.

    The kernel only installs ids from the Mojang manifest and refuses profiles
    with inheritsFrom, so Fabric-style profiles are completed here: the child and
    every parent JSON are merged and the missing files are downloaded one by one,
    skipping anything already valid on disk.
    """
    chain = _version_chain(versions_dir, version_id, own_raw)
    targets, index_file = _repair_targets(mc_dir, versions_dir, chain)
    downloaded = 0
    skipped = 0
    failed = 0
    notes: list[str] = []
    assets_expanded = False

    def expand_assets() -> None:
        nonlocal assets_expanded
        if assets_expanded:
            return
        assets_expanded = True
        targets.extend(_asset_targets(mc_dir, index_file))

    if index_file.is_file():
        expand_assets()

    processed = 0
    while targets:
        task = targets.pop(0)
        processed += 1
        if cancel is not None and cancel():
            raise RuntimeError("已取消补全")
        if progress is not None:
            progress(f"检查 {task['label']}", processed, processed + len(targets))
        if _file_ok(task["dest"], task["size"], task["sha1"]):
            skipped += 1
            continue
        if not task["url"]:
            failed += 1
            notes.append(f"{task['label']}：缺少下载地址")
            continue
        try:
            net.download(
                task["url"],
                task["dest"],
                expected_sha1=task["sha1"] or None,
                expected_size=task["size"] or None,
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the rest
            failed += 1
            notes.append(f"{task['label']}：{exc}")
            continue
        downloaded += 1
        if task["dest"] == index_file:
            expand_assets()
    return {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "notes": notes,
    }


CATALOG_BATCH = 20
CATALOG_ROW_HEIGHT = 66


def _catalog_label(entry) -> str:
    label = entry.id
    if entry.type == "snapshot":
        label += "  · 快照"
    elif entry.type in ("old_beta", "old_alpha"):
        label += "  · 旧版"
    return label


def _catalog_matches(entry, mode: str, query: str) -> bool:
    if mode == "old":
        if entry.type not in ("old_beta", "old_alpha"):
            return False
    elif mode != "all" and entry.type != mode:
        return False
    if query and query not in entry.id.lower():
        return False
    return True


class VersionListModel(QAbstractListModel):
    """Every catalog entry, filtered up front and revealed one batch at a time."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all: list = []
        self._rows: list = []
        self._loaded = 0
        self._mode = "all"
        self._query = ""

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return min(self._loaded, len(self._rows))

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid() or index.row() >= self.rowCount():
            return None
        entry = self._rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return entry
        if role == Qt.ItemDataRole.DisplayRole:
            return _catalog_label(entry)
        return None

    def reset_entries(self, entries, mode: str, query: str) -> None:
        self.beginResetModel()
        self._all = list(entries or [])
        self._mode = str(mode or "all")
        self._query = str(query or "").strip().lower()
        self._rows = [
            entry
            for entry in self._all
            if _catalog_matches(entry, self._mode, self._query)
        ]
        self._loaded = min(CATALOG_BATCH, len(self._rows))
        self.endResetModel()

    def load_more(self) -> int:
        if not self.has_more():
            return self.loaded_count()
        start = self._loaded
        end = min(self._loaded + CATALOG_BATCH, len(self._rows))
        self.beginInsertRows(QModelIndex(), start, end - 1)
        self._loaded = end
        self.endInsertRows()
        return self._loaded

    def has_more(self) -> bool:
        return self._loaded < len(self._rows)

    def loaded_count(self) -> int:
        return min(self._loaded, len(self._rows))

    def total_count(self) -> int:
        return len(self._rows)

    def entry_at(self, row: int):
        if 0 <= row < self.rowCount():
            return self._rows[row]
        return None

    def row_of(self, entry_id: str) -> int:
        target = str(entry_id or "")
        if not target:
            return -1
        for row, entry in enumerate(self._rows):
            if str(getattr(entry, "id", "") or "") == target:
                return row
        return -1

    def ensure_loaded(self, row: int) -> None:
        while self._loaded <= row and self.has_more():
            self.load_more()


class InstallPage(QWidget):
    log_requested = Signal(str, str)
    install_requested = Signal(str)
    browse_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mc_dir: Path | None = None
        self.info: dict | None = None
        self.version_dir: Path | None = None
        self._catalog_entries: list = []
        self._selected_entry_id = ""
        self._backfilled_id = ""
        self._tools_available = False
        self._installing = False
        self._repairing = False
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

        # No in-card heading: the tab bar already names this section, and the
        # duplicate title stole a whole row of vertical space.
        catalog_card, catalog_layout = _card()
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.catalog_filter = QComboBox()
        self.catalog_filter.addItem("全部版本", "all")
        self.catalog_filter.addItem("正式版", "release")
        self.catalog_filter.addItem("快照", "snapshot")
        self.catalog_filter.addItem("旧版", "old")
        self.catalog_filter.setFixedWidth(120)
        self.catalog_filter.currentIndexChanged.connect(self._fill_catalog)
        filter_row.addWidget(self.catalog_filter)
        self.catalog_search = QLineEdit()
        self.catalog_search.setPlaceholderText("搜索版本号…")
        self.catalog_search.textChanged.connect(self._fill_catalog)
        filter_row.addWidget(self.catalog_search, 1)
        catalog_layout.addLayout(filter_row)

        # Two rows instead of one: a single row of three buttons hard-codes a
        # minimum width the column cannot shrink below.
        catalog_actions = QHBoxLayout()
        catalog_actions.setSpacing(8)
        self.catalog_refresh = QPushButton("刷新列表")
        self.catalog_refresh.setObjectName("Ghost")
        self.catalog_refresh.clicked.connect(self.load_catalog)
        catalog_actions.addWidget(self.catalog_refresh)
        self.catalog_browse = QPushButton("全屏浏览")
        self.catalog_browse.setObjectName("Ghost")
        self.catalog_browse.clicked.connect(self.browse_requested.emit)
        catalog_actions.addWidget(self.catalog_browse)
        catalog_actions.addStretch(1)
        catalog_layout.addLayout(catalog_actions)

        install_actions = QHBoxLayout()
        install_actions.setSpacing(8)
        self.catalog_install = QPushButton("安装选中版本")
        self.catalog_install.setObjectName("Primary")
        self.catalog_install.setEnabled(False)
        self.catalog_install.clicked.connect(self._install_catalog_selection)
        install_actions.addWidget(self.catalog_install)
        install_actions.addStretch(1)
        catalog_layout.addLayout(install_actions)

        # A model/view pair instead of stacked items: the model owns all entries
        # and reveals them batch by batch, so thousands of versions never become
        # thousands of widgets.
        self.catalog_model = VersionListModel(self)
        self.catalog_list = QListView()
        self.catalog_list.setObjectName("CatalogList")
        # The list scrolls its own contents; keep the minimum small so column 1
        # never forces the window-level layout to grow.
        self.catalog_list.setMinimumHeight(260)
        self.catalog_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.catalog_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.catalog_list.setSpacing(1)
        self.catalog_list.setUniformItemSizes(True)
        self.catalog_list.setMouseTracking(True)
        self.catalog_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.catalog_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.catalog_list.setModel(self.catalog_model)
        self.catalog_list.setItemDelegate(VersionCardDelegate(self.catalog_list))
        self.catalog_list.selectionModel().selectionChanged.connect(
            self._on_catalog_selected
        )
        self.catalog_list.activated.connect(
            lambda _index: self._install_catalog_selection()
        )
        self.catalog_list.verticalScrollBar().valueChanged.connect(
            self._maybe_load_more
        )
        catalog_layout.addWidget(self.catalog_list, 1)
        self.catalog_status = QLabel("打开此页后自动加载 Mojang 官方版本清单")
        self.catalog_status.setObjectName("Hint")
        self.catalog_status.setWordWrap(True)
        catalog_layout.addWidget(self.catalog_status)

        install_card, install_layout = _card()
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

        self.install_skip_note = QLabel(
            "本地已存在文件会自动跳过：版本 JSON、依赖库、资源文件与客户端 JAR "
            "只要磁盘上已完整存在就不会重复下载；中断后再次点击「开始下载」即可续传。"
        )
        self.install_skip_note.setObjectName("Hint")
        self.install_skip_note.setWordWrap(True)
        install_layout.addWidget(self.install_skip_note)
        install_layout.addStretch(1)

        self.stack = QStackedWidget()
        # Minimum vertical policy keeps the stack at its size hint height, so the
        # column scroll area scrolls it instead of clipping the validation list.
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )

        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(0, 12, 0, 0)
        empty = QLabel("在版本列表中选择一个已安装版本，可查看本地文件校验结果")
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
        self.version_name.setWordWrap(True)
        summary_layout.addWidget(self.version_name)
        self.version_meta = QLabel("")
        self.version_meta.setObjectName("Muted")
        self.version_meta.setWordWrap(True)
        # Both must wrap instead of dictating a hard minimum width for the column.
        for label in (self.version_name, self.version_meta):
            label.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
        summary_layout.addWidget(self.version_meta)
        self.detail_labels: dict[str, QWidget] = {}
        detail_form = QFormLayout()
        detail_form.setHorizontalSpacing(18)
        detail_form.setVerticalSpacing(5)
        for key, label in (("type", "版本类型"), ("main_class", "主类"), ("path", "版本目录")):
            if key == "main_class":
                # A class name is one long token with no break opportunity, so a
                # wrapping label would truncate it. A frameless read-only field
                # keeps the whole value selectable/scrollable and stays tiny.
                value = QLineEdit("—")
                value.setReadOnly(True)
                value.setFrame(False)
                value.setStyleSheet("background: transparent; border: none; padding: 0;")
            else:
                value = QLabel("—")
                value.setWordWrap(True)
                # Long paths must wrap inside the card instead of dictating a
                # hard minimum width for this column.
                value.setSizePolicy(
                    QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
                )
                value.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
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

        note = QLabel("安装完成后建议重新校验")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        checks_layout.addWidget(note)
        content.addWidget(checks_card)
        content.addStretch(1)
        self.stack.addWidget(self.content_page)

        tools_card, tools_layout = _card()
        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        self.tool_validate = QPushButton("校验版本文件")
        self.tool_validate.setObjectName("Ghost")
        self.tool_validate.setEnabled(False)
        self.tool_validate.clicked.connect(self.validate)
        tool_row.addWidget(self.tool_validate)
        self.tool_repair = QPushButton("补全缺失文件")
        self.tool_repair.setObjectName("Primary")
        self.tool_repair.setEnabled(False)
        self.tool_repair.clicked.connect(self._repair_version)
        tool_row.addWidget(self.tool_repair)
        tool_row.addStretch(1)
        tools_layout.addLayout(tool_row)

        tool_open_row = QHBoxLayout()
        tool_open_row.setSpacing(8)
        self.tool_open = QPushButton("打开版本目录")
        self.tool_open.setObjectName("Ghost")
        self.tool_open.setEnabled(False)
        self.tool_open.clicked.connect(self.open_version_dir)
        tool_open_row.addWidget(self.tool_open)
        tool_open_row.addStretch(1)
        tools_layout.addLayout(tool_open_row)

        self.tool_status = QLabel("未选择版本：请先在「浏览官方版本」列表中选择一个已安装版本")
        self.tool_status.setObjectName("Hint")
        self.tool_status.setWordWrap(True)
        tools_layout.addWidget(self.tool_status)
        tools_layout.addWidget(self.stack, 1)

        # Exactly one scroll area per tab and no card wrapped twice: the tab card
        # scrolls on its own, and no two scroll areas are ever nested.
        self.catalog_scroll = _tab_scroll(catalog_card, "CatalogScroll", 260)
        self.install_scroll = _tab_scroll(install_card, "InstallScroll", 260)
        self.tools_scroll = _tab_scroll(tools_card, "InstallContentScroll", 260)

        # The same plain QTabWidget the 百宝箱 uses: every card look comes from the
        # global QSS, so no local styling is added and nothing can drift.
        self.tabs = QTabWidget()
        self.tabs.setObjectName("ToolTabs")
        tab_bar = self.tabs.tabBar()
        # A pill bar must hug its labels: expanding stretches each tab across the
        # whole row, scroll buttons add arrows, and the base line draws under the
        # selected pill.
        tab_bar.setExpanding(False)
        tab_bar.setUsesScrollButtons(False)
        tab_bar.setDrawBase(False)
        for page, label in (
            (self.catalog_scroll, "浏览官方版本"),
            (self.install_scroll, "官方源安装"),
            (self.tools_scroll, "版本辅助工具"),
        ):
            self.tabs.addTab(page, label)
        outer.addWidget(self.tabs, 1)
        self.loading = LoadingOverlay(self)
        # Apply the tool-button state once, so validation is clickable (and can
        # answer with a prompt) even before a version is ever selected.
        self._sync_tools_enabled()

    # -------------------------------------------------------------- install UI
    def _set_tools_enabled(self, available: bool) -> None:
        self._tools_available = bool(available)
        self._sync_tools_enabled()

    def _idle_tools_text(self) -> str:
        """Neutral tool-card line for the current selection."""
        if not getattr(self, "_tools_available", False):
            return "未选择版本：请先在「浏览官方版本」列表中选择一个已安装版本"
        version_id = str((self.info or {}).get("id") or "—")
        return f"当前版本 {version_id}：可校验文件完整性或补全缺失文件"

    def _sync_tools_enabled(self) -> None:
        """One place decides the three tool buttons: version present and idle.

        A download or a repair disables them on top of the "no version selected"
        dimension, so neither state can re-enable a button the other disabled.
        """
        available = bool(getattr(self, "_tools_available", False))
        busy = bool(getattr(self, "_installing", False)) or bool(
            getattr(self, "_repairing", False)
        )
        # Validation stays clickable without a selected version so a click can
        # answer with a prompt instead of doing nothing at all.
        self.tool_validate.setEnabled(not busy)
        for button in (self.tool_repair, self.tool_open):
            button.setEnabled(available and not busy)
        if busy:
            self.tool_status.setText("正在下载 / 补全…校验、补全与打开目录暂时不可用")
        else:
            self.tool_status.setText(self._idle_tools_text())

    def cancel_pending_operation(self, text: str = "") -> None:
        """Return the tool card to a neutral state (public, idempotent).

        main_window calls this when an install request is refused, so a stale
        "正在补全 …" line and a running/indeterminate progress bar never outlive
        the request that produced them.  An installation that is still running
        keeps the buttons disabled; set_installing(False) finishes that off.
        """
        self._repairing = False
        bar = self.install_progress
        unsettled = (
            bar.maximum() != 100
            or bar.value() != 0
            or bool(getattr(bar, "_shimmer", False))
        )
        if unsettled:
            bar.set_shimmer(False)
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("%p%")
        self._sync_tools_enabled()
        self.tool_status.setText(text or self._idle_tools_text())

    def _own_raw(self) -> dict:
        """The version JSON this page was handed, read from disk when missing."""
        raw = self.info.get("raw") if isinstance(self.info, dict) else None
        if isinstance(raw, dict) and raw:
            return raw
        if not self.mc_dir or not self.info:
            return {}
        version_id = str(self.info.get("id") or "")
        if not version_id:
            return {}
        try:
            return _read_version_json(self.mc_dir / "versions", version_id)
        except (OSError, ValueError):
            return {}

    def _repair_version(self) -> None:
        if not self.info:
            self.tool_status.setText("请先在「浏览官方版本」列表中选择一个已安装版本")
            return
        version_id = str(self.info.get("id") or "")
        if not version_id or self._repairing:
            return
        self.log_requested.emit("info", f"[辅助工具] 请求补全版本文件：{version_id}")
        own_raw = self._own_raw()
        if not str(own_raw.get("inheritsFrom") or ""):
            # A plain official version: the kernel install chain already covers it.
            self.tool_status.setText(f"正在补全 {version_id} 的缺失文件…")
            self.install_requested.emit(version_id)
            return
        if not self.mc_dir:
            self.tool_status.setText("缺少游戏目录，无法本地补全版本文件")
            return
        mc_dir = Path(self.mc_dir)
        versions_dir = mc_dir / "versions"
        self._repairing = True
        self._sync_tools_enabled()
        self.tool_status.setText(f"正在补全 {version_id} 的缺失文件…")
        self.install_progress.set_shimmer(True)
        self.install_progress.setRange(0, 0)
        self.install_progress.setFormat("补全中…")
        self.log_requested.emit(
            "info", f"[辅助工具] 本地补全 {version_id}：合并继承版本的依赖后逐个补齐缺失文件"
        )
        start_task(
            self,
            lambda progress, cancel: _repair_local_files(
                mc_dir, versions_dir, version_id, own_raw, progress, cancel
            ),
            on_done=self._on_repair_done,
            on_failed=self._on_repair_failed,
            on_progress=self._on_repair_progress,
        )

    def _on_repair_progress(self, message: str, done: int, total: int) -> None:
        self.install_progress.set_shimmer(True)
        if total > 0:
            value = max(0, min(done, total))
            self.install_progress.setRange(0, total)
            self.install_progress.setValue(value)
            self.install_progress.setFormat(f"{value} / {total}")
        else:
            self.install_progress.setRange(0, 0)
            self.install_progress.setFormat("补全中…")
        self.tool_status.setText(message)

    def _reset_repair_state(self) -> None:
        """Always leave 正在补全 behind, whatever the task outcome was."""
        self._repairing = False
        self.install_progress.set_shimmer(False)
        self.install_progress.setRange(0, 100)
        self.install_progress.setValue(0)
        self.install_progress.setFormat("%p%")
        self._sync_tools_enabled()

    def _on_repair_done(self, result) -> None:
        self._reset_repair_state()
        data = result if isinstance(result, dict) else {}
        downloaded = _int_or_zero(data.get("downloaded"))
        skipped = _int_or_zero(data.get("skipped"))
        failed = _int_or_zero(data.get("failed"))
        summary = f"补全完成：下载 {downloaded} 项 · 已存在跳过 {skipped} 项"
        if failed:
            summary += f" · 失败 {failed} 项"
        self.tool_status.setText(summary)
        self.log_requested.emit("warn" if failed else "success", summary)
        for note in (data.get("notes") or [])[:5]:
            self.log_requested.emit("warn", f"[辅助工具] {note}")
        self.run_checks()

    def _on_repair_failed(self, message: str) -> None:
        self._reset_repair_state()
        self.tool_status.setText(f"补全失败：{message}")
        self.log_requested.emit("error", f"[辅助工具] 本地补全失败：{message}")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        loading = getattr(self, "loading", None)
        if loading is not None:
            loading.setGeometry(self.rect())
        if getattr(self, "catalog_model", None) is not None:
            # A taller viewport shows more rows, so it needs more loaded batches.
            QTimer.singleShot(0, self._maybe_load_more)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_catalog_entries", None) and not getattr(
            self, "_catalog_loading", False
        ):
            QTimer.singleShot(50, self.load_catalog)

    def load_catalog(self) -> None:
        if getattr(self, "_catalog_loading", False):
            return
        self._catalog_loading = True
        self.catalog_refresh.setEnabled(False)
        self.loading.show_loading("正在加载官方版本 JSON…")
        self.catalog_status.setText("正在获取 Mojang 官方版本清单…")
        start_task(
            self,
            lambda progress, cancel: catalog.fetch_versions(),
            on_done=self._on_catalog_loaded,
            on_failed=self._on_catalog_failed,
        )

    def _on_catalog_loaded(self, entries) -> None:
        self._catalog_loading = False
        self.catalog_refresh.setEnabled(True)
        self.loading.stop()
        self._catalog_entries = list(entries or [])
        self._fill_catalog()
        self.catalog_status.setText(f"已加载 {len(self._catalog_entries)} 个官方版本")

    def _on_catalog_failed(self, message: str) -> None:
        self._catalog_loading = False
        self.catalog_refresh.setEnabled(True)
        self.loading.stop()
        self.catalog_status.setText(f"获取版本清单失败：{message}")
        self.log_requested.emit("warn", f"获取 Mojang 版本清单失败：{message}")

    def _fill_catalog(self) -> None:
        entries = getattr(self, "_catalog_entries", [])
        mode = str(self.catalog_filter.currentData() or "all")
        query = self.catalog_search.text().strip().lower()
        previous = getattr(self, "_selected_entry_id", "")
        selection = self.catalog_list.selectionModel()
        # Rebuilding the model drops the selection transiently; silencing it keeps
        # the restored selection from flickering through an empty state.
        selection.blockSignals(True)
        try:
            self.catalog_model.reset_entries(entries, mode, query)
        finally:
            selection.blockSignals(False)
        # A rebuilt filter is a new list: start the reader at its first batch.
        self.catalog_list.scrollToTop()
        self.catalog_install.setEnabled(False)
        # Selection and the backfilled version id must stay one-way consistent: a
        # filter that hides the selected version must not leave its id behind in
        # the install field, or 开始下载 would install a version nobody can see (C3).
        restored = bool(previous) and self._restore_selection(previous)
        if not restored:
            self._drop_stale_selection()
        if entries and not restored:
            self.catalog_status.setText(
                f"显示 {self.catalog_model.total_count()} / {len(entries)} 个版本"
            )
        QTimer.singleShot(0, self._maybe_load_more)

    def _catalog_capacity(self) -> int:
        """How many rows the viewport can show, plus one as the scroll-ahead."""
        viewport = self.catalog_list.viewport()
        height = viewport.height() if viewport is not None else 0
        return max(1, int(height) // CATALOG_ROW_HEIGHT) + 1

    def _maybe_load_more(self) -> None:
        """Top up the model: fill the viewport, then one batch per scroll to the end."""
        model = getattr(self, "catalog_model", None)
        if model is None:
            return
        capacity = self._catalog_capacity()
        if model.loaded_count() < capacity:
            guard = 0
            while (
                model.has_more()
                and model.loaded_count() < capacity
                and guard < 64
            ):
                model.load_more()
                guard += 1
            return
        bar = self.catalog_list.verticalScrollBar()
        if (
            model.has_more()
            and bar.maximum() > 0
            and bar.value() >= bar.maximum() - CATALOG_ROW_HEIGHT
        ):
            model.load_more()

    def _restore_selection(self, entry_id: str) -> bool:
        """Re-select entry_id after a refill; False when it is no longer listed."""
        row = self.catalog_model.row_of(entry_id)
        if row < 0:
            return False
        self.catalog_model.ensure_loaded(row)
        index = self.catalog_model.index(row, 0)
        if not index.isValid():
            return False
        self.catalog_list.setCurrentIndex(index)
        return True

    def _drop_stale_selection(self) -> None:
        """Forget a version id the list cannot show any more (never a ghost id)."""
        text = self.install_version_edit.text().strip()
        if self._backfilled_id and text == self._backfilled_id:
            self.install_version_edit.setText("")
        self._backfilled_id = ""

    def _on_catalog_selected(self, *_args) -> None:
        indexes = self.catalog_list.selectionModel().selectedIndexes()
        if not indexes:
            self._selected_entry_id = ""
            self.catalog_install.setEnabled(False)
            return
        entry = self.catalog_model.entry_at(indexes[0].row())
        if entry is None:
            return
        self._selected_entry_id = str(entry.id)
        self._backfilled_id = str(entry.id)
        self.install_version_edit.setText(entry.id)
        self.catalog_install.setEnabled(True)
        self.catalog_status.setText(f"已选择 {entry.id} · {entry.release_time[:10]}")

    def _install_catalog_selection(self) -> None:
        indexes = self.catalog_list.selectionModel().selectedIndexes()
        if not indexes:
            return
        entry = self.catalog_model.entry_at(indexes[0].row())
        if entry is None:
            return
        self.install_version_edit.setText(entry.id)
        self.install_requested.emit(entry.id)

    def _emit_install(self) -> None:
        version_id = self.install_version_edit.text().strip()
        if not version_id:
            QMessageBox.information(self, "缺少版本号", "请输入 Mojang 官方版本号，例如 1.20.1。")
            return
        self.install_requested.emit(version_id)

    def set_installing(self, running: bool, version: str = "") -> None:
        self._installing = bool(running)
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
            # Zero the bar explicitly: leaving the last 100% behind made a finished
            # download look like it was still running (C2).
            self.install_progress.setRange(0, 100)
            self.install_progress.setValue(0)
            self.install_progress.setFormat("%p%")
        # The tool buttons follow the download: repairing/validating a version that
        # is being written would race the installer (C2).
        self._sync_tools_enabled()
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
            # Keep the directory and drop the stale version path with it (C4).
            self.mc_dir = Path(mc_dir) if mc_dir else None
            self.info = None
            self.version_dir = None
            self.stack.setCurrentWidget(self.empty_page)
            self._set_tools_enabled(False)
            return
        self.mc_dir = Path(mc_dir)
        self.info = info
        version_id = str(info.get("id", "—"))
        self.version_dir = self.mc_dir / "versions" / version_id
        raw = info.get("raw") if isinstance(info.get("raw"), dict) else {}
        vtype = str(raw.get("type") or info.get("type") or "release")

        self.version_name.setText(version_id)
        self.version_meta.setText("Minecraft 版本 · " + TYPE_LABELS.get(vtype, vtype))
        detail_values = {
            "type": TYPE_LABELS.get(vtype, vtype),
            "main_class": str(raw.get("mainClass") or "—"),
            "path": str(self.version_dir),
        }
        for key, text in detail_values.items():
            self.detail_labels[key].setText(text)
            self.detail_labels[key].setToolTip(text)
        self.stack.setCurrentWidget(self.content_page)
        bar = self.tools_scroll.verticalScrollBar()
        if bar is not None:
            bar.setValue(0)
        self._set_tools_enabled(True)
        self.run_checks()

    # ----------------------------------------------------------------- checks
    def _raw_for(self, versions_dir: Path, version_id: str) -> dict:
        try:
            return _read_version_json(versions_dir, version_id)
        except (OSError, ValueError):
            return {}

    def _version_libraries(self, versions_dir: Path, version_id: str, raw: dict) -> list:
        """Libraries the version really needs: its own plus every inherited parent."""
        own = raw.get("libraries") if isinstance(raw.get("libraries"), list) else []
        if not raw:
            return own
        try:
            chain = _version_chain(versions_dir, version_id, raw)
        except (OSError, ValueError):
            return own
        if len(chain) == 1:
            return own
        return _merge_chain(chain).get("libraries") or own

    def _client_jar_check(self, versions_dir: Path, version_id: str, raw: dict) -> dict:
        """Client JAR check that follows inheritsFrom like the kernel does (B3)."""
        current = version_id
        seen: set[str] = set()
        for _ in range(INHERIT_MAX_DEPTH + 1):
            if not current or current in seen:
                break
            seen.add(current)
            jar = versions_dir / current / f"{current}.jar"
            if jar.is_file():
                owner_raw = raw if current == version_id else self._raw_for(versions_dir, current)
                downloads = (
                    owner_raw.get("downloads")
                    if isinstance(owner_raw.get("downloads"), dict)
                    else {}
                )
                client = (
                    downloads.get("client")
                    if isinstance(downloads.get("client"), dict)
                    else {}
                )
                # A zero-byte or truncated jar is present but not usable (B4).
                ok = _file_ok(
                    jar,
                    _int_or_zero(client.get("size")),
                    str(client.get("sha1") or ""),
                )
                detail = jar.name
                if current != version_id:
                    detail = f"{jar.name} · 回退父版本 {current}"
                return {"title": "客户端 JAR", "detail": detail, "state": "ok" if ok else "bad"}
            parent = raw if current == version_id else self._raw_for(versions_dir, current)
            current = str(parent.get("inheritsFrom") or "")
        return {"title": "客户端 JAR", "detail": f"{version_id}.jar", "state": "bad"}

    def _library_check(self, libraries: list) -> dict:
        """Library count that matches what lands in libraries/ (B2)."""
        files = _library_files(libraries)
        expected = len(files)
        if expected == 0:
            return {"title": "依赖库", "detail": "版本 JSON 未声明依赖库", "state": "warn"}
        present = 0
        broken = 0
        large_only = False
        for item in files:
            path = self.mc_dir / "libraries" / item["rel"]
            if _sha1_skipped(item["size"], item["sha1"]):
                large_only = True
            if _file_ok(path, item["size"], item["sha1"]):
                present += 1
            elif path.is_file():
                # Present but empty/truncated/hash-mismatched: not "already there".
                broken += 1
        detail = f"{present} / {expected} 已存在"
        if broken:
            detail += f" · {broken} 个损坏或截断"
        if large_only:
            detail += " · 超大文件仅比对大小"
        return {
            "title": "依赖库",
            "detail": detail,
            "state": "ok" if present == expected else "bad",
        }

    def _collect_checks(self) -> list[dict]:
        checks: list[dict] = []
        if not self.version_dir or not self.mc_dir or not self.info:
            return checks
        version_id = str(self.info.get("id", ""))
        versions_dir = self.mc_dir / "versions"
        manifest = self.version_dir / f"{version_id}.json"
        raw = self.info.get("raw") if isinstance(self.info.get("raw"), dict) else {}
        if not raw:
            raw = self._raw_for(versions_dir, version_id)
        libraries = self._version_libraries(versions_dir, version_id, raw)

        checks.append(
            {
                "title": "版本清单",
                "detail": manifest.name,
                "state": "ok" if manifest.is_file() else "bad",
            }
        )
        checks.append(self._client_jar_check(versions_dir, version_id, raw))
        checks.append(self._library_check(libraries))
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
        try:
            checks = self._collect_checks()
        except Exception as exc:  # noqa: BLE001 - reported to the user below
            self.log_requested.emit("error", f"校验本地文件时出错：{exc}")
            checks = [
                {
                    "title": "校验失败",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "state": "bad",
                }
            ]
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
            detail.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            detail.setToolTip(check["detail"])
            line.addWidget(detail, 1)
            self.checks_layout.addWidget(row)
        return checks

    def validate(self) -> None:
        """Check the local files and always answer with something visible."""
        if not self.info:
            message = "请先在「浏览官方版本」列表中选择一个已安装版本，然后再校验文件。"
            self.log_requested.emit("warn", "校验文件被跳过：当前没有选中版本。")
            self._show_result("无法校验", message, "")
            return
        version_id = str(self.info.get("id") or "?")
        self.log_requested.emit("info", f"开始校验版本 {version_id} 的本地文件…")
        try:
            checks = self.run_checks()
        except Exception as exc:  # noqa: BLE001 - must never disappear silently
            detail = f"{type(exc).__name__}: {exc}"
            self.log_requested.emit("error", f"校验版本 {version_id} 失败：{detail}")
            self._show_result("校验失败", f"校验 {version_id} 时发生错误：", detail)
            return
        problems: list[str] = []
        for check in checks:
            state = str(check.get("state") or "warn")
            level = {"ok": "success", "warn": "warn", "bad": "error"}.get(state, "warn")
            mark = {"ok": "✓", "warn": "!", "bad": "✗"}.get(state, "?")
            line = f"{mark} {check['title']}：{check['detail']}"
            self.log_requested.emit(level, line)
            if state != "ok":
                problems.append(line)
        ok = not problems
        self.log_requested.emit(
            "success" if ok else "warn",
            "校验完成：文件完整" if ok else "校验完成：存在缺失或未确认的文件",
        )
        if ok:
            self._show_result(
                "文件完整",
                f"版本 {version_id} 校验完毕：{len(checks)} 项检查全部正常。",
                "",
            )
        else:
            self._show_result(
                "发现缺失或异常文件",
                f"版本 {version_id} 共 {len(checks)} 项检查，其中 {len(problems)} 项需要处理：",
                "\n".join(problems),
            )

    def _show_result(self, title: str, message: str, detail: str) -> None:
        """Show a validation result in the in-window dialog style."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(
            QMessageBox.Icon.Information if not detail else QMessageBox.Icon.Warning
        )
        box.setText(message)
        if detail:
            box.setDetailedText(detail)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        try:
            run_in_window(self, box, title)
        except Exception as exc:  # noqa: BLE001 - a dialog must never raise
            self.log_requested.emit("warn", f"无法显示校验结果对话框：{exc}")

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
