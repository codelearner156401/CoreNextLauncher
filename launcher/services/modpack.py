"""Import and export Modrinth ``.mrpack`` modpacks.

An ``.mrpack`` is a ZIP with ``modrinth.index.json`` (a list of files with
hashes and CDN URLs) plus ``overrides/`` and ``client-overrides/`` folders.
This module parses the manifest, downloads the files that a client needs and
writes the overrides into the selected game directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from . import net

StageCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]

LOADER_KEYS = {
    "fabric-loader": ("fabric", "Fabric"),
    "quilt-loader": ("quilt", "Quilt"),
    "forge": ("forge", "Forge"),
    "neoforge": ("neoforge", "NeoForge"),
}


class ModpackError(RuntimeError):
    pass


@dataclass
class ModpackFile:
    path: str
    url: str
    sha1: str = ""
    sha512: str = ""
    size: int = 0

    @property
    def is_mod(self) -> bool:
        return self.path.lower().startswith("mods/") and self.path.lower().endswith(".jar")


@dataclass
class ModpackInfo:
    archive: Path
    name: str
    version: str
    summary: str
    mc_version: str
    loader: str
    loader_version: str
    files: list[ModpackFile] = field(default_factory=list)
    override_count: int = 0
    total_size: int = 0

    @property
    def loader_label(self) -> str:
        for key, (value, label) in LOADER_KEYS.items():
            if value == self.loader:
                return label
        return self.loader or "原版"

    @property
    def mod_count(self) -> int:
        return sum(1 for item in self.files if item.is_mod)


@dataclass
class ImportResult:
    pack: ModpackInfo
    downloaded: int = 0
    skipped_files: int = 0
    overrides: int = 0
    failed: list[str] = field(default_factory=list)
    loader_profile: str = ""
    notes: list[str] = field(default_factory=list)


# Windows refuses these names (and strips trailing dots/spaces first), so a
# pack that ships "nul.jar" or "config/con.txt" would hit a device node.
RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _safe_member(name: str) -> str | None:
    """Normalise an archive path and reject anything escaping the target.

    Rejects absolute paths, parent-relative segments, drive letters ("C:"),
    UNC prefixes and Windows reserved device names, so a hostile archive can
    never write outside the game directory.
    """
    text = str(name or "").replace("\\", "/").strip()
    if not text or "\x00" in text:
        return None
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        return None
    relative = PurePosixPath(text)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    parts: list[str] = []
    for part in relative.parts:
        if part in ("", "."):
            continue
        # Windows strips trailing dots/spaces: "aux." still opens the device.
        if part.endswith((".", " ")):
            return None
        if part.split(".", 1)[0].upper() in RESERVED_NAMES:
            return None
        parts.append(part)
    return "/".join(parts) if parts else None


def read_pack(path: str | Path) -> ModpackInfo:
    archive_path = Path(path)
    if not archive_path.is_file():
        raise ModpackError(f"文件不存在：{archive_path}")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if "modrinth.index.json" not in archive.namelist():
                raise ModpackError("这个压缩包不是 .mrpack：缺少 modrinth.index.json")
            raw = json.loads(archive.read("modrinth.index.json").decode("utf-8", "replace"))
            overrides = 0
            for name in archive.namelist():
                if name.startswith(("overrides/", "client-overrides/")) and not name.endswith("/"):
                    overrides += 1
    except zipfile.BadZipFile as exc:
        raise ModpackError("整合包文件已损坏，无法读取") from exc

    if not isinstance(raw, dict):
        raise ModpackError("整合包清单格式异常")
    format_version = raw.get("formatVersion")
    if isinstance(format_version, bool) or format_version != 1:
        raise ModpackError(
            f"不支持的整合包格式版本：formatVersion={format_version!r}（只支持 1）"
        )
    game = str(raw.get("game") or "").strip()
    if game.lower() != "minecraft":
        raise ModpackError(f"这个整合包是给 {game or '(未声明)'} 的，不是 Minecraft，无法导入")
    version_id = str(raw.get("versionId") or "").strip()
    if not version_id:
        raise ModpackError("整合包清单缺少 versionId（整合包版本号），无法导入")
    dependencies = raw.get("dependencies") or {}
    if not isinstance(dependencies, dict):
        dependencies = {}
    mc_version = str(dependencies.get("minecraft") or "").strip()
    loader = ""
    loader_version = ""
    for key, (value, _label) in LOADER_KEYS.items():
        if key in dependencies:
            loader = value
            loader_version = str(dependencies.get(key) or "").strip()
            break

    entries = raw.get("files")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise ModpackError("整合包清单的 files 字段格式异常（应该是数组）")
    files: list[ModpackFile] = []
    total = 0
    for item in entries:
        if not isinstance(item, dict):
            continue
        env = item.get("env") or {}
        if isinstance(env, dict) and str(env.get("client") or "").lower() == "unsupported":
            continue
        safe = _safe_member(str(item.get("path") or ""))
        if not safe:
            continue
        urls = item.get("downloads") or []
        if not isinstance(urls, list) or not urls:
            continue
        hashes = item.get("hashes") or {}
        files.append(
            ModpackFile(
                path=safe,
                url=str(urls[0]),
                sha1=str(hashes.get("sha1") or ""),
                sha512=str(hashes.get("sha512") or ""),
                size=int(item.get("fileSize") or 0),
            )
        )
        total += int(item.get("fileSize") or 0)

    if not mc_version:
        raise ModpackError("整合包没有声明 Minecraft 版本（dependencies.minecraft），无法导入")
    if not files and not overrides:
        raise ModpackError("整合包清单的 files 为空，也没有任何 overrides 文件，没有可安装的内容")
    return ModpackInfo(
        archive=archive_path,
        name=str(raw.get("name") or archive_path.stem),
        version=version_id,
        summary=str(raw.get("summary") or ""),
        mc_version=mc_version,
        loader=loader,
        loader_version=loader_version,
        files=files,
        override_count=overrides,
        total_size=total,
    )


def _existing_ok(target: Path, item: ModpackFile) -> bool:
    """Does the file already on disk match the manifest hashes?"""
    try:
        size = target.stat().st_size
        if item.size and size != item.size:
            return False
        if item.sha1:
            return net.file_sha1(target).lower() == item.sha1.lower()
        if item.sha512:
            digest = hashlib.sha512()
            with open(target, "rb") as handle:
                for block in iter(lambda: handle.read(net.CHUNK), b""):
                    digest.update(block)
            return digest.hexdigest().lower() == item.sha512.lower()
    except OSError:
        return False
    # Only a size (or nothing at all) was published: keep the local file.
    return True


def _download_file(
    item: ModpackFile,
    game: Path,
    cancel: CancelCallback | None,
) -> bool:
    target = game / item.path
    if target.is_file() and _existing_ok(target, item):
        return False
    net.download(
        item.url,
        target,
        expected_sha1=item.sha1 or None,
        expected_sha512=item.sha512 or None,
        expected_size=item.size or None,
        cancel=cancel,
    )
    return True


def _override_members(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(archive member, normalised relative path) pairs in write order.

    "overrides/" is applied first and "client-overrides/" second, so a
    client-specific file wins when both carry the same relative path - the
    ZIP namelist order cannot be trusted for that.
    """
    members: list[tuple[str, str]] = []
    for prefix in ("overrides/", "client-overrides/"):
        for name in archive.namelist():
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            safe = _safe_member(name.split("/", 1)[1])
            if safe:
                members.append((name, safe))
    return members


