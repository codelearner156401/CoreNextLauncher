"""Persisted launcher settings."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import anim
from .constants import DEFAULT_AI_MODEL, DEFAULT_MS_CLIENT_ID, DEFAULT_UPDATE_REPO
from .paths import app_root, data_dir, default_instance_dir
from .services.jvm import PRESET_LABELS, sanitize_args

PROJECT_ROOT = app_root()
CONFIG_PATH = data_dir() / "launcher_config.json"

UI_STYLES = ("liquid", "frosted", "html")

_STYLE_DEFAULTS: dict[str, dict] = {
    "liquid": {
        "color": "blue",
        "primary": "#4f8cff",
        "accent": "#7b5cff",
        "base": "dark",
        "glass": True,
        "system_blur": True,
        "blur": 26,
        "dim": 42,
        "background": "",
        "splash": "",
    },
    "frosted": {
        "color": "blue",
        "primary": "#2563eb",
        "accent": "#7c3aed",
        "base": "light",
        "glass": True,
        "system_blur": True,
        "blur": 34,
        "dim": 30,
        "background": "",
        "splash": "",
    },
    "html": {
        "color": "blue",
        "primary": "#2563eb",
        "accent": "#7c3aed",
        "base": "light",
        "glass": False,
        "system_blur": True,
        "blur": 0,
        "dim": 0,
        "background": "",
        "splash": "",
    },
}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _default_profile(style: str) -> dict:
    return dict(_STYLE_DEFAULTS.get(style, _STYLE_DEFAULTS["liquid"]))


def _absolute_drive(value: str) -> str:
    """Repair a bare or relative drive prefix: "D:" -> "D:\\", "D:MC" -> "D:\\MC".

    pathlib treats a bare drive prefix as a *relative* path (Path("D:").is_absolute()
    is False and str(Path("D:") / "MC") is "D:MC"), so the separator is restored here.
    Other values pass through untouched.
    """
    if not _DRIVE_RE.match(value):
        return value
    rest = value[2:]
    if rest[:1] in ("\\", "/"):
        return value
    return value[:2] + "\\" + rest


def path_is_absolute(value: str) -> bool:
    """True when value points at an absolute location (a bare "D:" counts)."""
    text = os.path.expanduser(str(value or "").strip())
    if not text:
        return False
    return Path(_absolute_drive(text)).is_absolute()


def normalize_directory(value: str) -> str:
    """Return value as an absolute path, or "" when it cannot be one.

    strip + expanduser, then a bare/relative drive prefix is repaired
    ("D:" -> "D:\\", "D:MC" -> "D:\\MC") and the result is resolved.  Relative and
    empty input return "" so the caller can warn; nothing is created on disk and the
    target is deliberately not required to exist.
    """
    text = os.path.expanduser(str(value or "").strip())
    if not text:
        return ""
    candidate = _absolute_drive(text)
    if not Path(candidate).is_absolute():
        return ""
    try:
        return str(Path(candidate).resolve())
    except OSError:
        return ""


@dataclass
class AppConfig:
    game_dir: str = ""
    java_path: str = "java"
    max_mem: str = "auto"
    min_mem: str = "auto"
    player_name: str = "Steve"
    theme: str = "dark"
    last_version: str = ""
    microsoft_client_id: str = DEFAULT_MS_CLIENT_ID
    background_image: str = ""
    glass_enabled: bool = True
    glass_blur: int = 26
    background_dim: int = 42
    # --- v1.8 appearance / animation -----------------------------------
    ui_style: str = "liquid"
    theme_profiles: dict = field(default_factory=dict)
    animation_speed: float = 1.0
    loading_animation: bool = True
    # --- automation / 百宝箱 -------------------------------------------
    auto_backup: bool = True
    backup_keep: int = 5
    auto_java_install: bool = True
    update_repo: str = DEFAULT_UPDATE_REPO
    auto_update_check: bool = True
    ai_model: str = DEFAULT_AI_MODEL
    # --- JVM -----------------------------------------------------------
    jvm_preset: str = "auto"
    jvm_args: list[str] = field(default_factory=list)
    # --- multiplayer / EasyTier ----------------------------------------
    # Shared rendezvous nodes, one per line (or comma separated). An empty
    # value means "use the built-in default nodes".
    mp_peer_nodes: str = ""

    def normalized(self) -> "AppConfig":
        """Fill in a sensible default game directory."""
        if not self.game_dir:
            self.game_dir = str(default_instance_dir())
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
        if not isinstance(self.update_repo, str) or not self.update_repo.strip():
            self.update_repo = DEFAULT_UPDATE_REPO
        self.update_repo = self.update_repo.strip().removeprefix("https://github.com/").strip("/")
        if not isinstance(self.ai_model, str) or not self.ai_model.strip():
            self.ai_model = DEFAULT_AI_MODEL
        self.ai_model = self.ai_model.strip()
        if self.jvm_preset not in PRESET_LABELS:
            self.jvm_preset = "auto"
        self.jvm_args = sanitize_args(self.jvm_args)
        nodes = getattr(self, "mp_peer_nodes", "")
        # Tolerate hand-edited/legacy files: only text can be a node list.
        self.mp_peer_nodes = nodes.strip() if isinstance(nodes, str) else ""

        # --- v1.8 three UI styles with independent profiles ----------------
        self.animation_speed = anim.clamp(getattr(self, "animation_speed", 1.0))
        self.loading_animation = bool(getattr(self, "loading_animation", True))
        if self.ui_style not in UI_STYLES:
            self.ui_style = "liquid"
        raw_profiles = self.theme_profiles if isinstance(self.theme_profiles, dict) else {}
        had_profiles = bool(raw_profiles)
        profiles: dict[str, dict] = {}
        for style in UI_STYLES:
            merged = _default_profile(style)
            source = raw_profiles.get(style)
            if isinstance(source, dict):
                for key in merged:
                    if key in source:
                        merged[key] = source[key]
            if not had_profiles and style == "liquid":
                merged["background"] = self.background_image
                merged["glass"] = self.glass_enabled
                merged["blur"] = self.glass_blur
                merged["dim"] = self.background_dim
            merged["base"] = _STYLE_DEFAULTS[style]["base"]
            merged["glass"] = bool(merged.get("glass", True))
            merged["system_blur"] = bool(merged.get("system_blur", True))
            try:
                merged["blur"] = max(0, min(48, int(merged.get("blur", 26))))
            except (TypeError, ValueError):
                merged["blur"] = 26
            try:
                merged["dim"] = max(0, min(80, int(merged.get("dim", 42))))
            except (TypeError, ValueError):
                merged["dim"] = 42
            for key in ("primary", "accent"):
                value = str(merged.get(key) or "")
                if not _HEX_RE.match(value):
                    merged[key] = _STYLE_DEFAULTS[style][key]
            for key in ("background", "splash", "color"):
                merged[key] = str(merged.get(key) or "")
            profiles[style] = merged
        self.theme_profiles = profiles
        return self

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize_loaded_paths(config: AppConfig) -> None:
    """Repair drive-relative path values saved by older versions ("D:MC").

    Only values normalize_directory() can turn into an absolute path are rewritten;
    anything else (a relative path, a URL, an empty string) is left untouched.
    """
    for name in ("game_dir", "background_image"):
        raw = getattr(config, name, "")
        if isinstance(raw, str) and raw.strip():
            fixed = normalize_directory(raw)
            if fixed:
                setattr(config, name, fixed)
    profiles = config.theme_profiles if isinstance(config.theme_profiles, dict) else {}
    for profile in profiles.values():
        if not isinstance(profile, dict):
            continue
        for key in ("background", "splash"):
            raw = profile.get(key)
            if isinstance(raw, str) and raw.strip():
                fixed = normalize_directory(raw)
                if fixed:
                    profile[key] = fixed


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    config = AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            raw = json.loads(backup.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return config.normalized()
    if isinstance(raw, dict):
        known = {f.name for f in fields(AppConfig)}
        for key, value in raw.items():
            if key in known:
                setattr(config, key, value)
    _normalize_loaded_paths(config)
    return config.normalized()


def save_config(config: AppConfig, path: Path = CONFIG_PATH) -> None:
    payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    if path.is_file():
        try:
            os.replace(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
    os.replace(tmp, path)
