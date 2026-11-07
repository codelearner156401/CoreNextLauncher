"""Detect the Java runtimes installed on this machine.

Candidates come from the Windows registry (all vendor keys, both 32/64-bit
views), the usual install directories, ``PATH``, the launcher's own managed
runtime folder and Minecraft's ``runtime/`` directory.  Every candidate is
probed with ``java -version`` so the dropdown can show a real version instead
of guessing from the folder name.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..paths import data_dir

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_VERSION_RE = re.compile(r'(?:openjdk|java)\s+(?:version\s+)?"([^"]+)"', re.IGNORECASE)
_LEGACY_RE = re.compile(r"openjdk\s+(\d+[^,\s]*)", re.IGNORECASE)

_VENDORS = (
    ("temurin", "Eclipse Temurin"),
    ("adoptopenjdk", "AdoptOpenJDK"),
    ("adoptium", "Eclipse Temurin"),
    ("zulu", "Azul Zulu"),
    ("microsoft", "Microsoft OpenJDK"),
    ("corretto", "Amazon Corretto"),
    ("liberica", "BellSoft Liberica"),
    ("semeru", "IBM Semeru"),
    ("dragonwell", "Alibaba Dragonwell"),
    ("graalvm", "GraalVM"),
    ("oracle", "Oracle"),
    ("openjdk", "OpenJDK"),
)


@dataclass(frozen=True)
class JavaInstall:
    """One discovered Java executable."""

    path: str
    version: str = ""
    major: int = 0
    vendor: str = ""
    arch: str = ""
    source: str = ""

    @property
    def label(self) -> str:
        parts: list[str] = []
        if self.major:
            parts.append(f"Java {self.major}")
            if self.version and self.version != str(self.major):
                parts.append(self.version)
        else:
            parts.append("Java（未知版本）")
        if self.vendor:
            parts.append(self.vendor)
        if self.arch:
            parts.append(f"{self.arch} 位")
        if self.source:
            parts.append(self.source)
        return " · ".join(parts)

    @property
    def missing(self) -> bool:
        return not Path(self.path).is_file()


def _pe_arch(path: Path) -> str:
    """Return ``"64"`` or ``"32"`` by reading the PE header of *path*."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(0x400)
        if len(header) < 0x40 or header[:2] != b"MZ":
            return ""
        offset = struct.unpack_from("<I", header, 0x3C)[0]
        if offset + 6 > len(header):
            return ""
        machine = struct.unpack_from("<H", header, offset + 4)[0]
        if machine in (0x8664, 0xAA64):
            return "64"
        if machine in (0x14C,):
            return "32"
    except OSError:
        return ""
    return ""


def _java_exe(home: Path) -> Path | None:
    """Turn a JDK root / bin directory / exe path into ``java.exe``."""
    home = Path(home)
    try:
        if home.is_file():
            if home.suffix.lower() != ".exe":
                return None
            if home.name.lower() == "javaw.exe":
                sibling = home.with_name("java.exe")
                return sibling if sibling.is_file() else home
            return home
        bin_dir = home if home.name.lower() == "bin" else home / "bin"
        java = bin_dir / "java.exe"
        if java.is_file():
            return java
    except OSError:
        return None
    return None


def _registry_homes() -> list[Path]:
    homes: list[Path] = []
    if os.name != "nt":
        return homes
    try:
        import winreg
    except ImportError:  # pragma: no cover - Windows always has winreg
        return homes

    roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    vendors = (
        r"SOFTWARE\JavaSoft\Java Development Kit",
        r"SOFTWARE\JavaSoft\Java Runtime Environment",
        r"SOFTWARE\JavaSoft\JDK",
        r"SOFTWARE\JavaSoft\JRE",
        r"SOFTWARE\Eclipse Adoptium\JDK",
        r"SOFTWARE\Eclipse Adoptium\JRE",
        r"SOFTWARE\Eclipse Foundation\JDK",
        r"SOFTWARE\Microsoft\JDK",
        r"SOFTWARE\Azul Systems\Zulu",
        r"SOFTWARE\Amazon Corretto",
        r"SOFTWARE\BellSoft\Liberica",
        r"SOFTWARE\Semeru",
    )
    views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    for root in roots:
        for vendor in vendors:
            for view in views:
                try:
                    with winreg.OpenKey(root, vendor, 0, winreg.KEY_READ | view) as key:
                        count = winreg.QueryInfoKey(key)[0]
                        for index in range(count):
                            try:
                                name = winreg.EnumKey(key, index)
                            except OSError:
                                break
                            try:
                                with winreg.OpenKey(key, name) as sub:
                                    for value_name in ("JavaHome", "InstallationPath"):
                                        try:
                                            value, _ = winreg.QueryValueEx(sub, value_name)
                                        except OSError:
                                            continue
                                        if value:
                                            homes.append(Path(str(value)))
                            except OSError:
                                continue
                except OSError:
                    continue
    return homes