def extract_overrides(
    archive_path: Path,
    game: Path,
    overwrite: bool = False,
    progress: StageCallback | None = None,
    cancel: CancelCallback | None = None,
    manifest_paths: set[str] | None = None,
) -> int:
    """Write the overrides into *game*.

    *manifest_paths* are the paths the pack also lists in "files": the
    Modrinth spec gives overrides priority over those downloads, so they are
    replaced even when the user asked to keep local files.
    """
    written = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = _override_members(archive)
        total = len(members)
        for index, (name, safe) in enumerate(members):
            if cancel is not None and cancel():
                break
            target = game / safe
            from_manifest = bool(manifest_paths) and safe in manifest_paths
            if target.exists() and not overwrite and not from_manifest:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(name) as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output)
                written += 1
            except OSError:
                continue
            if progress is not None:
                progress(f"写入整合包配置：{Path(safe).name}", index, total)
    return written


def import_pack(
    path: str | Path,
    game_dir: str | Path,
    overwrite_overrides: bool = False,
    install_loader: bool = True,
    progress: StageCallback | None = None,
    cancel: CancelCallback | None = None,
) -> ImportResult:
    info = read_pack(path)
    game = Path(game_dir)
    game.mkdir(parents=True, exist_ok=True)
    result = ImportResult(pack=info)
    manifest_paths = {item.path for item in info.files}
    downloaded_total = len(info.files)
    # One continuous scale for both phases (download + overrides), otherwise
    # the progress bar jumps backwards when the extraction starts.
    total = downloaded_total + info.override_count

    def override_progress(message: str, done: int, count: int) -> None:
        if progress is None:
            return
        combined = max(total, downloaded_total + count)
        progress(message, downloaded_total + done, combined)

    for index, item in enumerate(info.files):
        if cancel is not None and cancel():
            result.notes.append("导入已取消")
            break
        if progress is not None:
            progress(f"下载 {Path(item.path).name}", index, total)
        try:
            if _download_file(item, game, cancel):
                result.downloaded += 1
            else:
                result.skipped_files += 1
        except Exception as exc:  # keep going; report at the end
            if cancel is not None and cancel():
                result.notes.append("导入已取消")
                break
            result.failed.append(f"{item.path}：{exc}")
    if result.failed:
        result.notes.append(f"有 {len(result.failed)} 个文件下载或校验失败，详情见下方列表。")

    try:
        with zipfile.ZipFile(info.archive) as archive:
            override_paths = {safe for _name, safe in _override_members(archive)}
    except (OSError, zipfile.BadZipFile):
        override_paths = set()
    conflicts = sorted(override_paths & manifest_paths)
    if conflicts:
        preview = "、".join(conflicts[:5]) + (" 等" if len(conflicts) > 5 else "")
        result.notes.append(
            f"{len(conflicts)} 个文件同时出现在 files 与 overrides 中，已按 Modrinth 规范以 overrides 为准覆盖：{preview}"
        )

    if progress is not None and (cancel is None or not cancel()):
        progress("正在解压 overrides…", downloaded_total, total)
    try:
        result.overrides = extract_overrides(
            info.archive,
            game,
            overwrite_overrides,
            override_progress,
            cancel,
            manifest_paths,
        )
    except (OSError, zipfile.BadZipFile) as exc:
        result.failed.append(f"overrides 解压失败：{exc}")

    base_ready = (game / "versions" / info.mc_version / f"{info.mc_version}.jar").is_file()
    if not base_ready:
        result.notes.append(
            f"还没有安装原版 {info.mc_version}，请先在「下载 / 安装」页安装，否则整合包无法启动。"
        )
    if info.loader == "fabric" and install_loader and base_ready:
        try:
            from . import fabric_install

            if progress is not None:
                progress("正在安装 Fabric 加载器…", total, total)
            installed = fabric_install.install_fabric(
                game,
                info.mc_version,
                info.loader_version or None,
                progress,
                cancel,
            )
            result.loader_profile = installed.profile_id
            result.notes.append(
                f"已安装 Fabric {installed.loader_version}，启动时请在版本库选择 {installed.profile_id}。"
            )
        except Exception as exc:
            result.failed.append(f"Fabric 安装失败：{exc}")
    elif info.loader in {"forge", "neoforge", "quilt"}:
        result.notes.append(
            f"这个整合包使用 {info.loader_label}，启动器暂不会自动安装该加载器，请自行安装后启动。"
        )
    return result


