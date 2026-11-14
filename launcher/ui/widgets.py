"""Reusable widgets: version list, animations, progress and log console."""

from __future__ import annotations

import html
import math
import re
from datetime import datetime

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionProgressBar,
    QStyleOptionViewItem,
    QStylePainter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import anim, theme
from .glass import GlassFrame

TYPE_LABELS = {
    "release": "正式版",
    "snapshot": "快照",
    "old_beta": "旧 Beta",
    "old_alpha": "旧 Alpha",
}


def natural_key(text: str) -> list:
    """Sort ``1.10`` after ``1.9`` and snapshots sensibly."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


class VersionDelegate(QStyledItemDelegate):
    """Draw a rounded, hoverable version card."""

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        return QSize(240, 66)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802
        data = index.data(Qt.ItemDataRole.UserRole) or {}
        if not isinstance(data, dict):
            data = {}
        c = theme.colors()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect.adjusted(3, 3, -3, -3)

        background = QColor(c["selected"] if selected else (c["hover"] if hover else c["card"]))
        background.setAlpha(228 if selected else (196 if hover else 158))
        if selected:
            border = QColor(c["accent"])
        elif hover:
            border = QColor(c["accent"])
            border.setAlpha(140)
        else:
            border = QColor(c["border"])
            border.setAlpha(190)
        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 10, 10)

        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["accent"]))
            painter.drawRoundedRect(QRect(rect.left() + 1, rect.top() + 15, 3, rect.height() - 30), 2, 2)

        vid = str(data.get("id", "?"))
        vtype = str(data.get("type", "release"))
        badge = TYPE_LABELS.get(vtype, vtype)

        name_rect = QRect(rect.left() + 16, rect.top() + 11, rect.width() - 42, 22)
        name_font = QFont(option.font)
        name_font.setPointSizeF(10.8)
        name_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(name_font)
        painter.setPen(QColor(c["text"]))
        painter.drawText(name_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), vid)

        small = QFont(option.font)
        small.setPointSizeF(8.7)
        painter.setFont(small)
        sub_rect = QRect(name_rect.left(), rect.top() + 36, rect.width() - 46, 16)
        painter.setPen(QColor(c["accent"] if selected else c["badge_text"]))
        painter.drawText(sub_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), badge)
        badge_width = QFontMetrics(small).horizontalAdvance(badge)
        painter.setPen(QColor(c["muted"]))
        painter.drawText(
            sub_rect.adjusted(badge_width + 6, 0, 0, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "· 已安装",
        )

        chevron = QFont(option.font)
        chevron.setPointSizeF(13)
        painter.setFont(chevron)
        painter.setPen(QColor(c["accent"] if selected else c["muted"]))
        painter.drawText(
            QRect(rect.right() - 26, rect.top(), 20, rect.height()),
            int(Qt.AlignmentFlag.AlignCenter),
            "›",
        )
        painter.restore()


class ClickableLabel(QLabel):
    """A QLabel that emits ``clicked`` on a left mouse release."""

    clicked = Signal()

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HoverCard(GlassFrame):
    """A glass card that animates a very subtle accent border on hover."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, radius=14.0)
        self._hover = 0.0
        self._anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._anim.setDuration(anim.ms(180))
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def get_hover_progress(self) -> float:
        return self._hover

    def set_hover_progress(self, value: float) -> None:
        self._hover = max(0.0, min(1.0, float(value)))
        self.update()

    hoverProgress = Property(float, get_hover_progress, set_hover_progress)

    def _animate_hover(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        self._paint_surface(self._hover)


class AnimatedLaunchButton(QPushButton):
    """Launch button with a springy scale and an animated glow.

    The button content is drawn through a transformed ``QStylePainter`` so the
    whole QSS surface (gradient, radius, text) scales together instead of only
    changing its geometry.  ``OutBack`` easing gives the release its Q-bouncy
    rebound; the drop shadow fades in as the button lifts towards the pointer.
    """

    _IDLE_BLUR = 10
    _HOVER_BLUR = 38
    _PRESS_BLUR = 22
    _HOVER_SCALE = 1.05
    _PRESS_SCALE = 0.93

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scale = 1.0
        self._pressed = False
        self._hovered = False

        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(self._IDLE_BLUR)
        self._shadow.setOffset(0, 5)
        self._shadow.setColor(QColor(79, 140, 255, 70))
        self.setGraphicsEffect(self._shadow)

        self._scale_anim = QPropertyAnimation(self, b"scale", self)
        self._scale_anim.setEasingCurve(QEasingCurve.Type.OutBack)

        self._blur_anim = QPropertyAnimation(self._shadow, b"blurRadius", self)
        self._blur_anim.setDuration(anim.ms(260))
        self._blur_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._color_anim = QPropertyAnimation(self._shadow, b"color", self)
        self._color_anim.setDuration(anim.ms(260))
        self._color_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.pressed.connect(self._on_pressed)
        self.released.connect(self._on_released)

    # ------------------------------------------------------------- property
    def get_scale(self) -> float:
        return self._scale

    def set_scale(self, value: float) -> None:
        self._scale = max(0.5, min(1.2, float(value)))
        self.update()

    scale = Property(float, get_scale, set_scale)

    # --------------------------------------------------------------- helpers
    def set_rest_geometry(self, rect: QRect) -> None:
        self.setGeometry(rect)

    def _animate_scale(
        self, target: float, duration: int, curve: QEasingCurve.Type
    ) -> None:
        self._scale_anim.stop()
        self._scale_anim.setStartValue(self._scale)
        self._scale_anim.setEndValue(target)
        self._scale_anim.setDuration(anim.ms(duration))
        self._scale_anim.setEasingCurve(curve)
        self._scale_anim.start()

    def _animate_glow(self, blur: int, alpha: int, duration: int = 260) -> None:
        self._blur_anim.stop()
        self._blur_anim.setStartValue(self._shadow.blurRadius())
        self._blur_anim.setEndValue(blur)
        self._blur_anim.setDuration(anim.ms(duration))
        self._blur_anim.start()

        colour = QColor(79, 140, 255, alpha)
        self._color_anim.stop()
        self._color_anim.setStartValue(self._shadow.color())
        self._color_anim.setEndValue(colour)
        self._color_anim.setDuration(anim.ms(duration))
        self._color_anim.start()

    def _update_glow(self, immediate: bool = False) -> None:
        if not self.isEnabled():
            blur, alpha = 0, 0
        elif self._pressed:
            blur, alpha = self._PRESS_BLUR, 190
        elif self._hovered:
            blur, alpha = self._HOVER_BLUR, 235
        else:
            blur, alpha = self._IDLE_BLUR, 70
        if immediate:
            self._blur_anim.stop()
            self._color_anim.stop()
            self._shadow.setBlurRadius(blur)
            self._shadow.setColor(QColor(79, 140, 255, alpha))
        else:
            self._animate_glow(blur, alpha)

    # ---------------------------------------------------------------- events
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        if self.isEnabled():
            if not self._pressed:
                self._animate_scale(
                    self._HOVER_SCALE, 340, QEasingCurve.Type.OutBack
                )
            self._update_glow()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        if not self._pressed and self.isEnabled():
            self._animate_scale(1.0, 320, QEasingCurve.Type.OutCubic)
            self._update_glow()
        super().leaveEvent(event)

    def _on_pressed(self) -> None:
        if not self.isEnabled():
            return
        self._pressed = True
        self._animate_scale(self._PRESS_SCALE, 70, QEasingCurve.Type.OutCubic)
        self._update_glow()

    def _on_released(self) -> None:
        if not self._pressed:
            return
        self._pressed = False
        target = self._HOVER_SCALE if (self._hovered and self.isEnabled()) else 1.0
        self._animate_scale(target, 620, QEasingCurve.Type.OutBack)
        self._update_glow()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange:
            if self.isEnabled():
                self._hovered = bool(self.underMouse())
                if self._hovered and not self._pressed:
                    self._animate_scale(
                        self._HOVER_SCALE, 380, QEasingCurve.Type.OutBack
                    )
                self._update_glow(immediate=True)
            else:
                self._pressed = False
                self._scale_anim.stop()
                self._scale = 1.0
                self._update_glow(immediate=True)
                self.update()
        super().changeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QStylePainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect()
        center = rect.center()
        if self._scale > 1.0:
            # The surface is scaled about the widget centre, so draw it inside
            # an inset rect; otherwise the grown corners get clipped away.
            inset = int(
                math.ceil((self._scale - 1.0) * min(rect.width(), rect.height()) / 2.0)
            )
            option.rect = rect.adjusted(inset, inset, -inset, -inset)
        painter.translate(center)
        painter.scale(self._scale, self._scale)
        painter.translate(-center)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)


