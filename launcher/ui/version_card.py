"""PCL2-style rounded version cards for the download catalog."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .. import theme

TAG_LABELS = {
    "release": "正式版",
    "snapshot": "快照",
    "old_beta": "远古版",
    "old_alpha": "远古版",
}
TAG_COLORS = {
    "release": "#2f7cf6",
    "snapshot": "#e08a24",
    "old_beta": "#7d8799",
    "old_alpha": "#7d8799",
}


class VersionCardDelegate(QStyledItemDelegate):
    CARD_HEIGHT = 64
    FALLBACK_WIDTH = 320

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        # option.rect is not always filled in when Qt asks for the hint; a zero
        # width would then be cached as the uniform item size.
        width = option.rect.width()
        return QSize(width if width > 0 else self.FALLBACK_WIDTH, self.CARD_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802
        entry = index.data(Qt.ItemDataRole.UserRole)
        if entry is None:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = theme.colors()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        card = QColor(palette["card"])
        border = QColor(palette["border"])
        if selected:
            card = QColor(palette["selected"])
            border = QColor(palette["accent"])
        elif hover:
            card = QColor(palette["hover"])
        rect = QRectF(option.rect).adjusted(2, 3, -2, -3)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(card)
        painter.drawRoundedRect(rect, 10, 10)

        left = rect.left() + 14
        tag = TAG_LABELS.get(entry.type, entry.type)
        tag_color = QColor(TAG_COLORS.get(entry.type, "#7d8799"))

        font = QFont(option.font)
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(palette["text"]))
        name_rect = QRectF(left, rect.top() + 8, rect.width() * 0.45, 22)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter, entry.id)
        name_width = QFontMetrics(font).horizontalAdvance(entry.id)

        pill_x = left + name_width + 14
        pill = QRectF(pill_x, rect.top() + 10, 56, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tag_color.red(), tag_color.green(), tag_color.blue(), 40))
        painter.drawRoundedRect(pill, 7, 7)
        small = QFont(option.font)
        small.setPixelSize(10)
        painter.setFont(small)
        painter.setPen(tag_color)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, tag)

        if getattr(entry, "recommended", False):
            recommend = QRectF(pill.right() + 6, rect.top() + 10, 58, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            accent = QColor(palette["accent"])
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 34))
            painter.drawRoundedRect(recommend, 7, 7)
            painter.setPen(accent)
            painter.drawText(recommend, Qt.AlignmentFlag.AlignCenter, "★ 推荐")

        date = str(getattr(entry, "release_time", "") or "")[:10]
        date_font = QFont(option.font)
        date_font.setPixelSize(11)
        painter.setFont(date_font)
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(
            QRectF(rect.right() - 120, rect.top() + 8, 106, 22),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            date,
        )
        hint = QFont(option.font)
        hint.setPixelSize(10)
        painter.setFont(hint)
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(
            QRectF(left, rect.top() + 32, rect.width() - 28, 20),
            Qt.AlignmentFlag.AlignVCenter,
            "Mojang 官方版本 · 点击选择后安装",
        )
        painter.restore()
