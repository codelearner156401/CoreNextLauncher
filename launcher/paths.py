"""Workspace layout and portable runtime paths.

Every installation keeps a single portable workspace next to the program::

    CoreNextLauncher.exe
    CoreNextLauncher/
        config/       launcher_config.json / account.json / secrets.json ...
        kernel/       mc_core.exe and the managed Java runtime
        instances/    one folder per Minecraft instance
        skins/        imported / official Minecraft skins
        cache/        downloads, server icons, update packages
        logs/         launcher logs
        exports/      exported modpack output

Set ``CORENEXTLAUNCHER_HOME`` to override the workspace root (used by the
self-test suite and portable installations).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

WORKSPACE_NAME = "CoreNextLauncher"
ENV_HOME = "CORENEXTLAUNCHER_HOME"


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


def _known_documents_dir() -> Path | None:
    """Read the real Documents folder (handles OneDrive redirection)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folder_id = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")
        guid = GUID.from_buffer_copy(folder_id.bytes_le)
        path_ptr = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        result = shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
        )
        if result == 0 and path_ptr.value:
            path = Path(path_ptr.value)
            ole32.CoTaskMemFree(path_ptr)
            return path
        if path_ptr:
            ole32.CoTaskMemFree(path_ptr)
    except Exception:
        return None
    return None


def documents_dir() -> Path:
    known = _known_documents_dir()
    if known is not None:
        return known
    return Path.home() / "Documents"


def program_dir() -> Path:
    """Directory that contains CoreNextLauncher.exe (or main.py in source mode)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return app_root()


def workspace_root() -> Path:
    override = os.environ.get(ENV_HOME, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return program_dir() / WORKSPACE_NAME


def config_dir() -> Path:
    return workspace_root() / "config"


def kernel_dir() -> Path:
    return workspace_root() / "kernel"


def instances_dir() -> Path:
    return workspace_root() / "instances"


def cache_dir() -> Path:
    return workspace_root() / "cache"


def logs_dir() -> Path:
    return workspace_root() / "logs"


def exports_dir() -> Path:
    return workspace_root() / "exports"


def skins_dir() -> Path:
    return workspace_root() / "skins"


def default_instance_dir() -> Path:
    return instances_dir() / "default"


def data_dir() -> Path:
    """Backwards-compatible alias for the configuration directory."""
    return config_dir()


@dataclass(frozen=True)
class Workspace:
    root: Path
    config: Path
    kernel: Path
    instances: Path
    cache: Path
    logs: Path
    exports: Path
    skins: Path


def _legacy_state_dirs() -> list[Path]:
    """Old per-user locations that may still hold settings from 1.6.x/1.7.x."""
    candidates: list[Path] = []
    roaming = os.environ.get("APPDATA")
    if roaming:
        candidates.append(Path(roaming) / WORKSPACE_NAME)
    candidates.append(Path.home() / f".{WORKSPACE_NAME.lower()}")
    old_documents = documents_dir() / WORKSPACE_NAME
    if old_documents != workspace_root():
        candidates.append(old_documents)
    return candidates


def _migrate_legacy_config(workspace: Workspace) -> None:
    """Copy settings from the old APPDATA location the first time only."""
    if os.environ.get(ENV_HOME):
        # A custom workspace (portable install / tests) stays self-contained.
        return
    target = workspace.config / "launcher_config.json"
    if target.is_file():
        return
    names = (
        "launcher_config.json",
        "account.json",
        "secrets.json",
        "servers.json",
    )
    for old in _legacy_state_dirs():
        if not (old / "launcher_config.json").is_file():
            continue
        try:
            workspace.config.mkdir(parents=True, exist_ok=True)
            for name in names:
                source = old / name
                if source.is_file() and not (workspace.config / name).exists():
                    shutil.copy2(source, workspace.config / name)
            # v1.8 stores Minecraft data inside the portable workspace.  Keep
            # the old account/theme/Java settings, but move the default game
            # directory into instances/default instead of an external C: path.
            if target.is_file():
                try:
                    raw = json.loads(target.read_text(encoding="utf-8"))
                    configured = str(raw.get("game_dir") or "").strip()
                    if configured:
                        old_game = Path(configured).expanduser().resolve()
                        if not old_game.is_relative_to(workspace.root.resolve()):
                            raw["game_dir"] = str(workspace.instances / "default")
                            target.write_text(
                                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8",
                            )
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
        except OSError:
            pass
        return


def ensure_workspace() -> Workspace:
    """Create the portable workspace next to the program on first run."""
    root = workspace_root()
    workspace = Workspace(
        root=root,
        config=root / "config",
        kernel=root / "kernel",
        instances=root / "instances",
        cache=root / "cache",
        logs=root / "logs",
        exports=root / "exports",
        skins=root / "skins",
    )
    for folder in (
        workspace.config,
        workspace.kernel,
        workspace.instances,
        workspace.cache,
        workspace.logs,
        workspace.exports,
        workspace.skins,
    ):
        folder.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_config(workspace)
    # A fresh workspace starts with one empty default instance.
    try:
        if not any(path.is_dir() for path in workspace.instances.iterdir()):
            (workspace.instances / "default").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return workspace


def deploy_kernel() -> Path:
    """Copy the bundled C++ core into ``kernel/`` and return its path."""
    target = kernel_dir() / "mc_core.exe"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return target
    source = app_root() / "build" / "mc_core.exe"
    if not source.is_file():
        return target
    try:
        source_stat = source.stat()
        if target.is_file():
            target_stat = target.stat()
            if (
                target_stat.st_size == source_stat.st_size
                and target_stat.st_mtime >= source_stat.st_mtime
            ):
                return target
        shutil.copy2(source, target)
    except OSError:
        pass
    return target


def core_path() -> Path:
    """Path of the workspace kernel, deploying it when necessary."""
    path = deploy_kernel()
    if path.is_file():
        return path
    # Source-mode fallback before the first deployment succeeds.
    fallback = app_root() / "build" / "mc_core.exe"
    return fallback if fallback.is_file() else path