# --------------------------------------------------------------------- export
# Never exported, by product rule: saves are the player's own worlds, the rest
# is rebuilt on demand and only bloats the pack.
EXPORT_EXCLUDED = ("saves", "resourcepacks", "logs", "crash-reports")

LOADER_LIBRARIES = (
    ("net.fabricmc:fabric-loader:", "fabric-loader"),
    ("org.quiltmc:quilt-loader:", "quilt-loader"),
    ("net.neoforged:neoforge:", "neoforge"),
    ("net.minecraftforge:forge:", "forge"),
)


@dataclass
class ExportResult:
    path: Path
    version_id: str
    name: str
    entries: int
    size: int
    mods: int
    config_files: int
    notes: list[str] = field(default_factory=list)


def _version_sort_key(version: str) -> list[tuple[int, object]]:
    """Natural ordering ("1.20.10" after "1.20.9", not before)."""
    key: list[tuple[int, object]] = []
    buffer = ""
    digits = False
    for char in str(version):
        if char.isdigit() == digits:
            buffer += char
            continue
        if buffer:
            key.append((0, int(buffer)) if digits else (1, buffer.lower()))
        buffer, digits = char, char.isdigit()
    if buffer:
        key.append((0, int(buffer)) if digits else (1, buffer.lower()))
    return key


