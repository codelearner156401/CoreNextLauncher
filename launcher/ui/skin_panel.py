"""Embedded skin panel with a flat 2D paper-doll skin preview."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..services import skins
from .tasks import start_task


# 64x64 皮肤的正面 UV（1 单位 = 1 皮肤像素）：
#   头 (8,8) 8x8 · 身体 (20,20) 8x12 · 右臂 (44,20) w x 12 · 左臂 (36,52) w x 12
#   右腿 (4,20) 4x12 · 左腿 (20,52) 4x12
# 其中 w 为手臂宽度：经典 Steve 4px，纤细 Alex 3px。
HEAD_SIZE = 8
BODY_HEIGHT = 12
LEG_HEIGHT = 12
DOLL_HEIGHT = HEAD_SIZE + BODY_HEIGHT + LEG_HEIGHT
ARM_COLUMN_X = 54
ARM_COLUMN_ROWS = (20, 31)
# 纸娃娃与圆角卡片边框之间留一点空白，避免脚底压住边框。
PREVIEW_PADDING = 8


def _is_slim(image: QImage) -> bool:
    """Detect the 3px (slim / Alex) arm layout.

    判据：经典 4px 手臂区域的第 4 列（皮肤坐标 x=54）整列全透明即视为 slim。
    Steve 贴图用满 4 列实体像素，x=54 上有颜色；Alex 只用 3 列，第 4 列留空被
    透明像素填满。与 skinview3d 的 (54, 20) 单点判据同源，这里扫满手臂列高，
    避免单点噪点或压缩残留把模型判反。
    """
    if image.isNull() or image.width() < 64 or image.height() < ARM_COLUMN_ROWS[1] + 1:
        return False
    return all(
        image.pixelColor(ARM_COLUMN_X, y).alpha() == 0
        for y in range(ARM_COLUMN_ROWS[0], ARM_COLUMN_ROWS[1] + 1)
    )


@dataclass(frozen=True)
class Part:
    """One textured body part placed on the doll canvas (unit = 1 skin pixel)."""

    name: str
    source: tuple[int, int, int, int]
    x: int
    y: int


def _layout(arm_width: int) -> tuple[int, list[Part]]:
    """Return the doll canvas width and the front-view part placement."""
    body_x = arm_width
    return 2 * arm_width + HEAD_SIZE, [
        # 头在上、身体居中、腿在下，手臂挂在身体两侧（头顶对齐画布顶部）。
        Part("head", (8, 8, HEAD_SIZE, HEAD_SIZE), body_x, 0),
        Part("body", (20, 20, HEAD_SIZE, BODY_HEIGHT), body_x, HEAD_SIZE),
        # 正面视角：角色右手臂在画面左侧（皮肤 x=44 起），左手臂在画面右侧（x=36 起）。
        Part("right_arm", (44, 20, arm_width, BODY_HEIGHT), 0, HEAD_SIZE),
        Part("left_arm", (36, 52, arm_width, BODY_HEIGHT), body_x + HEAD_SIZE, HEAD_SIZE),
        # 双腿接在身体正下方：右腿占左半，左腿占右半。
        Part("right_leg", (4, 20, 4, LEG_HEIGHT), body_x, HEAD_SIZE + BODY_HEIGHT),
        Part("left_leg", (20, 52, 4, LEG_HEIGHT), body_x + 4, HEAD_SIZE + BODY_HEIGHT),
    ]


class SkinPreview(QWidget):
    """Flat 2D paper-doll preview; the doll is cut and cached once per skin."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(210, 270)
        self._image = QImage()
        self._model = "steve"
        self._doll = QPixmap()
        self._scaled = QPixmap()
        self._scaled_key: tuple[int, int] = (0, 0)
        self._draw_rect = QRect()
        self._draw_scale = 0.0

    @property
    def model(self) -> str:
        return self._model

    @property
    def draw_rect(self) -> QRect:
        """The rectangle the doll was last painted into."""
        return QRect(self._draw_rect)

    @property
    def draw_scale(self) -> float:
        return self._draw_scale

    def set_skin(self, image: QImage, model: str = "steve") -> None:
        self._image = image if isinstance(image, QImage) else QImage()
        self._resolve_model(model)
        self._rebuild()

    def set_model(self, model: str) -> None:
        self._resolve_model(model)
        self._rebuild()

    def _resolve_model(self, model: str) -> None:
        if model in ("steve", "alex"):
            self._model = model
        else:
            # "auto" 或未知值：交给 x=54 列判据决定粗细手臂。
            self._model = "alex" if _is_slim(self._image) else "steve"

    def _rebuild(self) -> None:
        """Cut the six parts out of the texture once and cache the doll pixmap."""
        self._doll = QPixmap()
        self._scaled = QPixmap()
        self._scaled_key = (0, 0)
        if self._image.isNull() or self._image.width() < 64 or self._image.height() < 32:
            self.update()
            return
        texture = self._image.convertToFormat(QImage.Format.Format_ARGB32)
        canvas_width, parts = _layout(3 if self._model == "alex" else 4)
        doll = QPixmap(canvas_width, DOLL_HEIGHT)
        doll.fill(Qt.GlobalColor.transparent)
        painter = QPainter(doll)
        for part in parts:
            x, y, w, h = part.source
            if x + w > texture.width() or y + h > texture.height():
                continue
            painter.drawImage(part.x, part.y, texture.copy(x, y, w, h))
        painter.end()
        self._doll = doll
        self.update()

    def _target_size(self) -> QSize:
        """Fit the doll with one scale factor for both axes (aspect locked)."""
        available_w = max(1, self.width() - 2 * PREVIEW_PADDING)
        available_h = max(1, self.height() - 2 * PREVIEW_PADDING)
        scale = min(available_w / self._doll.width(), available_h / self._doll.height())
        self._draw_scale = scale
        return QSize(
            max(1, int(self._doll.width() * scale)),
            max(1, int(self._doll.height() * scale)),
        )

    def _scaled_doll(self, size: QSize) -> QPixmap:
        key = (size.width(), size.height())
        if self._scaled_key != key:
            # 像素画必须最近邻缩放，否则 8 倍放大后边缘发糊、比例走形。
            self._scaled = self._doll.scaled(
                size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._scaled_key = key
        return self._scaled

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = theme.colors()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette["card"]))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 16, 16)
        if self._doll.isNull():
            self._draw_rect = QRect()
            self._draw_scale = 0.0
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "导入皮肤后预览")
            painter.end()
            return
        scaled = self._scaled_doll(self._target_size())
        self._draw_rect = QRect(
            (self.width() - scaled.width()) // 2,
            (self.height() - scaled.height()) // 2,
            scaled.width(),
            scaled.height(),
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawPixmap(self._draw_rect.topLeft(), scaled)
        painter.end()


class SkinPanel(QFrame):
    """Skin manager embedded inside the launch page."""

    log_requested = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SkinPanel")
        self._account = None
        self._entries: list[skins.SkinEntry] = []
        self._current: skins.SkinEntry | None = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("皮肤管理")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.source_label = QLabel("本地皮肤")
        self.source_label.setObjectName("Hint")
        header.addWidget(self.source_label)
        layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.preview = SkinPreview()
        self.preview.setFixedSize(230, 280)
        body.addWidget(self.preview)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.list = QListWidget()
        self.list.setObjectName("SkinList")
        self.list.setMinimumWidth(250)
        self.list.currentRowChanged.connect(self._on_selected)
        right.addWidget(self.list, 1)

        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        model_row.addWidget(QLabel("模型"))
        self.model_combo = QComboBox()
        self.model_combo.addItem("自动检测", "auto")
        self.model_combo.addItem("史蒂夫", "steve")
        self.model_combo.addItem("艾丽丝", "alex")
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        model_row.addWidget(self.model_combo)
        model_row.addStretch(1)
        right.addLayout(model_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.import_button = QPushButton("导入 PNG")
        self.import_button.setObjectName("Primary")
        self.import_button.clicked.connect(self._import)
        buttons.addWidget(self.import_button)
        self.official_button = QPushButton("拉取正版皮肤")
        self.official_button.setObjectName("Ghost")
        self.official_button.clicked.connect(self._fetch_official)
        buttons.addWidget(self.official_button)
        self.export_button = QPushButton("导出 PNG")
        self.export_button.setObjectName("Ghost")
        self.export_button.clicked.connect(self._export)
        buttons.addWidget(self.export_button)
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("DangerGhost")
        self.delete_button.clicked.connect(self._delete)
        self.delete_button.setEnabled(False)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        right.addLayout(buttons)
        body.addLayout(right, 1)
        layout.addLayout(body)

        hint = QLabel("支持 64×64 / 64×32 PNG，按标准 UV 裁切 2D 纸娃娃正面图；手臂第 4 列（x=54）全透明时自动判定为 Alex 细手臂。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.refresh()

    def set_account(self, account) -> None:
        self._account = account
        self.official_button.setToolTip(
            "从当前正版账号拉取皮肤" if account is not None else "请先登录 Microsoft 正版账号"
        )

    def refresh(self) -> None:
        self._entries = skins.list_skins()
        current_name = self._current.path.name if self._current else ""
        self.list.blockSignals(True)
        self.list.clear()
        for entry in self._entries:
            item = QListWidgetItem(f"{entry.name}  ·  {'Alex' if entry.model == 'alex' else 'Steve'}")
            item.setToolTip(str(entry.path))
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self._entries:
            index = next((i for i, e in enumerate(self._entries) if e.path.name == current_name), 0)
            self.list.setCurrentRow(index)
            self._on_selected(index)
        else:
            self._current = None
            self.preview.set_skin(QImage(), "steve")
            self.delete_button.setEnabled(False)
            self.source_label.setText("暂无本地皮肤")

    def _on_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._entries):
            self._current = None
            self.delete_button.setEnabled(False)
            return
        self._current = self._entries[row]
        image = skins.load_image(self._current.path)
        self.preview.set_skin(image, self._current.model)
        self.model_combo.blockSignals(True)
        index = self.model_combo.findData(self._current.model)
        self.model_combo.setCurrentIndex(max(0, index))
        self.model_combo.blockSignals(False)
        self.delete_button.setEnabled(True)
        self.source_label.setText("Mojang 正版皮肤" if self._current.source == "Mojang" else "本地皮肤")

    def _on_model_changed(self, _index: int) -> None:
        if self._current is None:
            return
        model = str(self.model_combo.currentData() or "auto")
        if model == "auto":
            model = skins.detect_model(skins.load_image(self._current.path))
        updated = skins.set_model(self._current, model)
        self._current = updated
        self.preview.set_model(updated.model)
        self.refresh()

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入皮肤", "", "PNG 皮肤 (*.png)")
        if not path:
            return
        name, ok = QInputDialog.getText(self, "皮肤名称", "保存名称：", text=Path(path).stem)
        if not ok:
            return
        try:
            entry = skins.import_skin(path, name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self._current = entry
        self.refresh()
        self.log_requested.emit("success", f"已导入皮肤：{entry.name}")

    def _fetch_official(self) -> None:
        if self._account is None:
            QMessageBox.information(self, "未登录", "请先完成 Microsoft 正版登录。")
            return
        if self._loading:
            return
        self._loading = True
        self.official_button.setEnabled(False)
        self.source_label.setText("正在从 Mojang 拉取皮肤…")
        start_task(
            self,
            lambda progress, cancel: skins.fetch_official_skin(self._account),
            on_done=self._official_done,
            on_failed=self._official_failed,
        )

    def _official_done(self, entry) -> None:
        self._loading = False
        self.official_button.setEnabled(True)
        self._current = entry
        self.refresh()
        self.log_requested.emit("success", f"已拉取正版皮肤：{entry.name}")

    def _official_failed(self, message: str) -> None:
        self._loading = False
        self.official_button.setEnabled(True)
        self.source_label.setText("拉取失败")
        QMessageBox.warning(self, "拉取皮肤失败", message)

    def _export(self) -> None:
        if self._current is None:
            return
        from ..paths import exports_dir
        import shutil, time

        target_dir = exports_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{self._current.name}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        try:
            shutil.copy2(self._current.path, target)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.log_requested.emit("success", f"皮肤已导出：{target}")

    def _delete(self) -> None:
        if self._current is None:
            return
        answer = QMessageBox.question(
            self,
            "删除皮肤",
            f"确定删除「{self._current.name}」？此操作只删除本地文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        skins.delete_skin(self._current)
        self._current = None
        self.refresh()
        self.log_requested.emit("info", "已删除本地皮肤。")
