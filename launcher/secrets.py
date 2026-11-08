"""DPAPI-protected key/value store for API keys and other secrets.

Secrets never enter ``launcher_config.json`` so the config file can be shared
when reporting a bug without leaking credentials.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from .dpapi import protect, unprotect
from .paths import data_dir

SECRETS_PATH = data_dir() / "secrets.json"


def _load_raw(path: Path = SECRETS_PATH) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load_secret(name: str, path: Path = SECRETS_PATH) -> str:
    raw = _load_raw(path)
    entry = raw.get(name)
    if not isinstance(entry, dict) or not entry.get("data"):
        return ""
    try:
        plain = unprotect(base64.b64decode(entry["data"]))
        return plain.decode("utf-8")
    except (OSError, ValueError, KeyError):
        return ""


def save_secret(name: str, value: str, path: Path = SECRETS_PATH) -> None:
    raw = _load_raw(path)
    if value:
        raw[name] = {
            "dpapi": True,
            "data": base64.b64encode(protect(value.encode("utf-8"))).decode("ascii"),
        }
    else:
        raw.pop(name, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def clear_secret(name: str, path: Path = SECRETS_PATH) -> None:
    save_secret(name, "", path)