def resolve_instance(game_dir: str | Path) -> tuple[str | None, Path]:
    """Best effort (current version id, directory holding mods and config).

    With per-version isolation the injected game directory already is the
    version directory; otherwise the newest installed version is reported.
    """
    game = Path(game_dir)
    if not game.is_dir():
        raise ModpackError(f"游戏目录不存在：{game}")
    if (game / f"{game.name}.json").is_file():
        return game.name, game
    versions = game / "versions"
    installed: list[str] = []
    if versions.is_dir():
        for path in versions.iterdir():
            manifest = path / f"{path.name}.json"
            if not path.is_dir() or not manifest.is_file():
                continue
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict) and raw.get("inheritsFrom"):
                continue  # loader profile: its parent is the real version
            installed.append(path.name)
    if not installed:
        return None, game
    installed.sort(key=_version_sort_key, reverse=True)
    return installed[0], game


def _read_manifest(game: Path, version_id: str) -> dict:
    for path in (
        game / "versions" / version_id / f"{version_id}.json",
        game / f"{version_id}.json",
    ):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            return raw
    return {}


def _dependencies(manifest: dict, version_id: str) -> dict[str, str]:
    """Minecraft plus loader dependencies taken from the installed version JSON."""
    minecraft = str(manifest.get("inheritsFrom") or "").strip()
    if not minecraft:
        minecraft = str(manifest.get("id") or "").strip() or version_id
    dependencies = {"minecraft": minecraft}
    for entry in manifest.get("libraries") or []:
        if not isinstance(entry, dict):
            continue
        coordinate = str(entry.get("name") or "")
        for prefix, key in LOADER_LIBRARIES:
            if coordinate.startswith(prefix):
                dependencies.setdefault(key, coordinate[len(prefix) :].split("@")[0])
    return dependencies


