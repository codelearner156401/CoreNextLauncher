"""Theme engine: three UI styles, colour presets, QSS and native acrylic."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

THEME_ORDER = ("dark", "light")
THEME_LABELS = {"dark": "暗黑", "light": "明亮"}
STYLE_ORDER = ("liquid", "frosted", "html")
STYLE_LABELS = {
    "liquid": "液态玻璃",
    "frosted": "毛玻璃",
    "html": "HTML 界面",
}
STYLE_BASE = {"liquid": "dark", "frosted": "light", "html": "light"}
THEME_DIR = Path(__file__).resolve().parent / "resources" / "themes"

COLOR_PRESETS: dict[str, tuple[str, str]] = {
    "white": ("#94a3b8", "#cbd5e1"),
    "black": ("#64748b", "#475569"),
    "blue": ("#4f8cff", "#7b5cff"),
    "green": ("#22c55e", "#10b981"),
    "orange": ("#f97316", "#fb923c"),
    "pink": ("#ec4899", "#f472b6"),
    "purple": ("#8b5cf6", "#a78bfa"),
    "cyan": ("#06b6d4", "#22d3ee"),
    "yellow": ("#eab308", "#facc15"),
    "red": ("#ef4444", "#f87171"),
}
PRESET_LABELS = {
    "white": "白色",
    "black": "黑色",
    "blue": "蓝色",
    "green": "绿色",
    "orange": "橙色",
    "pink": "粉色",
    "purple": "紫色",
    "cyan": "青色",
    "yellow": "黄色",
    "red": "红色",
}

_current = "dark"
_style = "liquid"
_primary = "#4f8cff"
_accent = "#7b5cff"

COLORS: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0b0e14",
        "card": "#10151f",
        "elevated": "#121722",
        "input": "#0e131c",
        "border": "#1e2635",
        "border_strong": "#2a3548",
        "text": "#e6edf6",
        "muted": "#7d8799",
        "accent": "#4f8cff",
        "accent2": "#7b5cff",
        "hover": "#171f2d",
        "selected": "#1a2740",
        "badge_bg": "#1b2a44",
        "badge_text": "#8ab4f8",
        "ok": "#3ddc97",
        "warn": "#f5c451",
        "err": "#ff7b72",
        "event": "#c792ea",
        "log_bg": "#090d13",
    },
    "light": {
        "bg": "#f4f6fb",
        "card": "#ffffff",
        "elevated": "#ffffff",
        "input": "#f7f9fc",
        "border": "#e2e8f0",
        "border_strong": "#cbd5e1",
        "text": "#1f2937",
        "muted": "#64748b",
        "accent": "#2563eb",
        "accent2": "#7c3aed",
        "hover": "#f1f5f9",
        "selected": "#e8f0fe",
        "badge_bg": "#e8f0fe",
        "badge_text": "#1d4ed8",
        "ok": "#15803d",
        "warn": "#b45309",
        "err": "#dc2626",
        "event": "#7c3aed",
        "log_bg": "#ffffff",
    },
}

LOG_LEVELS = {
    "info": "INFO",
    "success": " OK ",
    "warn": "WARN",
    "error": "ERR ",
    "event": "EVT ",
    "muted": "···",
}


def current_theme() -> str:
    return _current


def current_style() -> str:
    return _style


def current_primary() -> str:
    return _primary


def current_accent() -> str:
    return _accent


def _mix(color: str, other: str, ratio: float) -> str:
    a = QColor(color)
    b = QColor(other)
    r = int(a.red() * (1 - ratio) + b.red() * ratio)
    g = int(a.green() * (1 - ratio) + b.green() * ratio)
    bl = int(a.blue() * (1 - ratio) + b.blue() * ratio)
    return QColor(r, g, bl).name()


def _for_base(color: str, base: str) -> str:
    if base != "light":
        return color
    value = QColor(color)
    if value.lightness() > 170:
        return _mix(color, "#0f172a", 0.35)
    return color


def preset_colors(name: str, base: str = "dark") -> tuple[str, str]:
    primary, accent = COLOR_PRESETS.get(name, COLOR_PRESETS["blue"])
    return _for_base(primary, base), _for_base(accent, base)


def colors(theme: str | None = None) -> dict[str, str]:
    name = theme if theme in THEME_ORDER else _current
    palette = dict(COLORS.get(name, COLORS["dark"]))
    palette["accent"] = _primary if name == _current else palette["accent"]
    palette["accent2"] = _accent if name == _current else palette["accent2"]
    if name == _current:
        palette["badge_text"] = _primary
        palette["selected"] = _mix(_primary, palette["card"], 0.82)
        palette["hover"] = _mix(_primary, palette["card"], 0.92)
    return palette


def log_colors(theme: str | None = None) -> dict[str, str]:
    c = colors(theme)
    return {
        "time": c["muted"],
        "info": c["text"],
        "muted": c["muted"],
        "success": c["ok"],
        "warn": c["warn"],
        "error": c["err"],
        "event": c["event"],
        "accent": c["accent"],
    }


def stylesheet(theme: str, primary: str | None = None, accent: str | None = None) -> str:
    name = theme if theme in THEME_ORDER else "dark"
    path = THEME_DIR / f"{name}.qss"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    icon = (THEME_DIR / "check.png").as_posix()
    text = text.replace("__CHECK_ICON__", icon)
    primary = primary or _primary
    accent = accent or _accent
    if name == "dark":
        replacements = {
            "#4f8cff": primary,
            "#7b5cff": accent,
            "#2f6fed": _mix(primary, "#0b0e14", 0.18),
            "#8ab4f8": _mix(primary, "#ffffff", 0.35),
            "#1c2a44": _mix(primary, "#10151f", 0.8),
        }
    else:
        replacements = {
            "#2563eb": primary,
            "#7c3aed": accent,
            "#1d4ed8": _mix(primary, "#0f172a", 0.2),
            "#e8f0fe": _mix(primary, "#ffffff", 0.85),
            "#dbe7ff": _mix(primary, "#ffffff", 0.8),
        }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def apply_theme(
    app: QApplication,
    style: str = "liquid",
    base: str | None = None,
    primary: str | None = None,
    accent: str | None = None,
) -> None:
    """Apply a UI style.  ``style`` may also be ``dark``/``light`` for legacy."""
    global _current, _style, _primary, _accent
    if style in THEME_ORDER:
        base = style
        style = "liquid"
    if style not in STYLE_ORDER:
        style = "liquid"
    base = base if base in THEME_ORDER else STYLE_BASE[style]
    _style = style
    _current = base
    _primary = primary or COLORS[base]["accent"]
    _accent = accent or COLORS[base]["accent2"]

    app.setStyleSheet(stylesheet(base, _primary, _accent))
    c = colors(base)
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(c["input"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(c["elevated"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(c["elevated"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["elevated"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    app.setPalette(palette)
    app.setProperty("theme", base)
    app.setProperty("ui_style", _style)


def make_app_icon(size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor(_primary))
    gradient.setColorAt(1.0, QColor(_accent))
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.PenStyle.NoPen)
    radius = size * 0.24
    painter.drawRoundedRect(2, 2, size - 4, size - 4, radius, radius)
    font = QFont("Segoe UI")
    font.setPixelSize(int(size * 0.56))
    font.setWeight(QFont.Weight.Black)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "C")
    painter.end()
    return QIcon(pixmap)


def apply_window_effects(window: QWidget, theme_key: str | None = None) -> None:
    """Rounded corners + dark native frame on Windows."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        if not hwnd:
            return
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1 if (theme_key or _current) == "dark" else 0)
        size = ctypes.sizeof(dark)
        for attribute in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(dark), size) == 0:
                break
        corner = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner)
        )
    except Exception:
        pass


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class _WindowCompositionAttributeData(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def apply_acrylic(
    window: QWidget,
    enabled: bool,
    tint: str = "#0b0e14",
    alpha: int = 150,
) -> bool:
    """Enable real system acrylic behind a translucent window.

    Windows 11 uses DWMWA_SYSTEMBACKDROP_TYPE (transient/acrylic).  Windows 10
    falls back to SetWindowCompositionAttribute with ACCENT_ENABLE_ACRYLICBLUR.
    """
    if sys.platform != "win32":
        return False
    try:
        hwnd = int(window.winId())
        if not hwnd:
            return False
        dwm = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32

        class _Margins(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_int),
                ("right", ctypes.c_int),
                ("top", ctypes.c_int),
                ("bottom", ctypes.c_int),
            ]

        margins = _Margins(-1, -1, -1, -1)
        try:
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        except Exception:
            pass

        backdrop_ok = False
        # DWMWA_SYSTEMBACKDROP_TYPE = 38; 3 = transient acrylic, 1 = none.
        try:
            state = ctypes.c_int(3 if enabled else 1)
            backdrop_ok = (
                dwm.DwmSetWindowAttribute(
                    hwnd, 38, ctypes.byref(state), ctypes.sizeof(state)
                )
                == 0
            )
        except Exception:
            backdrop_ok = False

        policy = _AccentPolicy()
        if enabled:
            color = QColor(tint)
            rgba = (
                (max(0, min(255, alpha)) << 24)
                | (color.blue() << 16)
                | (color.green() << 8)
                | color.red()
            )
            policy.AccentState = 4  # ACCENT_ENABLE_ACRYLICBLURBEHIND
            policy.AccentFlags = 2
            policy.GradientColor = rgba
        else:
            policy.AccentState = 0
        data = _WindowCompositionAttributeData()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(policy), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(policy)
        try:
            accent_ok = bool(
                user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
            )
        except Exception:
            accent_ok = False
        if not enabled:
            return True
        return bool(backdrop_ok or accent_ok)
    except Exception:
        return False
