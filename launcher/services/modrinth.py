"""Modrinth integration: update detection by SHA1 and version downloads.

Hashing local JARs is more reliable than guessing from file names, and it also
identifies mods that were renamed by the user.  The update endpoint filters by
game version and loader, so a 1.20.1 Fabric pack never gets a 1.21 NeoForge
build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..constants import MODRINTH_API
from . import net
from .util import human_size

ProgressCallback = Callable[[int, int], None]


@dataclass
class ModUpdate:
    jar: Path
    project_id: str
    project_title: str
    current_version: str
    latest_version: str
    download_url: str
    filename: str
    sha1: str = ""
    size: int = 0
    game_versions: list[str] = field(default_factory=list)
    loaders: list[str] = field(default_factory=list)

    @property
    def project_url(self) -> str:
        return f"https://modrinth.com/mod/{self.project_id}"


def _primary_file(version: dict) -> dict:
    files = version.get("files") or []
    for item in files:
        if isinstance(item, dict) and item.get("primary"):
            return item
    return files[0] if files else {}


def lookup_files(hashes: list[str]) -> dict[str, dict]:
    """Map SHA1 → Modrinth version object (unknown hashes are absent)."""
    result: dict[str, dict] = {}
    for start in range(0, len(hashes), 100):
        chunk = hashes[start : start + 100]
        payload = net.post_json(
            f"{MODRINTH_API}/version_files", {"hashes": chunk, "algorithm": "sha1"}
        )
        if isinstance(payload, dict):
            result.update(payload)
    return result


def fetch_projects(project_ids: list[str]) -> dict[str, dict]:
    unique = [pid for pid in dict.fromkeys(project_ids) if pid]
    if not unique:
        return {}
    import json as _json

    query = net.query({"ids": _json.dumps(unique)})
    data = net.get_json(f"{MODRINTH_API}/projects?{query}")
    projects: dict[str, dict] = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                projects[str(item["id"])] = item
    return projects


def check_updates(
    jars: list[Path],
    game_version: str = "",
    loader: str = "",
    progress: Callable[[str], None] | None = None,
) -> list[ModUpdate]:
    """Return one entry per mod that has a newer compatible Modrinth version."""
    jars = [Path(jar) for jar in jars if Path(jar).is_file()]
    if not jars:
        return []
    if progress:
        progress("正在计算 Mod 指纹…")
    hashes: dict[str, Path] = {}
    for jar in jars:
        try:
            hashes[net.file_sha1(jar)] = jar
        except OSError:
            continue
    if not hashes:
        return []

    if progress:
        progress("正在查询 Modrinth…")
    current = lookup_files(list(hashes))

    filters: dict[str, object] = {"hashes": list(hashes), "algorithm": "sha1"}
    if loader:
        filters["loaders"] = [loader]
    if game_version:
        filters["game_versions"] = [game_version]
    latest = net.post_json(f"{MODRINTH_API}/version_files/update", filters)
    if not isinstance(latest, dict):
        return []

    project_ids = {
        str(item.get("project_id"))
        for item in latest.values()
        if isinstance(item, dict) and item.get("project_id")
    }
    projects = fetch_projects(list(project_ids)) if project_ids else {}

    updates: list[ModUpdate] = []
    for sha, version in latest.items():
        if not isinstance(version, dict):
            continue
        current_version = current.get(sha)
        if isinstance(current_version, dict) and current_version.get("id") == version.get("id"):
            continue
        jar = hashes.get(sha)
        if jar is None:
            continue
        file_info = _primary_file(version)
        if not file_info.get("url"):
            continue
        project_id = str(version.get("project_id") or "")
        title = str((projects.get(project_id) or {}).get("title") or project_id or jar.stem)
        updates.append(
            ModUpdate(
                jar=jar,
                project_id=project_id,
                project_title=title,
                current_version=str(
                    (current_version or {}).get("version_number")
                    or (current_version or {}).get("name")
                    or "未知版本"
                ),
                latest_version=str(version.get("version_number") or version.get("name") or ""),
                download_url=str(file_info.get("url")),
                filename=str(file_info.get("filename") or f"{jar.stem}.jar"),
                sha1=str((file_info.get("hashes") or {}).get("sha1") or ""),
                size=int(file_info.get("size") or 0),
                game_versions=[str(v) for v in version.get("game_versions") or []],
                loaders=[str(v) for v in version.get("loaders") or []],
            )
        )
    updates.sort(key=lambda item: item.project_title.lower())
    return updates


def download_update(
    update: ModUpdate,
    mods_dir: str | Path,
    progress: ProgressCallback | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """Download the new JAR and park the old one as ``*.old`` (never loaded)."""
    folder = Path(mods_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / update.filename
    counter = 1
    while target.exists() and target.resolve() != update.jar.resolve():
        target = folder / f"{Path(update.filename).stem}_{counter}{Path(update.filename).suffix}"
        counter += 1
    net.download(
        update.download_url,
        target,
        expected_sha1=update.sha1 or None,
        expected_size=update.size or None,
        progress=progress,
        cancel=cancel,
    )
    if update.jar.is_file() and update.jar.resolve() != target.resolve():
        backup = update.jar.with_name(update.jar.name + ".old")
        try:
            if backup.exists():
                backup.unlink()
            update.jar.rename(backup)
        except OSError:
            pass
    return target


def version_for_project(
    slug_or_id: str,
    game_version: str,
    loader: str,
    allow_prerelease: bool = False,
) -> dict | None:
    """Latest compatible version object for a Modrinth project slug/id."""
    import json as _json

    query = net.query(
        {
            "loaders": _json.dumps([loader]) if loader else None,
            "game_versions": _json.dumps([game_version]) if game_version else None,
        }
    )
    data = net.get_json(f"{MODRINTH_API}/project/{slug_or_id}/version?{query}")
    if not isinstance(data, list):
        return None
    for version in data:
        if not isinstance(version, dict):
            continue
        if not allow_prerelease and version.get("version_type") not in (None, "release"):
            continue
        if _primary_file(version).get("url"):
            return version
    for version in data:
        if isinstance(version, dict) and _primary_file(version).get("url"):
            return version
    return None


def install_project(
    slug_or_id: str,
    game_version: str,
    loader: str,
    mods_dir: str | Path,
    progress: ProgressCallback | None = None,
) -> Path:
    """Download the newest compatible build of a project into ``mods/``."""
    version = version_for_project(slug_or_id, game_version, loader)
    if version is None:
        raise net.NetworkError(
            f"Modrinth 上没有找到 {slug_or_id} 支持 Minecraft {game_version} / {loader} 的版本"
        )
    file_info = _primary_file(version)
    target = Path(mods_dir) / str(file_info.get("filename") or f"{slug_or_id}.jar")
    net.download(
        str(file_info["url"]),
        target,
        expected_sha1=str((file_info.get("hashes") or {}).get("sha1") or "") or None,
        expected_size=int(file_info.get("size") or 0) or None,
        progress=progress,
    )
    return target


def describe(update: ModUpdate) -> str:
    size = f"（{human_size(update.size)}）" if update.size else ""
    return (
        f"{update.project_title}: {update.current_version} → {update.latest_version}{size}"
    )
