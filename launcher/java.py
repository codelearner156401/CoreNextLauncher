"""Java executable discovery helpers.

The settings field accepts a JDK root, a ``bin`` directory, ``java.exe`` or
``javaw.exe``.  The launcher normalises that choice to an executable before it
forwards the path to the C++ core.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _clean(value: str) -> str:
    text = str(value or "").strip()
    while len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()
    return text


def normalize_java_path(value: str) -> str:
    raw = _clean(value)
    if not raw:
        return "java"

    path = Path(raw).expanduser()
    try:
        if path.is_dir():
            bin_dir = path if path.name.lower() == "bin" else path / "bin"
            names = ("javaw.exe", "java.exe") if os.name == "nt" else ("java",)
            for name in names:
                candidate = bin_dir / name
                if candidate.is_file():
                    return str(candidate.resolve())
            # Keep a predictable path for a clear "not found" warning later.
            return str((bin_dir / names[0]).resolve())
        if path.is_file():
            if os.name == "nt" and path.name.lower() == "java.exe":
                javaw = path.with_name("javaw.exe")
                if javaw.is_file():
                    return str(javaw.resolve())
            return str(path.resolve())
    except (OSError, RuntimeError):
        pass

    # A bare command (for example "java") can be resolved through PATH.
    if not any(separator in raw for separator in ("/", "\\")):
        found = shutil.which(raw)
        if found:
            return found
    return raw
