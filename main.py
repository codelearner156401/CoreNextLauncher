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

from launcher import APP_NAME
from launcher.config import load_config
from launcher.core_client import CoreClient
from launcher.main_window import MainWindow
from launcher.theme import apply_theme, make_app_icon
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
    app.setWindowIcon(make_app_icon())
    app.aboutToQuit.connect(shutdown_workers)
    app.aboutToQuit.connect(shutdown_tasks)

    config = load_config()
    if args.game_dir:
        config.game_dir = str(Path(args.game_dir))
    if args.theme:
        config.theme = args.theme
    apply_theme(app, config.theme)

    client = CoreClient(args.exe) if args.exe else CoreClient()
    window = MainWindow(client, config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
