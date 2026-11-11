"""Embedded skin panel with a pseudo-3D rotating Steve/Alex preview."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform
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


@dataclass(frozen=True)
class Box:
    cx: float
    cy: float
    cz: float
    sx: float
    sy: float
    sz: float
    texture: str


LEFT_ARM = "left_arm"
RIGHT_ARM = "right_arm"
LEFT_LEG = "left_leg"
RIGHT_LEG = "right_leg"


class SkinPreview(QWidget):
    """A small rotating box-model renderer (no OpenGL dependency)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(210, 270)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._image = QImage()
        self._model = "steve"
        self._yaw = -24.0
        self._pitch = 7.0
        self._drag = None
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_skin(self, image: QImage, model: str = "steve") -> None:
        self._image = image
        self._model = model if model in ("steve", "alex") else "steve"
        self.update()

    def set_model(self, model: str) -> None:
        self._model = model if model in ("steve", "alex") else "steve"
        self.update()

    def _tick(self) -> None:
        if self._drag is None:
            self._yaw = (self._yaw + 0.45) % 360.0
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            delta = event.position() - self._drag
            self._yaw = (self._yaw + delta.x() * 0.6) % 360.0
            self._pitch = max(-35.0, min(35.0, self._pitch + delta.y() * 0.3))
            self._drag = event.position()
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()

    def _project(self, point: tuple[float, float, float], scale: float) -> tuple[float, float, float]:
        x, y, z = point
        y -= 16.0
        yaw = math.radians(self._yaw)
        pitch = math.radians(self._pitch)
        x1 = x * math.cos(yaw) + z * math.sin(yaw)
        z1 = -x * math.sin(yaw) + z * math.cos(yaw)
        y2 = y * math.cos(pitch) - z1 * math.sin(pitch)
        z2 = y * math.sin(pitch) + z1 * math.cos(pitch)
        return (
            self.width() / 2 + x1 * scale,
            self.height() / 2 - y2 * scale + z2 * scale * 0.18,
            z2,
        )

    @staticmethod
    def _face_rect(part: str, face: str, model: str) -> tuple[int, int, int, int]:
        slim = model == "alex"
        w = 3 if slim else 4
        if part == "head":
            return {
                "front": (8, 8, 8, 8), "back": (24, 8, 8, 8),
                "right": (0, 8, 8, 8), "left": (16, 8, 8, 8),
                "top": (8, 0, 8, 8), "bottom": (16, 0, 8, 8),
            }[face]
        if part == "body":
            return {
                "front": (20, 20, 8, 12), "back": (32, 20, 8, 12),
                "right": (16, 20, 4, 12), "left": (28, 20, 4, 12),
                "top": (20, 16, 8, 4), "bottom": (28, 16, 8, 4),
            }[face]
        if part == RIGHT_ARM:
            return {
                "front": (44, 20, w, 12), "back": (44 + w, 20, w, 12),
                "right": (40, 20, 4, 12), "left": (48, 20, 4, 12),
                "top": (44, 16, w, 4), "bottom": (48, 16, w, 4),
            }[face]
        if part == LEFT_ARM:
            return {
                "front": (36, 52, w, 12), "back": (36 + w, 52, w, 12),
                "right": (32, 52, 4, 12), "left": (40, 52, 4, 12),
                "top": (36, 48, w, 4), "bottom": (40, 48, w, 4),
            }[face]
        if part == RIGHT_LEG:
            return {
                "front": (4, 20, 4, 12), "back": (12, 20, 4, 12),
                "right": (0, 20, 4, 12), "left": (8, 20, 4, 12),
                "top": (4, 16, 4, 4), "bottom": (8, 16, 4, 4),
            }[face]
        return {
            "front": (20, 52, 4, 12), "back": (28, 52, 4, 12),
            "right": (16, 52, 4, 12), "left": (24, 52, 4, 12),
            "top": (20, 48, 4, 4), "bottom": (24, 48, 4, 4),
        }[face]

    @staticmethod
    def _average(image: QImage, rect: tuple[int, int, int, int]) -> QColor:
        x0, y0, w, h = rect
        r = g = b = a = 0
        count = 0
        for x in range(x0, min(image.width(), x0 + w)):
            for y in range(y0, min(image.height(), y0 + h)):
                c = image.pixelColor(x, y)
                if c.alpha() == 0:
                    continue
                r += c.red(); g += c.green(); b += c.blue(); a += c.alpha()
                count += 1
        if not count:
            return QColor(60, 70, 90)
        return QColor(r // count, g // count, b // count, max(120, a // count))

    def _boxes(self) -> list[Box]:
        return [
            Box(0, 28, 0, 8, 8, 8, "head"),
            Box(0, 18, 0, 8, 12, 4, "body"),
            Box(-6 if self._model == "alex" else -6, 18, 0, 3 if self._model == "alex" else 4, 12, 4, LEFT_ARM),
            Box(6, 18, 0, 3 if self._model == "alex" else 4, 12, 4, RIGHT_ARM),
            Box(-2, 6, 0, 4, 12, 4, LEFT_LEG),
            Box(2, 6, 0, 4, 12, 4, RIGHT_LEG),
        ]

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        palette = theme.colors()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette["card"]))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 16, 16)
        if self._image.isNull():
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "导入皮肤后预览")
            return
        scale = min(self.width() / 42.0, self.height() / 44.0)
        shadow = QRectF(self.width() / 2 - 36, self.height() * 0.80, 72, 14)
        painter.setBrush(QColor(0, 0, 0, 55))
        painter.drawEllipse(shadow)

        faces = []
        for box in self._boxes():
            hx, hy, hz = box.sx / 2, box.sy / 2, box.sz / 2
            corners = {
                "front": [(box.cx - hx, box.cy - hy, box.cz + hz), (box.cx + hx, box.cy - hy, box.cz + hz),
                          (box.cx + hx, box.cy + hy, box.cz + hz), (box.cx - hx, box.cy + hy, box.cz + hz)],
                "back": [(box.cx + hx, box.cy - hy, box.cz - hz), (box.cx - hx, box.cy - hy, box.cz - hz),
                         (box.cx - hx, box.cy + hy, box.cz - hz), (box.cx + hx, box.cy + hy, box.cz - hz)],
                "right": [(box.cx + hx, box.cy - hy, box.cz + hz), (box.cx + hx, box.cy - hy, box.cz - hz),
                          (box.cx + hx, box.cy + hy, box.cz - hz), (box.cx + hx, box.cy + hy, box.cz + hz)],
                "left": [(box.cx - hx, box.cy - hy, box.cz - hz), (box.cx - hx, box.cy - hy, box.cz + hz),
                         (box.cx - hx, box.cy + hy, box.cz + hz), (box.cx - hx, box.cy + hy, box.cz - hz)],
                "top": [(box.cx - hx, box.cy + hy, box.cz + hz), (box.cx + hx, box.cy + hy, box.cz + hz),
                        (box.cx + hx, box.cy + hy, box.cz - hz), (box.cx - hx, box.cy + hy, box.cz - hz)],
                "bottom": [(box.cx - hx, box.cy - hy, box.cz - hz), (box.cx + hx, box.cy - hy, box.cz - hz),
                           (box.cx + hx, box.cy - hy, box.cz + hz), (box.cx - hx, box.cy - hy, box.cz + hz)],
            }
            for face, pts in corners.items():
                projected = [self._project(p, scale) for p in pts]
                depth = sum(p[2] for p in projected) / 4.0
                faces.append((depth, box.texture, face, projected))
        faces.sort(key=lambda row: row[0])

        for _depth, part, face, pts in faces:
            polygon = QPolygonF([QPointF(p[0], p[1]) for p in pts])
            tex = self._face_rect(part, face, self._model)
            color = self._average(self._image, tex)
            path = QPainterPath()
            path.addPolygon(polygon)
            path.closeSubpath()
            painter.save()
            painter.setClipPath(path)
            source = QPolygonF([
                QPointF(tex[0], tex[1]),
                QPointF(tex[0] + tex[2], tex[1]),
                QPointF(tex[0] + tex[2], tex[1] + tex[3]),
                QPointF(tex[0], tex[1] + tex[3]),
            ])
            transform = QTransform()
            if QTransform.quadToQuad(source, polygon, transform):
                painter.setTransform(transform, True)
                painter.drawImage(0, 0, self._image)
            else:
                painter.fillPath(path, color)
            painter.restore()
            painter.setPen(QPen(QColor(0, 0, 0, 55), 0.7))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(polygon)
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
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("DangerGhost")
        self.delete_button.clicked.connect(self._delete)
        self.delete_button.setEnabled(False)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        right.addLayout(buttons)
        body.addLayout(right, 1)
        layout.addLayout(body)

        hint = QLabel("支持 64×64 / 64×32 PNG，自动识别 Steve / Alex；拖拽预览可旋转模型。")
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

