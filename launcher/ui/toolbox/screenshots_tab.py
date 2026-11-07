"""截图管理 tab: thumbnail gallery and a full-size viewer."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...services import screenshots as shots
from ...services.util import human_age, human_size
from .common import card, open_path, start_task

THUMB_SIZE = QSize(232, 132)


class ScreenshotsTab(QWidget):
    log_requested = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_dir: Path | None = None
        self.items: list[shots.ScreenshotInfo] = []
        self._thumbs: dict[str, QPixmap] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(10)

        toolbar_card, toolbar_layout = card(
            "游戏截图",
            "按时间排序预览 screenshots 目录里的截图；双击查看大图，可上一张/下一张翻阅。",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        refresh = QPushButton("刷新")
        refresh.setObjectName("Primary")
        refresh.clicked.connect(lambda: self.reload())
        row.addWidget(refresh)
        self.delete_button = QPushButton("删除选中")
        self.delete_button.setObjectName("DangerGhost")
        self.delete_button.clicked.connect(self.delete_selected)
        row.addWidget(self.delete_button)
        folder = QPushButton("打开截图目录")
        folder.setObjectName("Ghost")
        folder.clicked.connect(self.open_folder)
        row.addWidget(folder)
        row.addStretch(1)
        self.summary = QLabel("尚未加载")
        self.summary.setObjectName("Muted")
        row.addWidget(self.summary)
        toolbar_layout.addLayout(row)
        layout.addWidget(toolbar_card)

        self.grid = QListWidget()
        self.grid.setObjectName("ScreenshotGrid")
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(THUMB_SIZE)
        self.grid.setGridSize(QSize(THUMB_SIZE.width() + 26, THUMB_SIZE.height() + 62))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(8)
        self.grid.itemDoubleClicked.connect(self._open_item)
        layout.addWidget(self.grid, 1)

        self.empty = QLabel("screenshots 目录中还没有截图")
        self.empty.setObjectName("EmptyHint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.hide()
        layout.addWidget(self.empty)

    # ---------------------------------------------------------------- load
    def set_game_dir(self, game_dir: str) -> None:
        changed = str(self.game_dir or "") != str(game_dir or "")
        self.game_dir = Path(game_dir) if game_dir else None
        if changed:
            self.items = []
            self._thumbs.clear()
            self.grid.clear()
            self.summary.setText("尚未加载")
        if self.isVisible() and self.game_dir is not None:
            self.reload()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.game_dir is not None and not self.items:
            self.reload()

    def reload(self) -> None:
        if self.game_dir is None:
            self.summary.setText("请先在设置页选择 .minecraft 目录")
            return
        items = shots.list_screenshots(self.game_dir)
        self.items = items
        self.grid.clear()
        self._thumbs.clear()
        self.empty.setVisible(not items)
        if not items:
            self.summary.setText("没有截图")
            return
        self.summary.setText(f"共 {len(items)} 张截图")
        start_task(
            self,
            lambda progress, cancelled: self._load_thumbs(items, progress, cancelled),
            on_done=self._on_thumbs,
            on_failed=lambda message: self.summary.setText(f"读取截图失败：{message}"),
        )

    @staticmethod
    def _load_thumbs(items, progress, cancelled) -> list[tuple[shots.ScreenshotInfo, "QImage"]]:
        result: list[tuple[shots.ScreenshotInfo, QImage]] = []
        for index, info in enumerate(items):
            if cancelled():
                break
            progress(f"正在读取截图 {index + 1}/{len(items)}", index, len(items))
            reader = QImageReader(str(info.path))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() > 0:
                scaled = size.scaled(
                    THUMB_SIZE * 2, Qt.AspectRatioMode.KeepAspectRatio
                )
                reader.setScaledSize(scaled)
            image = reader.read()
            result.append((info, image))
        return result

    def _on_thumbs(self, payload) -> None:
        for info, image in payload:
            if image.isNull():
                continue
            self._thumbs[str(info.path)] = QPixmap.fromImage(image)
        self._render_grid()
        if self.summary.text().startswith("正在"):
            self.summary.setText(f"共 {len(self.items)} 张截图")

    def _render_grid(self) -> None:
        current = self.grid.currentRow()
        self.grid.clear()
        empty = QPixmap(THUMB_SIZE)
        empty.fill(Qt.GlobalColor.transparent)
        for info in self.items:
            pixmap = self._thumbs.get(str(info.path), empty)
            item = QListWidgetItem(
                f"{info.path.name}\n{info.resolution} · {human_size(info.size)}"
            )
            item.setIcon(pixmap)
            item.setData(Qt.ItemDataRole.UserRole, str(info.path))
            item.setToolTip(
                f"{info.path}\n{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info.mtime))}"
            )
            self.grid.addItem(item)
        if 0 <= current < self.grid.count():
            self.grid.setCurrentRow(current)
        elif self.grid.count():
            self.grid.setCurrentRow(0)

    # -------------------------------------------------------------- actions
    def _selected_index(self) -> int:
        row = self.grid.currentRow()
        if 0 <= row < len(self.items):
            return row
        return -1

    def _open_item(self, item: QListWidgetItem) -> None:
        for index, info in enumerate(self.items):
            if info.path == Path(str(item.data(Qt.ItemDataRole.UserRole))):
                self._open_viewer(index)
                return

    def _open_viewer(self, index: int) -> None:
        if not self.items:
            return
        dialog = ScreenshotViewer(self, self.items, index)
        dialog.deleted.connect(lambda path: self._on_deleted(path))
        dialog.exec()

    def _on_deleted(self, path: str) -> None:
        self.log_requested.emit("info", f"已删除截图：{Path(path).name}")
        self.items = [info for info in self.items if str(info.path) != path]
        self._thumbs.pop(path, None)
        self._render_grid()
        self.summary.setText(f"共 {len(self.items)} 张截图")
        self.empty.setVisible(not self.items)

    def delete_selected(self) -> None:
        index = self._selected_index()
        if index < 0:
            QMessageBox.information(self, "未选择截图", "请先选中一张截图。")
            return
        info = self.items[index]
        answer = QMessageBox.question(
            self,
            "删除截图",
            f"删除 {info.path.name}？该操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            shots.delete_screenshot(info.path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._on_deleted(str(info.path))

    def open_folder(self) -> None:
        if self.game_dir is None:
            QMessageBox.warning(self, "未设置目录", "请先在设置页选择 .minecraft 目录。")
            return
        open_path(self.game_dir / "screenshots", create=True)


class ScreenshotViewer(QDialog):
    deleted = Signal(str)

    def __init__(self, parent, items: list[shots.ScreenshotInfo], index: int) -> None:
        super().__init__(parent)
        self.items = list(items)
        self.index = max(0, min(index, len(self.items) - 1))
        self._pixmap = QPixmap()
        self.setObjectName("ToolboxDialog")
        self.setWindowTitle("截图预览")
        self.resize(1100, 720)
        self.setMinimumSize(720, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        self.meta = QLabel("")
        self.meta.setObjectName("Hint")
        self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.meta)

        self.view = QLabel()
        self.view.setObjectName("ScreenshotView")
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setMinimumSize(400, 240)
        layout.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.prev_button = QPushButton("‹ 上一张")
        self.prev_button.setObjectName("Ghost")
        self.prev_button.clicked.connect(lambda: self.step(-1))
        row.addWidget(self.prev_button)
        self.next_button = QPushButton("下一张 ›")
        self.next_button.setObjectName("Ghost")
        self.next_button.clicked.connect(lambda: self.step(1))
        row.addWidget(self.next_button)
        row.addStretch(1)
        self.open_button = QPushButton("用系统看图打开")
        self.open_button.setObjectName("Ghost")
        self.open_button.clicked.connect(self._open_external)
        row.addWidget(self.open_button)
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("DangerGhost")
        self.delete_button.clicked.connect(self._delete)
        row.addWidget(self.delete_button)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

        self._show_current()

    def _item(self) -> shots.ScreenshotInfo:
        return self.items[self.index]

    def _show_current(self) -> None:
        info = self._item()
        self._pixmap = QPixmap(str(info.path))
        size = info.resolution
        age = human_age(info.mtime, time.time())
        self.meta.setText(
            f"{info.path.name} · {size} · {human_size(info.size)} · {age} · "
            f"{self.index + 1}/{len(self.items)}"
        )
        self._rescale()
        self.prev_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(self.items) - 1)

    def _rescale(self) -> None:
        if self._pixmap.isNull():
            self.view.setText("无法读取这张截图")
            return
        target = self.view.size()
        if target.width() <= 4 or target.height() <= 4:
            return
        scaled = self._pixmap.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.view.setPixmap(scaled)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rescale()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.step(-1)
            return
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self.step(1)
            return
        super().keyPressEvent(event)

    def step(self, delta: int) -> None:
        target = self.index + delta
        if 0 <= target < len(self.items):
            self.index = target
            self._show_current()

    def _open_external(self) -> None:
        open_path(self._item().path)

    def _delete(self) -> None:
        info = self._item()
        answer = QMessageBox.question(
            self,
            "删除截图",
            f"删除 {info.path.name}？该操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            shots.delete_screenshot(info.path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self.deleted.emit(str(info.path))
        self.items.pop(self.index)
        if not self.items:
            self.accept()
            return
        self.index = min(self.index, len(self.items) - 1)
        self._show_current()
