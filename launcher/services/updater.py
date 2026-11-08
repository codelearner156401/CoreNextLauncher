"""Self-update through the GitHub Releases API.

Checks the newest release of a configured repository, compares its tag with
``APP_VERSION``, downloads the new executable and hands the file swap to a
small batch script that runs after this process exits, then restarts the app.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..constants import GITHUB_API
from ..paths import cache_dir, is_frozen
from . import net

TAG_RE = re.compile(r"v?(\d+(?:\.\d+)*)", re.IGNORECASE)


class UpdateError(RuntimeError):
    pass


@dataclass
class ReleaseAsset:
    name: str
    url: str
    size: int = 0
    content_type: str = ""


@dataclass
class UpdateInfo:
    tag: str
    version: str
    title: str
    notes: str
    html_url: str
    published_at: str
    prerelease: bool = False
    asset: ReleaseAsset | None = None

    @property
    def has_asset(self) -> bool:
        return self.asset is not None and bool(self.asset.url)


def parse_version(text: str) -> tuple[int, ...]:
    match = TAG_RE.search(str(text or ""))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(remote: str, local: str) -> bool:
    remote_parts = parse_version(remote)
    local_parts = parse_version(local)
    if not remote_parts:
        return False
    length = max(len(remote_parts), len(local_parts))
    remote_padded = remote_parts + (0,) * (length - len(remote_parts))
    local_padded = local_parts + (0,) * (length - len(local_parts))
    return remote_padded > local_padded


def normalize_repo(repo: str) -> str:
    text = str(repo or "").strip().rstrip("/")
    text = re.sub(r"^https?://github\.com/", "", text, flags=re.IGNORECASE)
    return text


def _pick_asset(assets: list[dict]) -> ReleaseAsset | None:
    candidates: list[dict] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name.lower().endswith(".exe"):
            continue
        if "blockmap" in name.lower() or "setup" in name.lower():
            continue
        candidates.append(item)
    if not candidates:
        return None
    # Skip ARM64 builds when a native x64 executable is also published.
    x64 = [item for item in candidates if "arm64" not in str(item.get("name")).lower()]
    if x64:
        candidates = x64
    preferred = [
        item
        for item in candidates
        if str(item.get("name") or "").lower()
        in {"corenextlauncher.exe", "corenextlauncher-portable.exe"}
    ]
    chosen = (preferred or candidates)[0]
    return ReleaseAsset(
        name=str(chosen.get("name") or ""),
        url=str(chosen.get("browser_download_url") or ""),
        size=int(chosen.get("size") or 0),
        content_type=str(chosen.get("content_type") or ""),
    )


def check(repo: str, current_version: str, timeout: float = 20.0) -> UpdateInfo | None:
    """Return release info when a newer version exists, else ``None``."""
    slug = normalize_repo(repo)
    if not slug or "/" not in slug:
        raise UpdateError("还没有配置 GitHub 仓库（格式：owner/repo），请先在设置中填写")
    try:
        data = net.get_json(
            f"{GITHUB_API}/repos/{slug}/releases/latest",
            timeout=timeout,
            retries=2,
        )
    except net.NetworkError as exc:
        message = str(exc)
        if "404" in message:
            raise UpdateError(
                f"GitHub 仓库 {slug} 不存在或没有发布 Release，请在设置中检查仓库名"
            ) from exc
        raise
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise UpdateError("GitHub 返回的 Release 数据不完整")
    tag = str(data["tag_name"])
    info = UpdateInfo(
        tag=tag,
        version=tag.lstrip("vV"),
        title=str(data.get("name") or tag),
        notes=str(data.get("body") or ""),
        html_url=str(data.get("html_url") or ""),
        published_at=str(data.get("published_at") or ""),
        prerelease=bool(data.get("prerelease")),
        asset=_pick_asset(data.get("assets") or []),
    )
    if not is_newer(tag, current_version):
        return None
    return info


def download_update(
    info: UpdateInfo,
    progress: net.ProgressCallback | None = None,
    cancel: net.CancelCallback | None = None,
) -> Path:
    if info.asset is None or not info.asset.url:
        raise UpdateError("这个 Release 没有提供 .exe 下载文件")
    folder = cache_dir() / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (info.asset.name or "CoreNextLauncher-new.exe")
    if target.exists():
        target.unlink(missing_ok=True)
    net.download(
        info.asset.url,
        target,
        expected_size=info.asset.size or None,
        progress=progress,
        cancel=cancel,
        timeout=180,
    )
    return target


def current_executable() -> Path:
    return Path(sys.executable if is_frozen() else sys.argv[0]).resolve()


def can_self_update() -> bool:
    return is_frozen() and os.name == "nt"


def _bat_path() -> Path:
    return Path(os.environ.get("TEMP") or str(cache_dir())) / f"corenextlauncher_update_{int(time.time())}.bat"


def _quote(path: Path) -> str:
    return '"' + str(path).replace('"', "") + '"'


def build_update_command(new_exe: Path, target_exe: Path, pid: int | None = None) -> tuple[Path, str]:
    """Write the helper script and return its path plus the script text."""
    process_id = int(pid if pid is not None else os.getpid())
    exe_name = target_exe.name or "CoreNextLauncher.exe"
    script = f"""@echo off
setlocal
chcp 65001 >nul
set "TARGET={target_exe}"
set "SOURCE={new_exe}"
set "PID={process_id}"
rem Wait for the launcher to exit, at most 90 seconds.
for /L %%i in (1,1,90) do (
  tasklist /FI "PID eq %PID%" 2>nul | find /I "%PID%" >nul
  if errorlevel 1 goto replace
  timeout /t 1 /nobreak >nul
)
:replace
copy /Y "%SOURCE%" "%TARGET%" >nul
if errorlevel 1 goto failed
start "" "%TARGET%"
del /F /Q "%SOURCE%" >nul 2>nul
del /F /Q "%~f0"
exit /b 0
:failed
echo 更新失败：无法覆盖 %TARGET% > "%~dp0corenextlauncher_update_error.txt"
del /F /Q "%~f0"
exit /b 1
"""
    path = _bat_path()
    path.write_text(script.replace("\n", "\r\n"), encoding="utf-8")
    return path, script


def launch_update_script(bat_path: Path) -> None:
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=creationflags,
        close_fds=True,
        cwd=str(bat_path.parent),
    )


def apply_update(new_exe: Path, cleanup: bool = True) -> Path:
    """Prepare the swap.  The caller must quit the application afterwards."""
    if not can_self_update():
        raise UpdateError("源码模式下无法自动替换程序，请手动下载新版，或使用打包后的 exe")
    target = current_executable()
    if not new_exe.is_file():
        raise UpdateError("下载的更新文件不存在，请重试")
    bat_path, _script = build_update_command(new_exe, target)
    launch_update_script(bat_path)
    return bat_path
