"""Visual Mod management: list, inspect metadata and toggle ``.disabled``.

Fabric/Quilt/Forge/NeoForge all ignore files that do not end in ``.jar``, so
the launcher can enable and disable a mod by renaming ``foo.jar`` to
``foo.jar.disabled`` without ever touching the archive itself.
"""

from __future__ import annotations

import json
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

PLATFORM_MOD_IDS = {
    "minecraft",
    "java",
    "fabricloader",
    "fabric",
    "forge",
    "neoforge",
    "quilt_loader",
    "quilted_fabric_api",
}

DISABLED_SUFFIX = ".disabled"


@dataclass
class ModFile:
    path: Path
    enabled: bool = True
    size: int = 0
    mtime: float = 0.0
    mod_id: str = ""
    name: str = ""
    version: str = ""
    loader: str = ""
    description: str = ""
    depends: dict[str, str] = field(default_factory=dict)
    error: str = ""

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def display_name(self) -> str:
        return self.name or self.mod_id or self.path.stem

    @property
    def display_version(self) -> str:
        return self.version or "—"


@dataclass
class ModConflictReport:
    duplicates: dict[str, list[str]] = field(default_factory=dict)
    duplicate_files: dict[str, list[str]] = field(default_factory=dict)
    missing: list[tuple[str, str, str]] = field(default_factory=list)
    disabled_deps: list[tuple[str, str, str]] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not (
            self.duplicates
            or self.duplicate_files
            or self.missing
            or self.disabled_deps
        )


_META_CACHE: dict[tuple[str, int, int], dict] = {}


def mods_path(game_dir: str | Path) -> Path:
    return Path(game_dir) / "mods"


def list_mod_files(game_dir: str | Path) -> list[Path]:
    """Every JAR (enabled or disabled) inside ``mods/``."""
    folder = mods_path(game_dir)
    if not folder.is_dir():
        return []
    files: list[Path] = []
    try:
        for path in folder.iterdir():
            if not path.is_file():
                continue
            name = path.name.lower()
            if name.endswith(".jar") or name.endswith(".jar" + DISABLED_SUFFIX):
                files.append(path)
    except OSError:
        return []
    return sorted(files, key=lambda item: item.name.lower())


def _read_fabric_meta(archive: zipfile.ZipFile, names: set[str]) -> dict | None:
    if "fabric.mod.json" not in names:
        return None
    raw = json.loads(archive.read("fabric.mod.json").decode("utf-8", "replace"))
    depends = raw.get("depends") or {}
    if not isinstance(depends, dict):
        depends = {}
    return {
        "loader": "fabric",
        "mod_id": str(raw.get("id") or ""),
        "name": str(raw.get("name") or ""),
        "version": str(raw.get("version") or ""),
        "description": str(raw.get("description") or ""),
        "depends": {str(k): str(v) for k, v in depends.items()},
    }


def _read_quilt_meta(archive: zipfile.ZipFile, names: set[str]) -> dict | None:
    if "quilt.mod.json" not in names:
        return None
    raw = json.loads(archive.read("quilt.mod.json").decode("utf-8", "replace"))
    loader_block = raw.get("quilt_loader") or {}
    metadata = loader_block.get("metadata") or {}
    depends = loader_block.get("depends") or []
    deps: dict[str, str] = {}
    if isinstance(depends, list):
        for entry in depends:
            if isinstance(entry, dict) and entry.get("id"):
                deps[str(entry["id"])] = str(entry.get("versions") or "")
    return {
        "loader": "quilt",
        "mod_id": str(loader_block.get("id") or ""),
        "name": str(metadata.get("name") or ""),
        "version": str(loader_block.get("version") or ""),
        "description": str(metadata.get("description") or ""),
        "depends": deps,
    }


def _read_forge_meta(archive: zipfile.ZipFile, names: set[str]) -> dict | None:
    for candidate in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
        if candidate not in names:
            continue
        try:
            data = tomllib.loads(archive.read(candidate).decode("utf-8", "replace"))
        except tomllib.TOMLDecodeError:
            continue
        mods = data.get("mods") or []
        if not isinstance(mods, list) or not mods:
            return None
        first = mods[0] if isinstance(mods[0], dict) else {}
        depends: dict[str, str] = {}
        deps_root = data.get("dependencies") or {}
        if isinstance(deps_root, dict):
            for _owner, entries in deps_root.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict) or not entry.get("mandatory", True):
                        continue
                    dep_id = str(entry.get("modId") or "")
                    if dep_id:
                        depends[dep_id] = str(entry.get("versionRange") or "")
        return {
            "loader": "neoforge" if "neoforge.mods.toml" in candidate else "forge",
            "mod_id": str(first.get("modId") or ""),
            "name": str(first.get("displayName") or ""),
            "version": str(first.get("version") or ""),
            "description": str(first.get("description") or ""),
            "depends": depends,
        }
    return None


