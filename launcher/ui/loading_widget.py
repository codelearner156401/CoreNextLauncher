"""Thin blue C loading ring: rotating C path + static SVG ground line.

The source SVG (launcher/resources/loading_c.svg) holds two independent
elements: the 270 degree C path and the ground line.  Rendering the whole
document would rotate the ground line together with the ring, so the document
is split at load time into a ring-only and a line-only QSvgRenderer.  The
element markup itself is copied verbatim - the d, stroke, stroke-width and the
line coordinates are never rewritten here.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import (
    Property,
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRectF,
    QSequentialAnimationGroup,
    Qt,
)
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from .. import anim

SVG_PATH = Path(__file__).resolve().parent.parent / "resources" / "loading_c.svg"

# Inline fallback used when the resource file is missing or unreadable.  It is
# byte-identical to launcher/resources/loading_c.svg.
SVG_FALLBACK = """<svg width="200" height="200" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
  <!-- C环，270°圆弧，90°中等缺口，蓝色，线宽4 -->
  <path d="M150,100 A50,50 0 1,1 100,50" stroke="#4299ff" stroke-width="4" fill="none"/>
  <!-- 地面横线，加长，浅灰，线宽2，独立元素，不会随C环旋转弹跳 -->
  <line x1="40" y1="160" x2="160" y2="160" stroke="#aaaaaa" stroke-width="2"/>
