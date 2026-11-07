"""Silent, incremental backups of ``saves/`` before every launch.

A world is only re-zipped when something inside it is newer than its newest
backup, so a normal launch costs a few directory scans instead of minutes of
compression.  Old backups are pruned per world to keep disk usage bounded.
"""

from __future__ import annotations

import os
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .util import human_size, timestamp_slug

ProgressCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]


@dataclass
class BackupResult:
    created: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    total_bytes: int = 0
    elapsed: float = 0.0

    @property
    def changed(self) -> bool:
        return bool(self.created or self.removed)


def saves_dir(game_dir: str | Path) -> Path:
    return Path(game_dir) / "saves"


def backups_dir(game_dir: str | Path) -> Path:
    return Path(game_dir) / "backups" / "saves"


def list_worlds(game_dir: str | Path) -> list[Path]:
    folder = saves_dir(game_dir)
    if not folder.is_dir():
        return []
    try:
        return sorted(
            [p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")],
            key=lambda p: p.name.lower(),
        )
    except OSError:
        return []


def _world_newest_mtime(world: Path) -> float:
    newest = 0.0
    try:
        for root, _dirs, files in os.walk(world):
            for name in files:
                if name == "session.lock":
                    continue
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, name)))
                except OSError:
                    continue
    except OSError:
        return 0.0
    return newest


def _world_backups(world_name: str, folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    prefix = f"{world_name}_"
    result: list[Path] = []
    try:
        for path in folder.iterdir():
            if path.is_file() and path.name.startswith(prefix) and path.suffix.lower() == ".zip":
                result.append(path)
    except OSError:
        return []
    return sorted(result, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def latest_backup(world_name: str, game_dir: str | Path) -> Path | None:
    backups = _world_backups(world_name, backups_dir(game_dir))
    return backups[0] if backups else None


def _zip_world(world: Path, target: Path, cancel: CancelCallback | None) -> int:
    total = 0
    temp = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for root, _dirs, files in os.walk(world):
                for name in files:
                    if name == "session.lock":
                        continue
                    path = Path(root) / name
                    if cancel is not None and cancel():
                        raise InterruptedError
                    try:
                        archive.write(path, Path(world.name) / path.relative_to(world))
                        total += path.stat().st_size
                    except OSError:
                        continue
        os.replace(temp, target)
        return total
    except Exception:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def backup_worlds(
    game_dir: str | Path,
    world_names: list[str] | None = None,
    keep: int = 5,
    only_changed: bool = False,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
) -> BackupResult:
    """Back up the given worlds (or every world when *world_names* is None)."""
    started = time.monotonic()
    result = BackupResult()
    game = Path(game_dir)
    all_worlds = list_worlds(game)
    if world_names is not None:
        wanted = set(world_names)
        worlds = [world for world in all_worlds if world.name in wanted]
    else:
        worlds = all_worlds
    if not worlds:
        result.elapsed = time.monotonic() - started
        return result

    folder = backups_dir(game)
    folder.mkdir(parents=True, exist_ok=True)
    total_worlds = len(worlds)
    for index, world in enumerate(worlds):
        if cancel is not None and cancel():
            break
        if progress is not None:
            progress(f"正在检查存档：{world.name}", index, total_worlds)
        newest = _world_newest_mtime(world)
        existing = _world_backups(world.name, folder)
        latest_mtime = 0.0
        for path in existing:
            try:
                latest_mtime = max(latest_mtime, path.stat().st_mtime)
            except OSError:
                continue
        if only_changed and existing and latest_mtime >= newest:
            result.skipped.append(world.name)
            continue

        target = folder / f"{world.name}_{timestamp_slug()}.zip"
        counter = 1
        while target.exists():
            target = folder / f"{world.name}_{timestamp_slug()}_{counter}.zip"
            counter += 1
        try:
            size = _zip_world(world, target, cancel)
        except InterruptedError:
            break
        except OSError as exc:
            result.failed.append(f"{world.name}：{exc}")
            continue
        result.created.append(target)
        result.total_bytes += size
        if progress is not None:
            progress(f"已备份 {world.name}（{human_size(size)}）", index + 1, total_worlds)

        backups = _world_backups(world.name, folder)
        for old in backups[max(1, int(keep)) :]:
            try:
                old.unlink()
                result.removed.append(old)
            except OSError:
                continue

    result.elapsed = time.monotonic() - started
    return result


def backup_saves(
    game_dir: str | Path,
    keep: int = 5,
    only_changed: bool = True,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
) -> BackupResult:
    """Back up every world in ``saves/`` into ``backups/saves/``."""
    return backup_worlds(
        game_dir,
        None,
        keep=keep,
        only_changed=only_changed,
        progress=progress,
        cancel=cancel,
    )


def describe(result: BackupResult) -> str:
    if not result.created and not result.failed:
        return "存档没有变化，无需备份"
    parts: list[str] = []
    if result.created:
        parts.append(
            f"已备份 {len(result.created)} 个存档（{human_size(result.total_bytes)}）"
        )
    if result.failed:
        parts.append(f"{len(result.failed)} 个失败")
    if result.removed:
        parts.append(f"清理了 {len(result.removed)} 个旧备份")
    return "，".join(parts)