def _read_manifest_meta(archive: zipfile.ZipFile, names: set[str]) -> dict | None:
    if "META-INF/MANIFEST.MF" not in names:
        return None
    try:
        text = archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except (KeyError, OSError):
        return None
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith(" "):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    title = fields.get("implementation-title") or ""
    version = fields.get("implementation-version") or ""
    if not title and not version:
        return None
    return {
        "loader": "unknown",
        "mod_id": "",
        "name": title,
        "version": version,
        "description": "",
        "depends": {},
    }


def read_mod_meta(jar: str | Path) -> dict:
    """Read metadata from a mod JAR (cached by path + mtime + size)."""
    path = Path(jar)
    try:
        stat = path.stat()
    except OSError as exc:
        return {"error": str(exc)}
    key = (str(path), int(stat.st_mtime), stat.st_size)
    cached = _META_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            meta = (
                _read_fabric_meta(archive, names)
                or _read_quilt_meta(archive, names)
                or _read_forge_meta(archive, names)
                or _read_manifest_meta(archive, names)
                or {}
            )
    except (zipfile.BadZipFile, OSError, ValueError, KeyError) as exc:
        meta = {"error": f"无法读取（{exc}）"}
    if not meta:
        meta = {"error": "无法识别 Mod 元数据"}
    # Bound the cache so a long session cannot grow without limit.
    if len(_META_CACHE) > 2000:
        _META_CACHE.clear()
    _META_CACHE[key] = meta
    return meta


def load_mods(game_dir: str | Path) -> tuple[list[ModFile], Path]:
    folder = mods_path(game_dir)
    mods: list[ModFile] = []
    for path in list_mod_files(game_dir):
        try:
            stat = path.stat()
        except OSError:
            continue
        enabled = not path.name.lower().endswith(DISABLED_SUFFIX)
        meta = read_mod_meta(path)
        mods.append(
            ModFile(
                path=path,
                enabled=enabled,
                size=stat.st_size,
                mtime=stat.st_mtime,
                mod_id=str(meta.get("mod_id") or ""),
                name=str(meta.get("name") or ""),
                version=str(meta.get("version") or ""),
                loader=str(meta.get("loader") or ""),
                description=str(meta.get("description") or ""),
                depends=dict(meta.get("depends") or {}),
                error=str(meta.get("error") or ""),
            )
        )
    return mods, folder


def scan_conflicts(mods: list[ModFile], include_platform: bool = True) -> ModConflictReport:
    report = ModConflictReport()
    by_id: dict[str, list[str]] = {}
    enabled_ids: set[str] = set()
    by_filename: dict[str, list[str]] = {}
    for mod in mods:
        base = mod.filename
        if base.lower().endswith(DISABLED_SUFFIX):
            base = base[: -len(DISABLED_SUFFIX)]
        by_filename.setdefault(base.lower(), []).append(mod.filename)
        if mod.error or not mod.mod_id:
            if mod.error:
                report.unreadable.append(f"{mod.filename}：{mod.error}")
            continue
        by_id.setdefault(mod.mod_id, []).append(mod.filename)
        if mod.enabled:
            enabled_ids.add(mod.mod_id)
    report.duplicates = {
        mod_id: names
        for mod_id, names in by_id.items()
        if len(names) > 1 and mod_id not in {"minecraft", "java"}
    }
    report.duplicate_files = {
        name: files for name, files in by_filename.items() if len(files) > 1
    }
    for mod in mods:
        if mod.error or not mod.enabled:
            continue
        for dep_id, _range in mod.depends.items():
            if include_platform and dep_id in PLATFORM_MOD_IDS:
                continue
            if dep_id.startswith("java"):
                continue
            if dep_id not in by_id:
                report.missing.append((mod.mod_id or mod.display_name, dep_id, mod.filename))
            elif dep_id not in enabled_ids:
                report.disabled_deps.append(
                    (mod.mod_id or mod.display_name, dep_id, mod.filename)
                )
    return report


def toggle_mod(path: str | Path) -> Path:
    """Rename ``foo.jar`` ⇄ ``foo.jar.disabled`` and return the new path."""
    target = Path(path)
    if target.name.lower().endswith(DISABLED_SUFFIX):
        new_path = target.with_name(target.name[: -len(DISABLED_SUFFIX)])
    else:
        new_path = target.with_name(target.name + DISABLED_SUFFIX)
    if new_path.exists():
        raise FileExistsError(f"目标文件已存在：{new_path.name}")
    target.rename(new_path)
    return new_path


def set_mod_enabled(path: str | Path, enabled: bool) -> Path:
    target = Path(path)
    is_enabled = not target.name.lower().endswith(DISABLED_SUFFIX)
    if is_enabled == enabled:
        return target
    return toggle_mod(target)


def describe(mod: ModFile) -> str:
    bits = [mod.display_name]
    if mod.mod_id:
        bits.append(f"ID: {mod.mod_id}")
    if mod.version:
        bits.append(f"v{mod.version}")
    if mod.loader:
        bits.append(mod.loader)
    return " · ".join(bits)