</svg>
"""

VIEWBOX_UNITS = 200.0  # viewBox edge
RING_UNITS = 100.0  # C path diameter in viewBox units
GROUND_UNITS = 160.0  # line y in viewBox units

_ELEMENT_RE = re.compile(r"<(path|line)\b[^>]*?/>", re.IGNORECASE)

_SVG_HEADER = (
    '<svg width="200" height="200" viewBox="0 0 200 200" '
    'xmlns="http://www.w3.org/2000/svg">'
)


def load_svg_source() -> str:
    """Return the SVG text, falling back to the embedded copy."""
    try:
        text = SVG_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return SVG_FALLBACK
    return text if "<svg" in text else SVG_FALLBACK


def split_svg(source: str) -> tuple[str, str]:
    """Split the document into a ring-only and a line-only SVG document.

    Both documents keep the original viewBox so a user-space coordinate maps to
    the same pixel offset in either renderer; the elements themselves are
    emitted byte for byte as they appear in source.
    """
    path_tag = ""
    line_tag = ""
    for match in _ELEMENT_RE.finditer(source):
        tag = match.group(0)
        name = match.group(1).lower()
        if name == "path" and not path_tag:
            path_tag = tag
        elif name == "line" and not line_tag:
            line_tag = tag
    return (
        _SVG_HEADER + path_tag + "</svg>",
        _SVG_HEADER + line_tag + "</svg>",
    )


def _renderer(document: str) -> QSvgRenderer | None:
    renderer = QSvgRenderer(document.encode("utf-8"))
    return renderer if renderer.isValid() else None


def build_renderers(
    source: str | None = None,
) -> tuple[QSvgRenderer | None, QSvgRenderer | None]:
    """Return (ring_renderer, line_renderer) built from the split SVG."""
    text = load_svg_source() if source is None else source
    ring_doc, line_doc = split_svg(text)
    ring = _renderer(ring_doc)
    line = _renderer(line_doc)
    if ring is None and line is None:
        # Unparsable markup: keep a whole-document ring and drop the line.
        ring = _renderer(text)
    return ring, line


class LineCLoading(QWidget):
    """A fixed-size C ring that rotates and falls while the ground stays put.

    Two infinite animations run inside one QParallelAnimationGroup: a 3000 ms
    (at speed 1.0) linear clockwise rotation of the C path and a fall/rebound
    sequence of the same length.  Only the ring is transformed - the ground
    line is painted from its own renderer with no rotation and no offset.
    """

    SIZE = 44  # rendered diameter of the C ring in pixels
    GROUND_MARGIN = 26  # ground line distance from the widget bottom
    BOUNCE_HEIGHT = 30.0  # pixel travel of the bounce
    CYCLE_MS = 3000

    def __init__(self, parent=None, ground: bool = True) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._rotation = 0.0
        self._bounce = 0.0
        self._ground = bool(ground)
        self._ring, self._line = build_renderers()
        self.setFixedSize(120, 150)

        self._group = QParallelAnimationGroup(self)
        self._rotation_anim = QPropertyAnimation(self, b"rotation", self)
        self._rotation_anim.setStartValue(0.0)
        self._rotation_anim.setEndValue(360.0)
        self._rotation_anim.setEasingCurve(QEasingCurve.Type.Linear)

        self._fall = QPropertyAnimation(self, b"bounce", self)
        self._fall.setStartValue(self.BOUNCE_HEIGHT)
        self._fall.setEndValue(0.0)
        self._fall.setEasingCurve(QEasingCurve.Type.InQuad)
        self._rise = QPropertyAnimation(self, b"bounce", self)
        self._rise.setStartValue(0.0)
        self._rise.setEndValue(self.BOUNCE_HEIGHT)
        self._rise.setEasingCurve(QEasingCurve.Type.OutQuad)

        self._bounce_seq = QSequentialAnimationGroup(self)
        self._bounce_seq.addAnimation(self._fall)
        self._bounce_seq.addAnimation(self._rise)

        self._group.addAnimation(self._rotation_anim)
        self._group.addAnimation(self._bounce_seq)
        self._group.setLoopCount(-1)
        self.apply_timing()

    # -------------------------------------------------------------- lifespan
    def apply_timing(self) -> None:
        """Re-scale every duration so speed 1.0 keeps the 3000 ms period."""
        total = anim.ms(self.CYCLE_MS)
        fall = total // 2
        self._rotation_anim.setDuration(total)
        self._fall.setDuration(fall)
        self._rise.setDuration(total - fall)

    def set_loading_enabled(self, enabled: bool) -> None:
        """Apply the global switch to this instance immediately."""
        anim.set_loading_enabled(enabled)
        if anim.loading_enabled():
            if self.isVisible():
                self.start()
            return
        self.stop()
        self.set_rotation(0.0)
        self.set_bounce(0.0)

    def start(self) -> None:
        if not anim.loading_enabled():
            self.stop()
            return
        self.apply_timing()
        if self._group.state() == QAbstractAnimation.State.Running:
            return
        self._group.start()

    def stop(self) -> None:
        """Idempotent: stopping a stopped group is a no-op, no timer survives."""
        if self._group.state() != QAbstractAnimation.State.Stopped:
            self._group.stop()

    def is_running(self) -> bool:
        return self._group.state() == QAbstractAnimation.State.Running

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------ properties
    def get_rotation(self) -> float:
        return self._rotation

    def set_rotation(self, value: float) -> None:
        self._rotation = float(value)
        self.update()

    rotation = Property(float, get_rotation, set_rotation)

    def get_bounce(self) -> float:
        return self._bounce

    def set_bounce(self, value: float) -> None:
        self._bounce = float(value)
        self.update()

    bounce = Property(float, get_bounce, set_bounce)

    # --------------------------------------------------------------- drawing
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scale = self.SIZE / RING_UNITS
        span = VIEWBOX_UNITS * scale
        ground_y = float(self.height() - self.GROUND_MARGIN)
        centre_x = self.width() / 2.0
        # bounce 0 rests the lowest point of the ring on the ground line.
        centre_y = ground_y - self.SIZE / 2.0 - self._bounce

        if self._ring is not None:
            painter.save()
            painter.translate(centre_x, centre_y)
            painter.rotate(self._rotation)
            self._ring.render(painter, QRectF(-span / 2.0, -span / 2.0, span, span))
            painter.restore()

        if self._ground and self._line is not None:
            # Independent renderer: no rotate() and no bounce offset here, so
            # the ground line can never move.
            self._line.render(
                painter,
                QRectF(
                    centre_x - span / 2.0,
                    ground_y - GROUND_UNITS * scale,
                    span,
                    span,
                ),
            )
        painter.end()
