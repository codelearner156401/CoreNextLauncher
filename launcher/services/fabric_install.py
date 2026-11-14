"""One-click Fabric + Sodium + Iris setup.

Fabric publishes a ready-made launcher profile JSON (``profile/json``) that
inherits from the vanilla version, so installing Fabric is a matter of writing
that profile and fetching its libraries from the Fabric Maven.  Sodium, Iris
and Fabric API are then pulled from Modrinth for the exact game version.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..constants import FABRIC_META
from . import net, modrinth

StageCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]

OPTIMIZATION_MODS = (
    ("sodium", "Sodium（渲染优化）"),
    ("iris", "Iris Shaders（光影支持）"),
    ("fabric-api", "Fabric API（前置库）"),
)


class FabricError(RuntimeError):
    pass


@dataclass
class FabricInstall:
    profile_id: str
    profile_path: Path
    loader_version: str
    libraries: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass
class OptimizationResult:
    fabric: FabricInstall
    mods: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def maven_path(name: str) -> str:
    """``group:artifact:version[:classifier]`` → Maven repository path."""
    parts = [part for part in str(name).split(":") if part]
    if len(parts) < 3:
        return ""
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ""
    filename = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + ".jar"
    return f"{group.replace('.', '/')}/{artifact}/{version}/{filename}"


def loader_versions(mc_version: str) -> list[dict]:
    data = net.get_json(f"{FABRIC_META}/versions/loader/{mc_version}")
    return data if isinstance(data, list) else []


def latest_loader(mc_version: str) -> str:
    versions = loader_versions(mc_version)
    if not versions:
        raise FabricError(f"Fabric 不支持 Minecraft {mc_version}（meta 没有返回加载器）")
    for item in versions:
        loader = item.get("loader") or {}
        if loader.get("stable") and loader.get("version"):
            return str(loader["version"])
    loader = versions[0].get("loader") or {}
    if not loader.get("version"):
        raise FabricError("Fabric meta 返回的加载器数据不完整")
    return str(loader["version"])


def fetch_profile(mc_version: str, loader_version: str) -> dict:
    data = net.get_json(f"{FABRIC_META}/versions/loader/{mc_version}/{loader_version}/profile/json")
    if not isinstance(data, dict) or not data.get("libraries"):
        raise FabricError("Fabric 返回的安装配置不完整")
    return data


def installed_profile(versions_dir: str | Path, mc_version: str) -> str:
    """Return an already installed Fabric profile id for *mc_version*."""
    folder = Path(versions_dir)
    if not folder.is_dir():
        return ""
    best = ""
    for path in folder.glob("fabric-loader-*-*"):
        manifest = path / f"{path.name}.json"
        if not manifest.is_file():
            continue
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(raw.get("inheritsFrom") or "") == mc_version:
            best = path.name
    return best


def _int_or_zero(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _library_ok(path: Path, sha1: str, size: int) -> bool:
    """Is the file already on disk the one the profile describes?

    Compares the published sha1 when the profile has one and falls back to the
    declared size otherwise, so a truncated or stale jar is re-downloaded.
    """
    try:
        if size and path.stat().st_size != size:
            return False
    except OSError:
        return False
    if not sha1:
        return True
    try:
        return net.file_sha1(path).lower() == sha1.lower()
    except OSError:
        return False


def _download_libraries(
    profile: dict,
    game_dir: Path,
    progress: StageCallback | None,
    cancel: CancelCallback | None,
) -> tuple[int, int, int]:
    libraries = profile.get("libraries") or []
    downloaded = 0
    skipped = 0
    failed = 0
    total = len(libraries)
    for index, library in enumerate(libraries):
        if cancel is not None and cancel():
            raise FabricError("已取消 Fabric 安装")
        if not isinstance(library, dict):
            failed += 1
            continue
        name = str(library.get("name") or "")
        base_url = str(library.get("url") or "")
        relative = maven_path(name)
        if not relative:
            failed += 1
            continue
        dest = game_dir / "libraries" / relative
        expected_sha1 = str(library.get("sha1") or "").strip()
        expected_size = _int_or_zero(library.get("size"))
        if dest.is_file() and dest.stat().st_size > 0 and _library_ok(dest, expected_sha1, expected_size):
            skipped += 1
            continue
        if not base_url:
            # No maven URL: the library comes from the vanilla download chain.
            skipped += 1
            continue
        if progress is not None:
            progress(f"下载 Fabric 依赖：{Path(relative).name}", index, total)
        net.download(
            base_url.rstrip("/") + "/" + relative,
            dest,
            expected_sha1=expected_sha1 or None,
            expected_size=expected_size or None,
        )
        downloaded += 1
    return downloaded, skipped, failed


def install_fabric(
    game_dir: str | Path,
    mc_version: str,
    loader_version: str | None = None,
    progress: StageCallback | None = None,
    cancel: CancelCallback | None = None,
) -> FabricInstall:
    game = Path(game_dir)
    base_jar = game / "versions" / mc_version / f"{mc_version}.jar"
    base_json = game / "versions" / mc_version / f"{mc_version}.json"
    if not base_json.is_file() or not base_jar.is_file():
        raise FabricError(
            f"还没有安装原版 {mc_version}。请先在「下载 / 安装」页安装官方版本，再回来一键优化。"
        )

    if progress is not None:
        progress("正在获取 Fabric 加载器版本…", 0, 0)
    loader = loader_version or latest_loader(mc_version)
    profile = fetch_profile(mc_version, loader)
    profile_id = str(profile.get("id") or f"fabric-loader-{loader}-{mc_version}")
    profile["id"] = profile_id
    profile.setdefault("inheritsFrom", mc_version)
    profile.setdefault("type", "release")

    target_dir = game / "versions" / profile_id
    target_dir.mkdir(parents=True, exist_ok=True)
    profile_path = target_dir / f"{profile_id}.json"
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if progress is not None:
        progress("正在下载 Fabric 依赖库…", 0, len(profile.get("libraries") or []))
    downloaded, skipped, failed = _download_libraries(profile, game, progress, cancel)
    return FabricInstall(
        profile_id=profile_id,
        profile_path=profile_path,
        loader_version=loader,
        libraries=downloaded,
        skipped=skipped,
        failed=failed,
    )


def download_optimization_mods(
    game_dir: str | Path,
    mc_version: str,
    include_fabric_api: bool = True,
    progress: StageCallback | None = None,
    cancel: CancelCallback | None = None,
) -> list[Path]:
    mods_dir = Path(game_dir) / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    wanted = [
        (slug, label)
        for slug, label in OPTIMIZATION_MODS
        if include_fabric_api or slug != "fabric-api"
    ]
    installed: list[Path] = []
    for index, (slug, label) in enumerate(wanted):
        if cancel is not None and cancel():
            raise FabricError("已取消安装")
        if progress is not None:
            progress(f"正在安装 {label}…", index, len(wanted))
        installed.append(modrinth.install_project(slug, mc_version, "fabric", mods_dir))
        if progress is not None:
            progress(f"{label} 已安装", index + 1, len(wanted))
    return installed


def optimize(
    game_dir: str | Path,
    mc_version: str,
    loader_version: str | None = None,
    include_fabric_api: bool = True,
    progress: StageCallback | None = None,
    cancel: CancelCallback | None = None,
) -> OptimizationResult:
    """Install Fabric (if needed) plus Sodium / Iris / Fabric API."""
    fabric = install_fabric(game_dir, mc_version, loader_version, progress, cancel)
    notes: list[str] = []
    existing = installed_profile(Path(game_dir) / "versions", mc_version)
    if existing and existing != fabric.profile_id:
        notes.append(f"检测到已有的 Fabric 版本 {existing}，本次已安装 {fabric.profile_id}。")
    mods = download_optimization_mods(game_dir, mc_version, include_fabric_api, progress, cancel)
    return OptimizationResult(fabric=fabric, mods=mods, notes=notes)