def _common_homes() -> list[Path]:
    homes: list[Path] = []
    env = os.environ
    java_home = env.get("JAVA_HOME", "").strip()
    if java_home:
        homes.append(Path(java_home))

    for base in (env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("ProgramW6432")):
        if not base:
            continue
        root = Path(base)
        if not root.is_dir():
            continue
        for name in (
            "Java",
            "Eclipse Adoptium",
            "Eclipse Foundation",
            "Microsoft",
            "Zulu",
            "BellSoft",
            "Amazon Corretto",
            "Semeru",
            "Temurin",
            "GraalVM",
            "Alibaba",
        ):
            folder = root / name
            homes.append(folder)
            try:
                if folder.is_dir():
                    homes.extend([p for p in folder.iterdir() if p.is_dir()])
            except OSError:
                pass

    local = env.get("LOCALAPPDATA")
    if local:
        for name in ("Eclipse Adoptium", "Programs", "Microsoft", "Zulu", "Amazon Corretto"):
            folder = Path(local) / name
            homes.append(folder)
            try:
                if folder.is_dir():
                    homes.extend([p for p in folder.iterdir() if p.is_dir()])
                    for child in folder.iterdir():
                        if child.is_dir() and child.name.lower() in {"eclipse adoptium", "jdk", "java"}:
                            homes.extend([p for p in child.iterdir() if p.is_dir()])
            except OSError:
                pass

    user = env.get("USERPROFILE")
    if user:
        homes.append(Path(user) / ".jdks")
        try:
            jdks = Path(user) / ".jdks"
            if jdks.is_dir():
                homes.extend([p for p in jdks.iterdir() if p.is_dir()])
        except OSError:
            pass
    return homes


def _runtime_homes(game_dir: Path | None) -> list[Path]:
    """Java shipped inside a Minecraft ``runtime/`` or the managed folder."""
    roots: list[Path] = []
    if game_dir:
        roots.append(Path(game_dir) / "runtime")
    roots.append(data_dir() / "runtime")
    homes: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        patterns = (
            "*/bin/java.exe",
            "*/*/*/bin/java.exe",
            "*/*/bin/java.exe",
        )
        for pattern in patterns:
            try:
                homes.extend(root.glob(pattern))
            except OSError:
                continue
    return homes


def _path_homes() -> list[Path]:
    homes: list[Path] = []
    for name in ("java.exe", "javaw.exe"):
        found = shutil.which(name)
        if found:
            homes.append(Path(found))
    return homes


def discover_candidates(game_dir: str | Path | None = None) -> list[tuple[Path, str]]:
    """Return unique ``(java.exe, source label)`` pairs, in discovery order."""
    seen: set[str] = set()
    result: list[tuple[Path, str]] = []

    def add(path: Path | None, source: str) -> None:
        if path is None:
            return
        exe = _java_exe(path)
        if exe is None:
            return
        try:
            key = str(exe.resolve()).lower()
        except OSError:
            key = str(exe).lower()
        if key in seen:
            return
        seen.add(key)
        result.append((exe, source))

    for home in _registry_homes():
        add(home, "系统注册表")
    for home in _common_homes():
        add(home, "安装目录")
    if game_dir:
        for exe in _runtime_homes(Path(game_dir)):
            add(exe, "Minecraft 运行时")
    for exe in _runtime_homes(None):
        add(exe, "启动器托管")
    for exe in _path_homes():
        add(exe, "PATH")
    return result


def probe_java(exe: str | Path, source: str = "") -> JavaInstall | None:
    """Run ``java -version`` and build a :class:`JavaInstall`."""
    path = Path(exe)
    if not path.is_file():
        return None
    try:
        completed = subprocess.run(
            [str(path), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = f"{completed.stderr}\n{completed.stdout}".strip()
    if not output:
        return None

    version = ""
    match = _VERSION_RE.search(output)
    if match:
        version = match.group(1).strip()
    else:
        legacy = _LEGACY_RE.search(output)
        if legacy:
            version = legacy.group(1).strip()

    major = 0
    if version:
        head = version.split("_")[0]
        parts = head.split(".")
        try:
            if parts[0] == "1" and len(parts) > 1:
                major = int(parts[1])
            else:
                major = int(parts[0])
        except (ValueError, IndexError):
            major = 0

    lower = output.lower()
    vendor = ""
    for needle, name in _VENDORS:
        if needle in lower:
            vendor = name
            break

    arch = _pe_arch(path)
    return JavaInstall(
        path=str(path),
        version=version,
        major=major,
        vendor=vendor,
        arch=arch,
        source=source,
    )


def scan_java(
    game_dir: str | Path | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    include_missing: bool = False,
) -> list[JavaInstall]:
    """Probe every candidate in parallel and return a sorted result list."""
    candidates = discover_candidates(game_dir)
    total = len(candidates)
    if on_progress:
        on_progress(0, total, "正在扫描本机 Java…")
    installs: list[JavaInstall] = []
    if not candidates:
        return installs

    with ThreadPoolExecutor(max_workers=min(6, total)) as pool:
        futures = [
            pool.submit(probe_java, exe, source) for exe, source in candidates
        ]
        for index, future in enumerate(futures):
            try:
                install = future.result()
            except Exception:
                install = None
            if install is not None and (include_missing or not install.missing):
                installs.append(install)
            if on_progress:
                on_progress(index + 1, total, f"已检测 {index + 1}/{total} 个 Java")

    installs.sort(key=lambda item: (item.major, item.arch == "64", item.version), reverse=True)
    return installs


def best_install(installs: Iterable[JavaInstall], minimum_major: int = 17) -> JavaInstall | None:
    """Pick a sensible default: 64-bit, newest, and at least *minimum_major*."""
    pool = [item for item in installs if not item.missing]
    if not pool:
        return None
    preferred = [item for item in pool if item.arch == "64"] or pool
    suitable = [item for item in preferred if item.major >= minimum_major]
    return (suitable or preferred)[0]


def find_by_major(installs: Iterable[JavaInstall], major: int) -> JavaInstall | None:
    exact = [item for item in installs if item.major == major and not item.missing]
    if not exact:
        return None
    return sorted(exact, key=lambda item: (item.arch == "64", item.version), reverse=True)[0]
