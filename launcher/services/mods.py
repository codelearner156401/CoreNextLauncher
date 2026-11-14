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
    author: str = ""
    loader: str = ""
    description: str = ""
    depends: dict[str, str] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
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
    version_mismatch: list[tuple[str, str, str]] = field(default_factory=list)
    conflicts: list[tuple[str, str, str]] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        # version_mismatch / conflicts stay out of this flag on purpose: the
        # launch pre-check renders only the four sections above, so counting
        # them here would open a blocking dialog that lists no reason.
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


def _authors_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        names = []
        for item in value:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                names.append(str(item.get("name") or item.get("author") or ""))
        return ", ".join(name for name in names if name)
    if isinstance(value, dict):
        return str(value.get("name") or value.get("author") or "")
    return ""


def _read_fabric_meta(archive: zipfile.ZipFile, names: set[str]) -> dict | None:
    if "fabric.mod.json" not in names:
        return None
    raw = json.loads(archive.read("fabric.mod.json").decode("utf-8", "replace"))
    depends = raw.get("depends") or {}
    if not isinstance(depends, dict):
        depends = {}
    breaks = raw.get("breaks") or raw.get("conflicts") or {}
    if not isinstance(breaks, dict):
        breaks = {}
    return {
        "loader": "fabric",
        "mod_id": str(raw.get("id") or ""),
        "name": str(raw.get("name") or ""),
        "version": str(raw.get("version") or ""),
        "author": _authors_text(raw.get("authors") or raw.get("author")),
        "description": str(raw.get("description") or ""),
        "depends": {str(k): str(v) for k, v in depends.items()},
        "conflicts": [str(key) for key in breaks],
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
    breaks = loader_block.get("breaks") or []
    conflicts = [
        str(entry["id"])
        for entry in (breaks if isinstance(breaks, list) else [])
        if isinstance(entry, dict) and entry.get("id")
    ]
    return {
        "loader": "quilt",
        "mod_id": str(loader_block.get("id") or ""),
        "name": str(metadata.get("name") or ""),
        "version": str(loader_block.get("version") or ""),
        "author": _authors_text(metadata.get("authors") or metadata.get("author")),
        "description": str(metadata.get("description") or ""),
        "depends": deps,
        "conflicts": conflicts,
    }


def _dependency_kind(entry: dict) -> str:
    """Kind of one Forge/NeoForge dependency table entry.

    NeoForge 1.x replaced the boolean ``mandatory`` with ``type`` taking
    ``required`` / ``optional`` / ``incompatible`` (mod-file spec, section
    "Dependency"); Forge still writes ``mandatory``.
    """
    kind = entry.get("type")
    if isinstance(kind, str) and kind.strip():
        return kind.strip().lower()
    return "required" if entry.get("mandatory", True) else "optional"


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
            continue
        declared = [entry for entry in mods if isinstance(entry, dict)]
        first = declared[0] if declared else {}
        depends: dict[str, str] = {}
        conflicts: list[str] = []
        deps_root = data.get("dependencies") or {}
        if isinstance(deps_root, dict):
            # Every [[mods]] entry brings its own [dependencies.<modId>] table.
            owners = [str(entry.get("modId") or "") for entry in declared]
            owners = [owner for owner in owners if owner]
            for owner in deps_root:
                if str(owner) not in owners:
                    owners.append(str(owner))
            for owner in owners:
                entries = deps_root.get(owner) or []
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict) or not entry.get("modId"):
                        continue
                    dep_id = str(entry["modId"])
                    kind = _dependency_kind(entry)
                    if kind == "incompatible":
                        conflicts.append(dep_id)
                    elif kind == "required":
                        depends[dep_id] = str(entry.get("versionRange") or "")
        return {
            "loader": "neoforge" if "neoforge.mods.toml" in candidate else "forge",
            "mod_id": str(first.get("modId") or ""),
            "name": str(first.get("displayName") or ""),
            "version": str(first.get("version") or ""),
            "author": _authors_text(first.get("authors") or first.get("author")),
            "description": str(first.get("description") or ""),
            "depends": depends,
            "conflicts": conflicts,
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
        "author": "",
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
                author=str(meta.get("author") or ""),
                loader=str(meta.get("loader") or ""),
                description=str(meta.get("description") or ""),
                depends=dict(meta.get("depends") or {}),
                conflicts=list(meta.get("conflicts") or []),
                error=str(meta.get("error") or ""),
            )
        )
    return mods, folder


