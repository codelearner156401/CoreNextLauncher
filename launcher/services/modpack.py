"""Import Modrinth ``.mrpack`` modpacks.

An ``.mrpack`` is a ZIP with ``modrinth.index.json`` (a list of files with
hashes and CDN URLs) plus ``overrides/`` and ``client-overrides/`` folders.
This module parses the manifest, downloads the files that a client needs and
writes the overrides into the selected game directory.
"""

from __future__ import annotations

import json
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


def _safe_member(name: str) -> str | None:
    """Normalise an archive path and reject anything escaping the target."""
    relative = PurePosixPath(name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        return None
    parts = [part for part in relative.parts if part not in ("", ".")]
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

    files: list[ModpackFile] = []
    total = 0
    for item in raw.get("files") or []:
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
        raise ModpackError("整合包没有声明 Minecraft 版本，无法导入")
    return ModpackInfo(
        archive=archive_path,
        name=str(raw.get("name") or archive_path.stem),
        version=str(raw.get("versionId") or ""),
        summary=str(raw.get("summary") or ""),
        mc_version=mc_version,
        loader=loader,
        loader_version=loader_version,
        files=files,
        override_count=overrides,
        total_size=total,
    )


def _download_file(
    item: ModpackFile,
    game: Path,
    cancel: CancelCallback | None,
) -> bool:
    target = game / item.path
    if target.is_file():
        if item.size and target.stat().st_size == item.size:
            return False
        if not item.size and not item.sha1:
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


def extract_overrides(
    archive_path: Path,
    game: Path,
    overwrite: bool = False,
    progress: StageCallback | None = None,
    cancel: CancelCallback | None = None,
) -> int:
    written = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = [
            name
            for name in archive.namelist()
            if name.startswith(("overrides/", "client-overrides/")) and not name.endswith("/")
        ]
        total = len(members)
        for index, name in enumerate(members):
            if cancel is not None and cancel():
                break
            relative = name.split("/", 1)[1]
            safe = _safe_member(relative)
            if not safe:
                continue
            target = game / safe
            if target.exists() and not overwrite:
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
    total = len(info.files)
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
            result.failed.append(f"{item.path}：{exc}")

    if progress is not None:
        progress("正在解压 overrides…", total, total)
    try:
        result.overrides = extract_overrides(
            info.archive, game, overwrite_overrides, progress, cancel
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
