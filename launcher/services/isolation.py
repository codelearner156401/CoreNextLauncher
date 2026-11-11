"""Per-version resource isolation inside a Minecraft instance."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

STATE_NAME = "isolation.json"
ISOLATED_DIRS = ("mods", "config", "saves", "resourcepacks")


def state_path(instance: str | Path) -> Path:
    return Path(instance) / STATE_NAME


def load_state(instance: str | Path) -> set[str]:
    try:
        raw = json.loads(state_path(instance).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    versions = raw.get("versions") if isinstance(raw, dict) else None
    if not isinstance(versions, list):
        return set()
    return {str(v) for v in versions}


def _save_state(instance: Path, versions: set[str]) -> None:
    data = {"versions": sorted(versions)}
    state_path(instance).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def is_enabled(instance: str | Path, version: str) -> bool:
    return str(version) in load_state(instance)


def game_dir(instance: str | Path, version: str) -> Path:
    """Return the game directory used for launching *version*."""
    instance = Path(instance)
    if is_enabled(instance, version):
        return instance / "versions" / version
    return instance


def _copy_shared(instance: Path, target: Path) -> None:
    for name in ISOLATED_DIRS:
        source = instance / name
        destination = target / name
        destination.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)


def _copy_back(instance: Path, target: Path) -> None:
    for name in ISOLATED_DIRS:
        source = target / name
        if not source.is_dir():
            continue
        destination = instance / name
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)


def set_isolation(
    instance: str | Path,
    version: str,
    enabled: bool,
    migrate: bool = True,
) -> Path:
    """Enable/disable isolation and merge resources when requested."""
    instance = Path(instance)
    version = str(version)
    target = instance / "versions" / version
    if not target.is_dir():
        raise FileNotFoundError(f"版本目录不存在：{target}")
    versions = load_state(instance)
    if enabled:
        for name in ISOLATED_DIRS:
            (target / name).mkdir(parents=True, exist_ok=True)
        if migrate:
            _copy_shared(instance, target)
        versions.add(version)
    else:
        if migrate:
            _copy_back(instance, target)
        versions.discard(version)
        # Keep the version JSON/JAR, remove only the isolated resource folders.
        for name in ISOLATED_DIRS:
            shutil.rmtree(target / name, ignore_errors=True)
    _save_state(instance, versions)
    return target


def isolated_versions(instance: str | Path) -> list[str]:
    return sorted(load_state(instance))