def _collect_entries(game: Path) -> tuple[list[tuple[str, Path]], int, int, list[str]]:
    """Zip entries for the export scope: enabled mods, config, options.txt."""
    notes: list[str] = []
    entries: list[tuple[str, Path]] = []
    mods_dir = game / "mods"
    mods = 0
    disabled = 0
    if mods_dir.is_dir():
        for path in sorted(mods_dir.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file():
                continue
            name = path.name.lower()
            # "*.jar.disabled" never ends with ".jar", so disabled mods drop out.
            if name.endswith(".jar"):
                entries.append((f"overrides/mods/{path.name}", path))
                mods += 1
            elif name.endswith(".jar.disabled"):
                disabled += 1
    config_dir = game / "config"
    config_files = 0
    if config_dir.is_dir():
        for root, folders, names in os.walk(config_dir):
            folders.sort()
            for name in sorted(names):
                source = Path(root) / name
                if not source.is_file():
                    continue
                relative = source.relative_to(config_dir).as_posix()
                entries.append((f"overrides/config/{relative}", source))
                config_files += 1
    options = game / "options.txt"
    if options.is_file():
        entries.append(("overrides/options.txt", options))
    if disabled:
        notes.append(f"{disabled} 个禁用的 Mod（*.jar.disabled）未导出")
    for folder in EXPORT_EXCLUDED:
        if (game / folder).is_dir():
            notes.append(f"按规则跳过 {folder}/")
    return entries, mods, config_files, notes


def export_pack(
    game_dir: str | Path,
    target: str | Path,
    *,
    version_id: str | None = None,
    name: str | None = None,
    progress: StageCallback | None = None,
    cancel: CancelCallback | None = None,
) -> ExportResult:
    """Write a standard Modrinth .mrpack of the current instance into *target*.

    Mods go below "overrides/mods/", not into "files": a locally installed jar
    has no Modrinth CDN download URL and no published sha1/sha512, so inventing
    "files" entries would produce a pack whose downloads can never be fetched or
    verified. Overrides are the spec-sanctioned place for local content and keep
    the archive importable by this module and by Modrinth App alike.
    """
    detected, game = resolve_instance(game_dir)
    version_id = str(version_id or detected or "").strip()
    if not version_id:
        raise ModpackError("没有找到已安装的版本，请先在「下载 / 安装」页安装一个版本")
    manifest = _read_manifest(game, version_id)
    dependencies = _dependencies(manifest, version_id)
    if not dependencies.get("minecraft"):
        raise ModpackError(f"无法确定 Minecraft 版本：{version_id}.json 缺失或已损坏")
    entries, mods, config_files, notes = _collect_entries(game)
    if mods == 0:
        raise ModpackError(
            "当前版本没有可导出的 Mod（mods 目录为空，或所有 Mod 都处于禁用状态），"
            "已取消导出，不会生成空包"
        )

    pack_name = str(name or f"{version_id} 整合包")
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": version_id,
        "name": pack_name,
        "files": [],
        "dependencies": dependencies,
    }
    planned: list[tuple[str, Path]] = []
    for entry_name, source in entries:
        safe = _safe_member(entry_name)
        if not safe or safe != entry_name or not safe.startswith("overrides/"):
            notes.append(f"文件名不安全，已跳过：{entry_name}")
            continue
        planned.append((safe, source))
    if not planned:
        raise ModpackError("所有条目都被路径安全检查拒绝，已取消导出")

    destination = Path(target)
    if destination.suffix.lower() != ".mrpack":
        destination = destination.with_name(destination.name + ".mrpack")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    total = len(planned) + 1
    if progress is not None:
        progress("正在打包整合包…", 0, total)
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "modrinth.index.json", json.dumps(index, ensure_ascii=False, indent=2)
            )
            for written, (entry_name, source) in enumerate(planned, start=1):
                if cancel is not None and cancel():
                    raise ModpackError("导出已取消")
                if progress is not None:
                    progress(f"打包 {source.name}", written, total)
                member = zipfile.ZipInfo.from_file(source, entry_name)
                member.compress_type = zipfile.ZIP_DEFLATED
                with open(source, "rb") as handle, archive.open(member, "w") as output:
                    shutil.copyfileobj(handle, output)
        # Only a complete archive may take the final name.
        os.replace(partial, destination)
    except BaseException:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    notes.append(
        f"{mods} 个 Mod 放在 overrides/mods/ 下（本地 jar 没有 Modrinth CDN 地址和官方哈希，"
        "伪造 files 条目会生成无法下载、也无法再导入的包）"
    )
    return ExportResult(
        path=destination,
        version_id=version_id,
        name=pack_name,
        entries=len(planned) + 1,
        size=destination.stat().st_size,
        mods=mods,
        config_files=config_files,
        notes=notes,
    )

