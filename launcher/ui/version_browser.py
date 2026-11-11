"""Full-page PCL2-style version browser (replaces modal download dialog flows)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..services import catalog
from .loading_overlay import LoadingOverlay
from .subpage import SubPage
from .tasks import start_task
from .version_card import VersionCardDelegate


class VersionBrowserPage(SubPage):
    install_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(
            "浏览官方版本",
            "从 Mojang 官方版本清单选择版本，选中后可直接安装；返回按钮会滑动回到上一页。",
            parent,
        )
        self.setObjectName("VersionBrowserPage")
        self._entries: list[catalog.VersionEntry] = []
        self._loading = False

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.filter = QComboBox()
        self.filter.addItem("全部版本", "all")
        self.filter.addItem("正式版", "release")
        self.filter.addItem("快照", "snapshot")
        self.filter.addItem("远古版", "old")
        self.filter.setFixedWidth(120)
        self.filter.currentIndexChanged.connect(self._fill)
        controls.addWidget(self.filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索版本号…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        controls.addWidget(self.search, 1)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("Ghost")
        self.refresh_button.clicked.connect(self.load)
        controls.addWidget(self.refresh_button)
        self.install_button = QPushButton("安装选中版本")
        self.install_button.setObjectName("Primary")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self._install)
        controls.addWidget(self.install_button)
        self.body.addLayout(controls)

        self.list = QListWidget()
        self.list.setObjectName("CatalogList")
        self.list.setSpacing(1)
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setItemDelegate(VersionCardDelegate(self.list))
        self.list.itemSelectionChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda _item: self._install())
        self.body.addWidget(self.list, 1)

        self.status = QLabel("点击“刷新”加载 Mojang 官方版本清单")
        self.status.setObjectName("Hint")
        self.body.addWidget(self.status)
        self.loading = LoadingOverlay(self)
        self._loaded = False

    def load(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.refresh_button.setEnabled(False)
        self.loading.show_loading("正在加载官方版本文件…")
        self.status.setText("正在获取 Mojang 官方版本清单…")
        start_task(
            self,
            lambda progress, cancel: catalog.fetch_versions(),
            on_done=self._loaded_ok,
            on_failed=self._loaded_fail,
        )

    def _loaded_ok(self, entries) -> None:
        self._loading = False
        self._loaded = True
        self.refresh_button.setEnabled(True)
        self.loading.stop()
        self._entries = list(entries or [])
        self._fill()
        self.status.setText(f"已加载 {len(self._entries)} 个官方版本")

    def _loaded_fail(self, message: str) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)
        self.loading.stop()
        self.status.setText(f"获取版本清单失败：{message}")

    def _fill(self) -> None:
        mode = str(self.filter.currentData() or "all")
        query = self.search.text().strip().lower()
        self.list.blockSignals(True)
        self.list.clear()
        shown = 0
        for entry in self._entries:
            if mode == "old":
                if entry.type not in ("old_beta", "old_alpha"):
                    continue
            elif mode != "all" and entry.type != mode:
                continue
            if query and query not in entry.id.lower():
                continue
            item = QListWidgetItem(entry.id)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setSizeHint(QSize(100, 64))
            self.list.addItem(item)
            shown += 1
        self.list.blockSignals(False)
        self.install_button.setEnabled(False)
        if self._entries:
            self.status.setText(f"显示 {shown} / {len(self._entries)} 个版本")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.loading.setGeometry(self.rect())

    def _selected(self) -> None:
        items = self.list.selectedItems()
        if not items:
            self.install_button.setEnabled(False)
            return
        entry = items[0].data(Qt.ItemDataRole.UserRole)
        if entry is not None:
            self.install_button.setEnabled(True)
            self.status.setText(f"已选择 {entry.id} · {entry.release_time[:10]}")

    def _install(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        entry = items[0].data(Qt.ItemDataRole.UserRole)
        if entry is not None:
            self.install_requested.emit(entry.id)