class AnimatedTabBar(QTabBar):
    """Tab strip with a smoothly sliding accent underline."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setUsesScrollButtons(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._indicator_x = 0.0
        self._indicator_width = 0.0
        self._x_anim = QPropertyAnimation(self, b"indicatorX", self)
        self._w_anim = QPropertyAnimation(self, b"indicatorWidth", self)
        for animation in (self._x_anim, self._w_anim):
            animation.setDuration(anim.ms(220))
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.currentChanged.connect(self._animate_indicator)

    def get_indicator_x(self) -> float:
        return self._indicator_x

    def set_indicator_x(self, value: float) -> None:
        self._indicator_x = float(value)
        self.update()

    indicatorX = Property(float, get_indicator_x, set_indicator_x)

    def get_indicator_width(self) -> float:
        return self._indicator_width

    def set_indicator_width(self, value: float) -> None:
        self._indicator_width = max(0.0, float(value))
        self.update()

    indicatorWidth = Property(float, get_indicator_width, set_indicator_width)

    def _indicator_target(self) -> tuple[float, float]:
        index = self.currentIndex()
        if index < 0:
            return 0.0, 0.0
        rect = self.tabRect(index)
        if not rect.isValid() or rect.width() <= 0:
            return 0.0, 0.0
        return rect.left() + 14.0, max(0.0, rect.width() - 28.0)

    def _set_indicator_now(self) -> None:
        self._x_anim.stop()
        self._w_anim.stop()
        x, width = self._indicator_target()
        self.set_indicator_x(x)
        self.set_indicator_width(width)

    def _animate_indicator(self, _index: int = -1) -> None:
        x, width = self._indicator_target()
        if not self.isVisible() or self._indicator_width <= 0.5:
            self._set_indicator_now()
            return
        self._x_anim.stop()
        self._x_anim.setStartValue(self._indicator_x)
        self._x_anim.setEndValue(x)
        self._x_anim.start()
        self._w_anim.stop()
        self._w_anim.setStartValue(self._indicator_width)
        self._w_anim.setEndValue(width)
        self._w_anim.start()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._set_indicator_now)

    def _update_scroll_buttons(self) -> None:
        """Only pay for scroll buttons when the tabs genuinely do not fit."""
        if self.count() == 0:
            return
        needed = sum(self.tabSizeHint(index).width() for index in range(self.count()))
        parent = self.parentWidget()
        available = parent.width() if parent is not None else self.width()
        overflow = bool(available) and needed > available
        if self.usesScrollButtons() != overflow:
            self.setUsesScrollButtons(overflow)

    def tabInserted(self, index: int) -> None:  # noqa: N802
        super().tabInserted(index)
        self._update_scroll_buttons()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_scroll_buttons()
        self._set_indicator_now()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._indicator_width <= 0.5:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = QColor(theme.colors()["accent"])
        colour.setAlpha(225)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(
            QRectF(
                self._indicator_x,
                max(0.0, self.height() - 3.5),
                self._indicator_width,
                2.5,
            ),
            1.25,
            1.25,
        )


class SlidingStackedWidget(QStackedWidget):
    """QStackedWidget with a PCL-style horizontal slide for the new page."""

    SLIDE_MS = 280

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MainStack")
        self.setContentsMargins(0, 0, 0, 0)
        self._page: QWidget | None = None
        self._anim: QPropertyAnimation | None = None

    def _cleanup(self) -> None:
        anim_obj = self._anim
        self._anim = None
        if anim_obj is not None:
            anim_obj.stop()
            anim_obj.deleteLater()
        if self._page is not None:
            self._page.move(0, 0)
        self._page = None

    def slide_to(self, index: int) -> None:
        if index < 0 or index >= self.count() or index == self.currentIndex():
            return
        page = self.widget(index)
        if page is None:
            return
        old_index = self.currentIndex()
        self._cleanup()
        self.setCurrentIndex(index)
        if not self.isVisible() or self.width() < 10:
            return
        offset = self.width() if index > old_index else -self.width()
        page.move(offset, 0)
        animation = QPropertyAnimation(page, b"pos", self)
        animation.setDuration(anim.ms(self.SLIDE_MS))
        animation.setStartValue(QPoint(offset, 0))
        animation.setEndValue(QPoint(0, 0))
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._page = page
        self._anim = animation
        animation.finished.connect(self._cleanup)
        animation.start()

    # Backwards-compatible alias for callers/tests written for the fade API.
    def fade_to(self, index: int) -> None:
        self.slide_to(index)


FadingStackedWidget = SlidingStackedWidget


class TabSwitchTransition(QObject):
    """Fade-in plus a slight horizontal shift when a QTabWidget page changes.

    The incoming page fades 0 -> 1 through a QGraphicsOpacityEffect while it
    slides in from SHIFT_WIDTH px. Both animations are plain
    QPropertyAnimation objects parented to the animated page, and the duration
    goes through anim.ms so the global speed slider applies. The tab bar itself
    is untouched and no extra container wraps the pages, so their existing
    layouts keep full control of their geometry.
    """

    FADE_BASE_MS = 160  # 160 ms at 1.0x speed, scaled by anim.ms()
    SHIFT_WIDTH = 8  # px the page slides in from

    def __init__(self, tabs: QTabWidget) -> None:
        super().__init__(tabs)
        self._tabs = tabs
        self._page: QWidget | None = None
        self._effect: QGraphicsOpacityEffect | None = None
        self._animations: list[QPropertyAnimation] = []
        tabs.currentChanged.connect(self._on_current_changed)

    def _settle(self) -> None:
        """Stop the running transition and force the settled end state."""
        animations = self._animations
        self._animations = []
        for animation in animations:
            animation.stop()
            animation.deleteLater()
        page = self._page
        effect = self._effect
        self._page = None
        self._effect = None
        if effect is not None:
            effect.setOpacity(1.0)
        if page is not None:
            page.move(0, page.y())
            page.setGraphicsEffect(None)

    def _on_current_changed(self, index: int) -> None:
        self._settle()
        if not anim.loading_enabled() or not self._tabs.isVisible():
            return
        page = self._tabs.widget(index)
        if page is None or page.width() <= 0 or page.graphicsEffect() is not None:
            return
        rest_y = page.y()
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)
        page.move(self.SHIFT_WIDTH, rest_y)
        duration = anim.ms(self.FADE_BASE_MS)
        fade = QPropertyAnimation(effect, b"opacity", page)
        fade.setDuration(duration)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide = QPropertyAnimation(page, b"pos", page)
        slide.setDuration(duration)
        slide.setStartValue(QPoint(self.SHIFT_WIDTH, rest_y))
        slide.setEndValue(QPoint(0, rest_y))
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._page = page
        self._effect = effect
        self._animations = [fade, slide]
        fade.finished.connect(self._settle)
        slide.finished.connect(self._settle)
        fade.start()
        slide.start()


def install_tab_transition(tabs: QTabWidget) -> TabSwitchTransition:
    """Install the fade/slide page transition on tabs at most once."""
    helper = getattr(tabs, "_tab_switch_transition", None)
    if isinstance(helper, TabSwitchTransition):
        return helper
    helper = TabSwitchTransition(tabs)
    tabs._tab_switch_transition = helper
    return helper


class AnimatedTabView(QWidget):
    """Tab bar + a navigation stack of base tabs and subpages."""

    currentChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MainTabs")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.tab_bar = AnimatedTabBar()
        self.tab_bar.setObjectName("MainTabBar")
        self.stack = FadingStackedWidget()
        layout.addWidget(self.tab_bar)
        layout.addWidget(self.stack, 1)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self._base_count = 0
        self._subpages: list[QWidget] = []
        self._origins: dict[QWidget, int] = {}
        self._retired_pages: list[QWidget] = []

    def _clear_subpages(self) -> None:
        pages = list(self._subpages)
        self._subpages.clear()
        self._origins.clear()
        for page in pages:
            # An embedded dialog page closes its nested event loop through this
            # signal; without it a tab switch would leave the caller blocked.
            back = getattr(page, "back_requested", None)
            if back is not None:
                try:
                    back.emit()
                except RuntimeError:
                    pass
            self.stack.removeWidget(page)
            page.hide()
            self._retired_pages.append(page)
        self._prune_retired_pages()

    def _prune_retired_pages(self) -> None:
        """Forget retired pages once their background threads have finished."""
        alive: list[QWidget] = []
        for page in self._retired_pages:
            try:
                running = any(
                    isinstance(child, QThread) and child.isRunning()
                    for child in page.findChildren(QThread)
                )
            except RuntimeError:
                continue
            if running:
                alive.append(page)
            else:
                page.deleteLater()
        self._retired_pages = alive

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # The tab bar keeps its minimum width, so a narrower view must be told
        # that the tabs no longer fit.
        self.tab_bar._update_scroll_buttons()

    def _on_tab_changed(self, index: int) -> None:
        had_subpages = bool(self._subpages)
        if had_subpages:
            self._clear_subpages()
        if not had_subpages or index != self.stack.currentIndex():
            self.stack.slide_to(index)
        self.currentChanged.emit(index)

    def addTab(self, widget: QWidget, label: str) -> int:
        index = self.stack.addWidget(widget)
        self.tab_bar.addTab(label)
        self._base_count += 1
        if index == 0:
            self.stack.setCurrentIndex(0)
        return index

    def push_page(self, page: QWidget) -> None:
        if self.stack.indexOf(page) >= 0:
            self.stack.slide_to(self.stack.indexOf(page))
            return
        index = self.stack.addWidget(page)
        self._subpages.append(page)
        self._origins[page] = self.stack.currentIndex()
        self.stack.slide_to(index)
        self.currentChanged.emit(self.tab_bar.currentIndex())

    def pop_page(self) -> None:
        if not self._subpages:
            return
        page = self.stack.currentWidget()
        target = self._origins.pop(page, max(0, self.stack.currentIndex() - 1)) if page is not None else 0
        if page is not None:
            self._subpages.remove(page)
        self.stack.slide_to(target)
        if page is not None:
            QTimer.singleShot(anim.ms(320), lambda: self._drop_page(page))

    def _drop_page(self, page: QWidget) -> None:
        if self.stack.indexOf(page) >= 0:
            self.stack.removeWidget(page)
        page.hide()
        # Keep the page alive: a background version-catalog / skin task may
        # still be running and QThread must not be destroyed mid-flight.
        self._retired_pages.append(page)
        self._prune_retired_pages()

    def can_go_back(self) -> bool:
        return bool(self._subpages)

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < self.tab_bar.count():
            self.tab_bar.setCurrentIndex(index)

    def setCurrentWidget(self, widget: QWidget) -> None:
        index = self.stack.indexOf(widget)
        if 0 <= index < self._base_count:
            self.setCurrentIndex(index)

    def currentIndex(self) -> int:
        return self.tab_bar.currentIndex()

    def currentWidget(self) -> QWidget | None:
        return self.stack.currentWidget()

    def count(self) -> int:
        return self._base_count

    def widget(self, index: int) -> QWidget | None:
        return self.stack.widget(index)


class ShimmerProgressBar(QProgressBar):
    """QProgressBar with a moving highlight band over the filled area."""

    _BAND = 84.0
    _STEP = 7.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._shimmer = False
        self._offset = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(28)
        self._timer.timeout.connect(self._advance)
        self.setTextVisible(True)

    def set_shimmer(self, enabled: bool) -> None:
        self._shimmer = enabled
        if enabled and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._shimmer:
            self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def _filled_width(self) -> int:
        span = max(1, self.maximum() - self.minimum())
        ratio = 1.0 if self.maximum() <= 0 else (self.value() - self.minimum()) / span
        return int((self.width() - 2) * max(0.0, min(1.0, ratio)))

    def _period(self, filled: int) -> float:
        """Travel distance of one highlight sweep; shared by timer and paint."""
        return max(1.0, float(filled) + self._BAND * 2.0)

    def _advance(self) -> None:
        self._offset = (self._offset + self._STEP) % self._period(
            max(4, self._filled_width())
        )
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._shimmer or self.width() <= 4:
            return
        if self.maximum() > 0 and self.value() >= self.maximum():
            return
        filled = self._filled_width()
        if filled < 4:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(1, 1, filled, self.height() - 2), 6, 6)
        painter.setClipPath(clip)

        band = self._BAND
        x = (self._offset % self._period(filled)) - band
        gradient = QLinearGradient(x, 0, x + band, 0)
        gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
        gradient.setColorAt(0.5, QColor(255, 255, 255, 60))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(QRectF(0, 0, self.width(), self.height()), gradient)

        # The band must not wash out the label: draw the text again on top.
        painter.setClipping(False)
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)
        self.style().drawControl(
            QStyle.ControlElement.CE_ProgressBarLabel, option, painter
        )


class VersionList(QListWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("VersionList")
        self.setItemDelegate(VersionDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setSpacing(0)

    def add_version(self, data: dict) -> None:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, data)
        item.setSizeHint(QSize(0, 66))
        item.setToolTip(f"{data.get('id', '')} · {TYPE_LABELS.get(str(data.get('type')), '版本')}")
        self.addItem(item)

    def current_data(self) -> dict | None:
        item = self.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def filter_items(self, text: str) -> int:
        needle = text.strip().lower()
        visible = 0
        for row in range(self.count()):
            item = self.item(row)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            haystack = f"{data.get('id', '')} {data.get('type', '')} {TYPE_LABELS.get(str(data.get('type')), '')}".lower()
            hidden = bool(needle) and needle not in haystack
            item.setHidden(hidden)
            if not hidden:
                visible += 1
        return visible


class LogView(QPlainTextEdit):
    """Read-only console with a smoothly animated auto-scroll."""

    # Visible block budget only: the full record history stays in _records so
    # an export is never limited to what the widget still displays.
    MAX_RECORDS = 5000
    FLUSH_INTERVAL_MS = 100

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LogView")
        self.setReadOnly(True)
        self.setMaximumBlockCount(self.MAX_RECORDS)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.auto_scroll = True
        self._records: list[tuple[str, str, str]] = []
        self._pending: list[str] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(self.FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush_pending)
        self._scroll_anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll_anim.setDuration(anim.ms(220))
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def add(self, level: str, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self._records.append((stamp, level, message))
        self._pending.append(self._render(stamp, level, message))
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending(self) -> None:
        """Append every line collected during the throttle window at once."""
        if not self._pending:
            return
        chunk = "<br>".join(self._pending)
        self._pending.clear()
        bar = self.verticalScrollBar()
        old_value = bar.value()
        self.appendHtml(chunk)
        if self.auto_scroll:
            # appendHtml can jump to the end; restore and animate instead.
            bar.setValue(old_value)
            QTimer.singleShot(0, self._smooth_scroll_to_bottom)

    def _smooth_scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        target = bar.maximum()
        if target <= bar.value() + 1:
            bar.setValue(target)
            return
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(bar.value())
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.start()

    def scroll_to_bottom(self) -> None:
        self._smooth_scroll_to_bottom()

    def clear_logs(self) -> None:
        self._scroll_anim.stop()
        self._flush_timer.stop()
        self._pending.clear()
        self._records.clear()
        self.clear()

    def toPlainText(self) -> str:
        # Readers (for example the copy button) must never miss lines that are
        # still waiting in the throttle window.
        if self._pending:
            self._flush_timer.stop()
            self._flush_pending()
        return super().toPlainText()

    def record_count(self) -> int:
        """Number of records retained for export (not the displayed blocks)."""
        return len(self._records)

    def export_text(self) -> str:
        """Plain-text snapshot of every retained record, oldest first."""
        return "\n".join(
            f"[{stamp}] {theme.LOG_LEVELS.get(level, level.upper())} {message}"
            for stamp, level, message in list(self._records)
        )

    def refresh_theme(self) -> None:
        self._flush_timer.stop()
        self._pending.clear()
        records = self._records[-self.MAX_RECORDS :]
        self.clear()
        if records:
            self.appendHtml("<br>".join(self._render(*record) for record in records))
        if self.auto_scroll:
            QTimer.singleShot(0, self._smooth_scroll_to_bottom)

    @staticmethod
    def _render(stamp: str, level: str, message: str) -> str:
        palette = theme.log_colors()
        color = palette.get(level, palette["info"])
        tag = theme.LOG_LEVELS.get(level, level.upper())
        text_color = palette["muted"] if level == "muted" else color
        body = html.escape(str(message)).replace("\n", "<br>")
        return (
            f'<span style="color:{palette["time"]};">[{stamp}]</span> '
            f'<span style="color:{color}; font-weight:600;">{tag}</span> '
            f'<span style="color:{text_color};">{body}</span>'
        )
