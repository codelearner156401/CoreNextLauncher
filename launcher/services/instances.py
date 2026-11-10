"""Minecraft instances stored under ``CoreNextLauncher/instances`` next to the program.

An instance is a self-contained game directory (``versions/``, ``mods/``,
``saves/`` ...).  Cloning copies everything except logs, crash reports, old
backups and caches, so a clone starts clean but keeps its mods and settings.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..paths import instances_dir

ProgressCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]

INVALID_CHARS = '<>:"/\\|?*'
RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
SKIP_ALWAYS = {"logs", "crash-reports", "backups", ".mixin.out", "cache"}
SKIP_SAVES = {"saves"}


class InstanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstanceInfo:
    name: str
    path: Path
    versions: int = 0
    modified: float = 0.0

    @property
    def display_versions(self) -> str:
        return f"{self.versions} 个版本" if self.versions else "未安装版本"


def root_dir() -> Path:
    return instances_dir()


def sanitize_name(name: str) -> str:
    text = str(name or "").strip()
    for char in INVALID_CHARS:
        text = text.replace(char, "-")
    text = text.strip(" .")
    text = " ".join(text.split())
    if not text:
        raise InstanceError("实例名称不能为空")
    if text.lower() in RESERVED_NAMES:
        raise InstanceError(f"“{text}”是 Windows 保留名称，请换一个")
    if len(text) > 48:
        raise InstanceError("实例名称不能超过 48 个字符")
    return text


def instance_path(name: str) -> Path:
    root = root_dir()
    path = (root / sanitize_name(name)).resolve()
    if root.resolve() not in path.parents:
        raise InstanceError("实例路径不合法")
    return path


def _count_versions(path: Path) -> int:
    folder = path / "versions"
    if not folder.is_dir():
        return 0
    count = 0
    try:
        for child in folder.iterdir():
            if child.is_dir() and (child / f"{child.name}.json").is_file():
                count += 1
    except OSError:
        return 0
    return count


def list_instances() -> list[InstanceInfo]:
    root = root_dir()
    if not root.is_dir():
        return []
    result: list[InstanceInfo] = []
    try:
        for child in root.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            try:
                modified = child.stat().st_mtime
            except OSError:
                modified = 0.0
            result.append(
                InstanceInfo(
                    name=child.name,
                    path=child,
                    versions=_count_versions(child),
                    modified=modified,
                )
            )
    except OSError:
        return []
    return sorted(result, key=lambda item: item.name.lower())


def is_managed(path: str | Path) -> bool:
    try:
        candidate = Path(path).resolve()
        return root_dir().resolve() in candidate.parents
    except (OSError, RuntimeError):
        return False


def create_instance(name: str) -> Path:
    path = instance_path(name)
    if path.exists():
        raise InstanceError(f"实例“{path.name}”已存在")
    try:
        (path / "versions").mkdir(parents=True, exist_ok=False)
        (path / "mods").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InstanceError(f"无法创建实例：{exc}") from exc
    return path


def _clone_files(
    source: Path,
    target: Path,
    include_saves: bool,
    progress: ProgressCallback | None,
    cancel: CancelCallback | None,
) -> int:
    skip_top = set(SKIP_ALWAYS)
    if not include_saves:
        skip_top |= SKIP_SAVES

    files: list[Path] = []
    for current, dirs, names in os.walk(source):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        if relative == Path("."):
            dirs[:] = [name for name in dirs if name not in skip_top]
        for name in names:
            files.append(current_path / name)
    total = len(files)
    done = 0
    for path in files:
        if cancel is not None and cancel():
            raise InterruptedError
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, destination)
        except OSError as exc:
            raise InstanceError(f"复制 {relative} 失败：{exc}") from exc
        done += 1
        if progress is not None and (done % 25 == 0 or done == total):
            progress(f"正在复制 {relative.name}", done, total)
    return total


def clone_instance(
    source: str | Path,
    new_name: str,
    include_saves: bool = True,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
) -> Path:
    source_path = Path(source)
    if not source_path.is_dir():
        raise InstanceError("源实例不存在")
    if not is_managed(source_path):
        # Allow cloning a custom game directory into the workspace, but never
        # a random system folder.
        if not (source_path / "versions").is_dir():
            raise InstanceError("源目录不是 Minecraft 实例（缺少 versions 目录）")
    target = instance_path(new_name)
    if target.exists():
        raise InstanceError(f"实例“{target.name}”已存在")
    if progress is not None:
        progress("正在准备克隆…", 0, 0)
    try:
        target.mkdir(parents=True, exist_ok=False)
        _clone_files(source_path, target, include_saves, progress, cancel)
    except InterruptedError:
        shutil.rmtree(target, ignore_errors=True)
        raise InstanceError("克隆已取消") from None
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def delete_instance(name: str) -> None:
    path = instance_path(name)
    if not path.is_dir():
        raise InstanceError("实例不存在")
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise InstanceError(f"删除实例失败：{exc}") from exc
