"""Persisted launcher settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .constants import DEFAULT_AI_MODEL, DEFAULT_MS_CLIENT_ID, DEFAULT_UPDATE_REPO
from .paths import app_root, data_dir

PROJECT_ROOT = app_root()
CONFIG_PATH = data_dir() / "launcher_config.json"


@dataclass
class AppConfig:
    game_dir: str = ""
    java_path: str = "java"
    max_mem: str = "auto"
    min_mem: str = "auto"
    player_name: str = "Steve"
    offline: bool = False
    offline_ack: bool = False
    developer_unlocked: bool = False
    theme: str = "dark"
    last_version: str = ""
    microsoft_client_id: str = DEFAULT_MS_CLIENT_ID
    background_image: str = ""
    glass_enabled: bool = True
    glass_blur: int = 26
    background_dim: int = 42
    # --- automation / 百宝箱 -------------------------------------------
    auto_backup: bool = True
    backup_keep: int = 5
    auto_java_install: bool = True
    update_repo: str = DEFAULT_UPDATE_REPO
    auto_update_check: bool = True
    ai_model: str = DEFAULT_AI_MODEL

    def normalized(self) -> "AppConfig":
        """Fill in a sensible default game directory."""
        if not self.game_dir:
            roaming = Path(os.environ.get("APPDATA", str(Path.home())))
            real = roaming / ".minecraft"
            demo = PROJECT_ROOT / "demo" / ".minecraft"
            if real.is_dir():
                self.game_dir = str(real)
            elif (demo / "versions").is_dir():
                self.game_dir = str(demo)
            else:
                self.game_dir = str(real)
        if self.theme not in {"dark", "light"}:
            self.theme = "dark"
        if not self.microsoft_client_id:
            self.microsoft_client_id = DEFAULT_MS_CLIENT_ID
        try:
            self.glass_blur = max(0, min(48, int(self.glass_blur)))
        except (TypeError, ValueError):
            self.glass_blur = 26
        try:
            self.background_dim = max(0, min(80, int(self.background_dim)))
        except (TypeError, ValueError):
            self.background_dim = 42
        if not isinstance(self.background_image, str):
            self.background_image = ""
        self.glass_enabled = bool(self.glass_enabled)
        self.auto_backup = bool(self.auto_backup)
        self.auto_java_install = bool(self.auto_java_install)
        self.auto_update_check = bool(self.auto_update_check)
        try:
            self.backup_keep = max(1, min(30, int(self.backup_keep)))
        except (TypeError, ValueError):
            self.backup_keep = 5
        if not isinstance(self.update_repo, str):
            self.update_repo = DEFAULT_UPDATE_REPO
        self.update_repo = self.update_repo.strip().removeprefix("https://github.com/").strip("/")
        if not isinstance(self.ai_model, str) or not self.ai_model.strip():
            self.ai_model = DEFAULT_AI_MODEL
        self.ai_model = self.ai_model.strip()
        # Offline mode is opt-in and must never be inherited silently from an
        # older config that predates the confirmation warning.
        if self.offline and not self.offline_ack:
            self.offline = False
        if not self.offline:
            self.offline_ack = False
        if self.offline and self.offline_ack:
            self.developer_unlocked = True
        return self

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    config = AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config.normalized()
    if isinstance(raw, dict):
        known = {f.name for f in fields(AppConfig)}
        for key, value in raw.items():
            if key in known:
                setattr(config, key, value)
    return config.normalized()


def save_config(config: AppConfig, path: Path = CONFIG_PATH) -> None:
    payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, path)
