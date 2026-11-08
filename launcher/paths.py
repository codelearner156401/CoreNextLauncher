"""Runtime paths that work both from source and from a PyInstaller bundle."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Directory that contains bundled resources (``sys._MEIPASS`` when frozen)."""
    if is_frozen():
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            return Path(bundle)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Writable directory for launcher_config.json and future caches."""
    if is_frozen():
        roaming = os.environ.get("APPDATA")
        base = Path(roaming) if roaming else Path.home() / ".config"
        return base / "CoreNextLauncher"
    return app_root()
