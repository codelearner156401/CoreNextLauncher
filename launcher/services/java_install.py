"""Download and unpack a Temurin JDK from the Adoptium API.

Used when no suitable Java is installed: the archive is fetched with a size
and SHA-256 check, extracted under ``CoreNextLauncher/kernel/runtime`` next to the program
and the resulting ``java.exe`` is returned so the launcher can select it
immediately.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..constants import ADOPTIUM_API
from ..paths import kernel_dir
from . import net

StageCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]

ARCHIVE_NAME_RE = re.compile(r"OpenJDK\d+U-jdk_(?P<arch>[^_]+)_windows_hotspot", re.IGNORECASE)


@dataclass
class JavaRelease:
    major: int
    version: str
    vendor: str
    url: str
    filename: str
    size: int
    sha256: str
    release_name: str = ""

    @property
    def label(self) -> str:
        return f"Temurin {self.version}（{self.major}）"


def _architecture() -> str:
    # The launcher ships as a 32-bit executable, but Minecraft needs the native
    # architecture; ARM64 Windows runs x64 builds through emulation.
    import platform

    machine = (platform.machine() or "").lower()
    if machine in {"arm64", "aarch64"}:
        return "x64"
    return "x64"


def resolve_release(major: int = 21, architecture: str | None = None) -> JavaRelease:
    """Ask Adoptium for the newest Temurin JDK of *major* for Windows."""
    arch = architecture or _architecture()
    query = net.query(
        {
            "architecture": arch,
            "image_type": "jdk",
            "os": "windows",
            "vendor": "eclipse",
        }
    )
    data = net.get_json(f"{ADOPTIUM_API}/assets/latest/{int(major)}/hotspot?{query}")
    if not isinstance(data, list) or not data:
        raise net.NetworkError(f"Adoptium 没有返回 Java {major} 的下载信息")
    item = data[0]
    binary = item.get("binary") or {}
    package = binary.get("package") or {}
    version_info = item.get("version") or {}
    if not package.get("link"):
        raise net.NetworkError("Adoptium 返回的数据缺少下载地址")
    return JavaRelease(
        major=int(version_info.get("major") or major),
        version=str(version_info.get("openjdk_version") or version_info.get("semver") or major),
        vendor=str(item.get("vendor") or "Eclipse Adoptium"),
        url=str(package["link"]),
        filename=str(package.get("name") or f"temurin-{major}.zip"),
        size=int(package.get("size") or 0),
        sha256=str(package.get("checksum") or ""),
        release_name=str(item.get("release_name") or ""),
    )


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    names = archive.namelist()
    for name in names:
        candidate = (target / name).resolve()
        if not str(candidate).startswith(str(target.resolve())):
            raise net.NetworkError("压缩包内包含非法路径，已停止解压")
    archive.extractall(target)
    return names


def install_java(
    major: int = 21,
    progress: StageCallback | None = None,
    cancel: CancelCallback | None = None,
) -> Path:
    """Download + extract Temurin *major* and return ``java.exe``."""
    if progress:
        progress("正在查询 Adoptium 最新版本…", 0, 0)
    release = resolve_release(major)
    runtime_root = kernel_dir() / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    folder_name = f"temurin-{release.major}-{release.version.split('+')[0]}"
    install_dir = runtime_root / folder_name
    archive_path = runtime_root / release.filename

    if install_dir.is_dir():
        existing = _find_java(install_dir)
        if existing is not None:
            if progress:
                progress(f"已存在 {release.label}，直接使用", 1, 1)
            return existing

    if progress:
        progress(f"正在下载 {release.label}（{release.size / (1024 * 1024):.0f} MB）…", 0, release.size)

    last = {"done": 0, "total": release.size}

    def on_chunk(done: int, total: int) -> None:
        last["done"] = done
        last["total"] = total or release.size
        if progress:
            progress("正在下载 Java 运行时…", done, last["total"])

    net.download(
        release.url,
        archive_path,
        expected_sha256=release.sha256 or None,
        expected_size=release.size or None,
        progress=on_chunk,
        cancel=cancel,
        timeout=120,
    )

    if cancel is not None and cancel():
        archive_path.unlink(missing_ok=True)
        raise net.NetworkError("已取消 Java 安装")

    if progress:
        progress("正在解压 Java 运行时…", 0, 0)
    temp_dir = runtime_root / f".{folder_name}.tmp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = _safe_extract(archive, temp_dir)
        java = _find_java(temp_dir)
        if java is None:
            raise net.NetworkError("解压后没有找到 java.exe，压缩包可能不完整")
        # Temurin archives wrap everything in a single jdk-21.x.y+z directory;
        # flatten that one level so the runtime folder layout stays predictable.
        top_entries = {name.split("/")[0] for name in names if name}
        top = temp_dir / next(iter(top_entries)) if len(top_entries) == 1 else None
        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)
        if top is not None and top.is_dir() and _find_java(top) is not None:
            top.rename(install_dir)
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            temp_dir.rename(install_dir)
        archive_path.unlink(missing_ok=True)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    java = _find_java(install_dir)
    if java is None:
        raise net.NetworkError("Java 安装完成但找不到可执行文件")
    if progress:
        progress(f"Java 安装完成：{java}", 1, 1)
    return java


def _find_java(root: Path) -> Path | None:
    for pattern in ("bin/java.exe", "*/bin/java.exe", "*/*/bin/java.exe"):
        for candidate in root.glob(pattern):
            if candidate.is_file():
                return candidate
    return None


def suggested_major(mc_version: str, manifest_major: int | None = None) -> int:
    """Pick the Java major a Minecraft version expects."""
    if manifest_major:
        return int(manifest_major)
    match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", str(mc_version or ""))
    if not match:
        return 21
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3) or 0)
    if major >= 26:
        # New version scheme (26.x) targets Java 25.
        return 25
    if major != 1:
        return 21
    if minor >= 21:
        return 21
    if minor == 20:
        # 1.20.5 switched from Java 17 to Java 21.
        return 21 if patch >= 5 else 17
    if minor >= 18:
        return 17
    if minor == 17:
        return 16
    return 8
