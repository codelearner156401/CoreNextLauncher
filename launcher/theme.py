"""Theme registry: QSS loading, palette and painting colours."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

THEME_ORDER = ("dark", "light")
THEME_LABELS = {"dark": "暗黑", "light": "明亮"}
THEME_DIR = Path(__file__).resolve().parent / "resources" / "themes"

_current = "dark"

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


def colors(theme: str | None = None) -> dict[str, str]:
    return COLORS.get(theme or _current, COLORS["dark"])


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


def stylesheet(theme: str) -> str:
    name = theme if theme in THEME_ORDER else "dark"
    path = THEME_DIR / f"{name}.qss"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    # QSS loaded from a string has no base URL, so resolve the checkmark icon
    # to an absolute path here (works both from source and from PyInstaller).
    icon = (THEME_DIR / "check.png").as_posix()
    return text.replace("__CHECK_ICON__", icon)


def apply_theme(app: QApplication, theme: str) -> None:
    """Load the QSS for *theme* and keep the widget palette in sync."""
    global _current
    name = theme if theme in THEME_ORDER else "dark"
    _current = name
    c = colors(name)
    app.setStyleSheet(stylesheet(name))

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

    # Make sure property-based selectors (e.g. [state="ok"]) re-evaluate.
    app.setProperty("theme", name)


def make_app_icon(size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#4f8cff"))
    gradient.setColorAt(1.0, QColor("#7b5cff"))
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
    """Match the native title bar to the app theme on Windows.

    Uses DwmSetWindowAttribute: immersive dark mode plus rounded corners.  All
    calls fail softly on older Windows versions, where the title bar simply
    keeps its system appearance.
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        if not hwnd:
            return
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1 if (theme_key or _current) == "dark" else 0)
        size = ctypes.sizeof(dark)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 11 / 10 2004+),
        # 19 = the pre-2004 attribute number.
        for attribute in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(dark), size) == 0:
                break
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2.
        corner = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner)
        )
    except Exception:
        pass