def _version_tuple(text: str) -> tuple[int, ...] | None:
    """Parse "1.20.1" / "0.83.0+mc1.20" into numbers; None when unusable."""
    numbers: list[int] = []
    for part in str(text).strip().split("."):
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            return None
        numbers.append(int(digits))
        if len(digits) != len(part):
            break  # a prerelease/build suffix ends the comparable part
    return tuple(numbers) if numbers else None


def _at_least(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    size = max(len(left), len(right))
    return left + (0,) * (size - len(left)) >= right + (0,) * (size - len(right))


def _atom_satisfied(atom: str, version: tuple[int, ...]) -> bool | None:
    """Check one atom of a Fabric/Quilt range; None means "cannot decide"."""
    if atom in {"*", "x", "X", "any"}:
        return True
    if atom.startswith(">="):
        floor = _version_tuple(atom[2:])
        return None if floor is None else _at_least(version, floor)
    if atom.startswith("<="):
        ceiling = _version_tuple(atom[2:])
        return None if ceiling is None else _at_least(ceiling, version)
    if atom.startswith(">"):
        floor = _version_tuple(atom[1:])
        return None if floor is None else not _at_least(floor, version)
    if atom.startswith("<"):
        ceiling = _version_tuple(atom[1:])
        return None if ceiling is None else not _at_least(version, ceiling)
    if atom[:1] in {"~", "^", "!"}:
        return None  # operators outside this check: stay silent instead of guessing
    if atom[-1:] in {"*", "x", "X"}:
        prefix = _version_tuple(atom[:-1].rstrip("."))
        if prefix is None:
            return None
        return version[: len(prefix)] == prefix
    exact = _version_tuple(atom)
    if exact is None:
        return None
    return _at_least(version, exact) and _at_least(exact, version)


def _range_satisfied(requirement: str, version_text: str) -> bool | None:
    """Check a Fabric/Quilt dependency range; None when undecidable."""
    version = _version_tuple(version_text)
    if version is None:
        return None
    undecided = False
    for alternative in str(requirement).split("||"):
        atoms = alternative.replace(",", " ").split()
        if not atoms:
            undecided = True
            continue
        for atom in atoms:
            outcome = _atom_satisfied(atom, version)
            if outcome is None:
                undecided = True
            if not outcome:
                break
        else:
            return True
    return None if undecided else False


def scan_conflicts(mods: list[ModFile], include_platform: bool = True) -> ModConflictReport:
    report = ModConflictReport()
    by_id: dict[str, list[str]] = {}
    enabled_ids: set[str] = set()
    enabled_by_id: dict[str, list[str]] = {}
    enabled_versions: dict[str, str] = {}
    by_filename: dict[str, list[str]] = {}
    for mod in mods:
        if mod.enabled:
            # A kept .disabled backup is not a second copy of the mod, so both
            # duplicate checks below only look at enabled jars.
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
            enabled_by_id.setdefault(mod.mod_id, []).append(mod.filename)
            enabled_versions.setdefault(mod.mod_id, mod.version)
    report.duplicates = {
        mod_id: names
        for mod_id, names in enabled_by_id.items()
        if len(names) > 1 and mod_id not in {"minecraft", "java"}
    }
    report.duplicate_files = {
        name: files for name, files in by_filename.items() if len(files) > 1
    }
    for mod in mods:
        if mod.error or not mod.enabled:
            continue
        for dep_id, dep_range in mod.depends.items():
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
            elif mod.loader in {"fabric", "quilt"}:
                # Forge/NeoForge use Maven ranges; only Fabric/Quilt ranges are
                # compared here, and an undecidable range stays silent.
                actual = enabled_versions.get(dep_id, "")
                if _range_satisfied(dep_range, actual) is False:
                    report.version_mismatch.append(
                        (
                            mod.mod_id or mod.display_name,
                            f"{dep_id} {dep_range}".strip(),
                            mod.filename,
                        )
                    )
        for conflict_id in mod.conflicts:
            if conflict_id in enabled_ids:
                report.conflicts.append(
                    (mod.mod_id or mod.display_name, conflict_id, mod.filename)
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
