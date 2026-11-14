"""PCL-style start page: selected version, account and big launch button."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME
from .glass import GlassFrame


class StartPage(QWidget):
    launch_requested = Signal()
    open_folder_requested = Signal()
    open_logs_requested = Signal()
    log_requested = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        content.setObjectName("PageInner")
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 8, 12, 8)
        layout.setSpacing(14)

        title = QLabel("启动")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        hero = GlassFrame(radius=18.0)
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(26, 22, 26, 22)
        hero_layout.setSpacing(10)

        eyebrow = QLabel("CORENEXTLAUNCHER")
        eyebrow.setObjectName("Eyebrow")
        hero_layout.addWidget(eyebrow)

        self.version_label = QLabel("尚未选择版本")
        self.version_label.setObjectName("HeroTitle")
        hero_layout.addWidget(self.version_label)

        self.version_meta = QLabel("请选择左侧版本库中的游戏版本")
        self.version_meta.setObjectName("Muted")
        self.version_meta.setWordWrap(True)
        hero_layout.addWidget(self.version_meta)
        hero_layout.addSpacing(6)

        self.launch_button = QPushButton("▶   启动游戏")
        self.launch_button.setObjectName("HeroLaunch")
        self.launch_button.setMinimumHeight(52)
        self.launch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_button.clicked.connect(self.launch_requested.emit)
        hero_layout.addWidget(self.launch_button)

        info = QHBoxLayout()
        info.setSpacing(10)
        info.addWidget(self._info_card("账号", "account_value", "未登录"))
        info.addWidget(self._info_card("Java", "java_value", "自动检测"))
        info.addWidget(self._info_card("内存", "memory_value", "自动"))
        hero_layout.addLayout(info)
        layout.addWidget(hero)

        quick = QHBoxLayout()
        quick.setSpacing(10)
        self.folder_button = QPushButton("打开游戏目录")
        self.folder_button.setObjectName("Ghost")
        self.folder_button.clicked.connect(self.open_folder_requested.emit)
        quick.addWidget(self.folder_button)
        self.logs_button = QPushButton("查看运行日志")
        self.logs_button.setObjectName("Ghost")
        self.logs_button.clicked.connect(self.open_logs_requested.emit)
        quick.addWidget(self.logs_button)
        quick.addStretch(1)
        layout.addLayout(quick)
        layout.addStretch(1)

    def _info_card(self, title: str, attr: str, value: str) -> QFrame:
        card = QFrame()
        card.setObjectName("InfoCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(3)
        label = QLabel(title)
        label.setObjectName("InfoLabel")
        box.addWidget(label)
        content = QLabel(value)
        content.setObjectName("InfoValue")
        content.setWordWrap(True)
        setattr(self, attr, content)
        box.addWidget(content)
        return card

    def set_version(self, version_id: str, version_type: str = "") -> None:
        if version_id:
            self.version_label.setText(version_id)
            meta = f"{version_type} · {APP_NAME} 已就绪" if version_type else "已就绪"
            self.version_meta.setText(meta)
        else:
            self.version_label.setText("尚未选择版本")
            self.version_meta.setText("请选择左侧版本库中的游戏版本")

    def set_account(self, account) -> None:
        if account is None:
            self.account_value.setText("未登录")
            return
        name = getattr(account, "name", "") or str(account)
        self.account_value.setText(name or "未登录")

    def set_environment(self, java: str = "", memory: str = "") -> None:
        if java:
            self.java_value.setText(java)
        if memory:
            self.memory_value.setText(memory)

    def set_running(self, running: bool) -> None:
        self.launch_button.setEnabled(not running)
        self.launch_button.setText("启动中…" if running else "▶   启动游戏")
