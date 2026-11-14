"""CoreNextLauncher desktop entry point.

Usage:
    python main.py
    python main.py --game-dir "D:/path/to/.minecraft"
    python main.py --theme light
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# High-DPI settings must be in place before QApplication is constructed.  Qt 6
# enables high-DPI scaling unconditionally, but the default rounding policy can
# round 125%/150% displays to 100%/200%, which makes a 2K/4K UI blurry.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from launcher import APP_NAME, anim
from launcher.config import load_config
from launcher.core_client import CoreClient
from launcher.main_window import MainWindow
from launcher.paths import ensure_workspace
from launcher.theme import apply_theme, make_app_icon
from launcher.ui.splash import create_splash
from launcher.ui.account_dialog import shutdown_workers
from launcher.ui.tasks import shutdown_tasks


def configure_high_dpi() -> None:
    """Use fractional Windows scaling instead of snapping to 100/200%."""
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except (AttributeError, RuntimeError):
        # Older Qt builds keep their platform default.
        pass



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CoreNextLauncher Minecraft 启动器")
    parser.add_argument("--game-dir", help="覆盖 .minecraft 目录")
    parser.add_argument("--exe", help="覆盖 mc_core.exe 路径")
    parser.add_argument("--theme", choices=("dark", "light"), help="启动时使用的主题")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    configure_high_dpi()
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("CoreNextLauncher")
    app.setStyle("Fusion")
    app.aboutToQuit.connect(shutdown_workers)
    app.aboutToQuit.connect(shutdown_tasks)

    ensure_workspace()
    config = load_config()
    if args.game_dir:
        config.game_dir = str(Path(args.game_dir))
    if args.theme:
        config.ui_style = "html" if args.theme == "light" else "liquid"
    style = config.ui_style
    profile = config.theme_profiles.get(style, {})
    apply_theme(
        app,
        style,
        str(profile.get("base") or ("light" if style in ("frosted", "html") else "dark")),
        str(profile.get("primary") or "#4f8cff"),
        str(profile.get("accent") or "#7b5cff"),
    )
    app.setWindowIcon(make_app_icon())
    # The startup scene obeys the same global switch and speed as every other
    # scene, so the persisted settings are applied before the splash exists.
    anim.set_speed(getattr(config, "animation_speed", 1.0))
    anim.set_loading_enabled(getattr(config, "loading_animation", True))

    splash = create_splash(config)
    if splash is not None:
        splash.show()
        app.processEvents()

    client = CoreClient(args.exe) if args.exe else CoreClient()
    window = MainWindow(client, config)
    window.show()
    if splash is not None:
        splash.finish(window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
