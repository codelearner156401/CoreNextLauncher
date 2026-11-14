"""EasyTier based multiplayer: tool bootstrap plus a supervised child process.

This module replaces the old local-JSON room simulation. There is no fake state
anywhere: a link is either backed by a running easytier-core.exe child process
or it does not exist.

Room model
----------
EasyTier has no "room number". A shared room code is mapped to a network:

    network_name   = "cnl-" + normalised room code
    network_secret = sha256(room code)[:16]

Both players derive the same pair from the same code, so the only thing they
have to exchange is the code itself.

Tool bootstrap
--------------
The binary is not bundled. ensure_core() looks in
<workspace>/tools/easytier/easytier-core.exe and, when missing, tries to
download the official Windows x64 archive from GitHub Releases and extract it.
When the download is impossible (offline, blocked, no write access) it raises
EasyTierError carrying the exact URL and the exact path to place the file by
hand -- it never falls back to pretending a link exists.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..paths import workspace_root

CORE_EXE = "easytier-core.exe"
CLI_EXE = "easytier-cli.exe"
# Files that must sit next to the core: without the DLL/sys trio a fresh copy
# dies with 0xC0000135 (missing dependency).
BUNDLED_FILES = (
    CLI_EXE,
    "wintun.dll",
    "Packet.dll",
    "WinDivert64.sys",
)
RELEASES_API = "https://api.github.com/repos/EasyTier/EasyTier/releases/latest"
FALLBACK_RELEASE_URL = "https://github.com/EasyTier/EasyTier/releases/latest"
USER_AGENT = "CoreNextLauncher-EasyTier/1"

# EasyTier's public shared nodes; players behind NAT still meet here. They are
# only rendezvous points, so several are tried in order and any one of them being
# reachable is enough. The list is overridable per install (config mp_peer_nodes).
DEFAULT_PEERS = [
    "tcp://public.easytier.cn:11010",
    "tcp://public.easytier.top:11010",
    "tcp://161.33.207.13:51010",
]

# Legacy single-node alias kept for callers written against the 1-node API.
DEFAULT_PEER = DEFAULT_PEERS[0]

# EasyTier accepts repeated -p flags; the field separators below are the ones the
# UI textarea offers (one node per line, or comma/semicolon separated).
PEER_SEPARATORS = re.compile(r"[,;\s]+")

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_ERROR = "error"

ROOM_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
CONNECT_HINTS = (
    "new peer",
    "peer added",
    "peer connected",
    "connection established",
    "connected to peer",
    "network ready",
    "handshake complete",
    "established",
)
ERROR_HINTS = (
    "error",
    "failed",
    "fatal",
    "panic",
    "denied",
    "permission",
    "not permitted",
    "unreachable",
    "timeout",
)


class EasyTierError(RuntimeError):
    """Raised when the multiplayer core is missing or cannot be driven."""


# --------------------------------------------------------------------- paths
def tools_dir() -> Path:
    """Where the multiplayer binaries live inside the workspace."""
    return workspace_root() / "tools" / "easytier"


def core_path() -> Path:
    return tools_dir() / CORE_EXE


def cli_path() -> Path:
    return tools_dir() / CLI_EXE


def bundled_dir() -> Path | None:
    """The tools folder shipped inside the frozen exe (sys._MEIPASS), if any."""
    bundle = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not bundle:
        return None
    return Path(str(bundle)) / "tools" / "easytier"


def bundled_core_path() -> Path | None:
    """Path of the frozen-build core, or None when this is a source checkout."""
    folder = bundled_dir()
    if folder is None:
        return None
    candidate = folder / CORE_EXE
    return candidate if candidate.is_file() else None


def _install_bundled_core(folder: Path) -> Path | None:
    """Copy the bundled tools into the writable workspace; None when it fails.

    The DLLs travel with the binary: a core without wintun.dll / Packet.dll /
    WinDivert64.sys dies with 0xC0000135. The core is copied last so its presence
    means the install finished, which keeps the copy idempotent and restartable.
    """
    target = tools_dir()
    try:
        sources = [folder / name for name in BUNDLED_FILES]
        sources += [p for p in folder.iterdir() if p.is_file() and p not in sources]
        src_core = folder / CORE_EXE
        sources = [p for p in sources if p != src_core] + [src_core]
        target.mkdir(parents=True, exist_ok=True)
        for source in sources:
            if not source.is_file():
                continue
            destination = target / source.name
            try:
                same = (
                    destination.is_file()
                    and destination.stat().st_size == source.stat().st_size
                )
            except OSError:
                same = False
            if not same:
                shutil.copy2(source, destination)
    except Exception:  # noqa: BLE001 - a failed copy must not break startup
        return None
    installed = target / CORE_EXE
    return installed if installed.is_file() else None


def find_core() -> Path | None:
    """Locate a usable core: workspace copy, frozen bundle, then legacy spots."""
    local = core_path()
    if local.is_file():
        return local
    bundled = bundled_core_path()
    if bundled is not None:
        # Copy beside the workspace so the user can replace it and so the core
        # never runs from the temporary extraction folder.
        return _install_bundled_core(bundled.parent) or bundled
    for candidate in (tools_dir().parent / CORE_EXE, tools_dir() / "bin" / CORE_EXE):
        if candidate.is_file():
            return candidate
    return None


def manual_instructions() -> str:
    """Human readable recovery steps used by the UI and by error messages."""
    return (
        "联机内核未就绪。请任选一种方式：\n"
        "1) 在联机页点「下载联机内核」，自动拉取官方 Releases；\n"
        "2) 手动从 " + FALLBACK_RELEASE_URL + " 下载\n"
        "   easytier-windows-x86_64-*.zip，解压后把 easytier-core.exe 放到：\n"
        "   " + str(core_path()) + "\n"
        "   （同目录放 easytier-cli.exe 可选，用于查看对端列表）"
    )


# ------------------------------------------------------------------- download
def _github_asset() -> tuple[str, str, int]:
    """Return (name, download_url, size) of the Windows x64 archive."""
    request = urllib.request.Request(
        RELEASES_API,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        lowered = name.lower()
        if "windows-x86_64" in lowered and lowered.endswith(".zip"):
            return name, str(asset.get("browser_download_url") or ""), int(asset.get("size") or 0)
    raise EasyTierError(
        "官方 Releases 里没有找到 Windows x64 的 EasyTier 压缩包。\n" + manual_instructions()
    )


def download_core(
    progress: Callable[[str, int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """Fetch and unpack the official EasyTier archive into the tools folder."""
    try:
        name, url, size = _github_asset()
    except EasyTierError:
        raise
    except Exception as exc:  # noqa: BLE001 - API blocked / DNS / TLS failures
        raise EasyTierError(
            "查询联机内核版本失败：" + type(exc).__name__ + ": " + str(exc)
            + chr(10) + manual_instructions()
        ) from exc
    target_dir = tools_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    archive = target_dir / name
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        done = 0
        with urllib.request.urlopen(request, timeout=120) as response, open(archive, "wb") as handle:
            total = int(response.headers.get("Content-Length") or size or 0)
            while True:
                if cancel is not None and cancel():
                    raise EasyTierError("已取消下载联机内核")
                block = response.read(1 << 18)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                if progress is not None:
                    progress(name, done, total)
    except EasyTierError:
        archive.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - reported with manual steps
        archive.unlink(missing_ok=True)
        raise EasyTierError(
            "下载联机内核失败：" + type(exc).__name__ + ": " + str(exc) + "\n" + manual_instructions()
        ) from exc

    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target_dir)
    except Exception as exc:  # noqa: BLE001
        raise EasyTierError("解压联机内核失败：" + str(exc) + "\n" + manual_instructions()) from exc
    finally:
        archive.unlink(missing_ok=True)

    found = find_core()
    if found is None:
        raise EasyTierError("压缩包内没有找到 easytier-core.exe。\n" + manual_instructions())
    return found


def ensure_core(
    progress: Callable[[str, int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """Return the core path, downloading it on first use."""
    found = find_core()
    if found is not None:
        return found
    return download_core(progress=progress, cancel=cancel)


# --------------------------------------------------------------- room mapping
def normalise_room(code: str) -> str:
    text = str(code or "").strip()
    if not ROOM_CODE_RE.match(text):
        raise EasyTierError("房间号只能使用 3-32 位字母、数字、下划线或短横线。")
    return text


def room_to_network(code: str) -> tuple[str, str]:
    """Map a shared room code onto EasyTier's network name/secret pair."""
    room = normalise_room(code)
    digest = hashlib.sha256(("cnl-easytier:" + room).encode("utf-8")).hexdigest()
    return "cnl-" + room.lower(), digest[:16]


def parse_peers(text: str) -> list[str]:
    """Split persisted peer text into a node list (order kept, blanks dropped).

    Accepts every format the advanced textarea can hold: one node per line, or
    comma / semicolon separated, in any mix; duplicates keep their first place.
    """
    peers: list[str] = []
    for part in PEER_SEPARATORS.split(str(text or "")):
        item = part.strip()
        if item and item not in peers:
            peers.append(item)
    return peers


def build_arguments(
    code: str, peer: str = "", ipv4: str = "", dhcp: bool = True
) -> list[str]:
    """Command line for easytier-core (flags verified with the v2.6.4 --help).

    Every node gets its own -p pair. -d (DHCP) is on by default: with an empty
    --ipv4 EasyTier only forwards packets and never creates a TUN device, so a
    room would not form a real LAN without it.
    """
    network_name, network_secret = room_to_network(code)
    argv = ["--network-name", network_name, "--network-secret", network_secret]
    for item in parse_peers(peer) or list(DEFAULT_PEERS):
        argv += ["-p", item]
    if dhcp:
        argv.append("-d")
    if ipv4:
        argv += ["--ipv4", str(ipv4).strip()]
    return argv


# -------------------------------------------------------------- line parsing
_LEVEL_RE = re.compile(r"\b(ERROR|WARN|INFO|DEBUG|TRACE)\b")
_HARD_FAIL_WORDS = (
    "error",
    "failed",
    "failure",
    "timeout",
    "refused",
    "denied",
    "panic",
    "unreachable",
    "resolveerror",
)


def classify_line(line: str) -> str:
    """Map one core output line onto connected / error / log.

    EasyTier logs carry an explicit level token (INFO / WARN / ERROR). A retry
    such as "INFO ... connect to peer error ..." is routine, so only ERROR-level
    lines raise an error event -- otherwise a flaky peer pops dialogs forever.
    Lines without a level token fall back to keyword matching.
    """
    text = str(line or "").strip()
    if not text:
        return "log"
    match = _LEVEL_RE.search(text)
    level = (match.group(1) if match else "").upper()
    lowered = text.lower()
    hard_fail = any(word in lowered for word in _HARD_FAIL_WORDS)
    if level == "ERROR":
        return "error"
    if level == "WARN":
        return "log"
    if not level and any(hint in lowered for hint in ERROR_HINTS):
        return "error"
    if (
        any(hint in lowered for hint in CONNECT_HINTS)
        and not hard_fail
        and "disconnect" not in lowered
    ):
        return "connected"
    return "log"


def is_admin() -> bool:
    """True when the process can create a TUN adapter without elevation."""
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - unknown means "assume not elevated"
        return False


def relaunch_as_admin() -> bool:
    """Re-start this launcher elevated; returns False when the user declines."""
    if os.name != "nt":
        return False
    executable = sys.executable
    parameters = " ".join(subprocess.list2cmdline([arg]) for arg in sys.argv)
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, parameters, str(Path.cwd()), 1
        )
        return int(result) > 32
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------- session
@dataclass
class LinkEvent:
    kind: str  # state | log | error | exit
    text: str = ""
    state: str = ""


@dataclass
class LinkSession:
    """Supervises one easytier-core child process."""

    code: str = ""
    peer: str = ""
    ipv4: str = ""
    dhcp: bool = True
    state: str = STATE_STOPPED
    lines: list[str] = field(default_factory=list)
    process: subprocess.Popen | None = None
    _reader: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- lifecycle -------------------------------------------------------
    def start(self, on_event: Callable[[LinkEvent], None] | None = None) -> None:
        core = find_core()
        if core is None:
            raise EasyTierError(manual_instructions())
        if self.is_running():
            return
        if os.name == "nt" and not is_admin():
            raise EasyTierError(
                "EasyTier 需要管理员权限创建虚拟网卡。请以管理员身份重新启动启动器，"
                "或在联机页点「以管理员身份重启」。"
            )
        argv = [
            str(core),
            *build_arguments(self.code, self.peer, self.ipv4, dhcp=self.dhcp),
        ]
        flags = 0
        startupinfo = None
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            self.process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
                startupinfo=startupinfo,
                cwd=str(core.parent),
            )
        except OSError as exc:
            self._set_state(STATE_ERROR)
            raise EasyTierError("无法启动联机内核：" + str(exc)) from exc
        self._set_state(STATE_CONNECTING)
        self._emit(on_event, LinkEvent("state", state=STATE_CONNECTING))
        self._reader = threading.Thread(
            target=self._pump, args=(on_event,), name="easytier-log", daemon=True
        )
        self._reader.start()

    def _pump(self, on_event: Callable[[LinkEvent], None] | None) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            for raw in process.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                with self._lock:
                    self.lines.append(line)
                    del self.lines[:-400]
                kind = classify_line(line)
                self._emit(on_event, LinkEvent("log", text=line))
                if kind == "connected" and self.state != STATE_CONNECTED:
                    self._set_state(STATE_CONNECTED)
                    self._emit(on_event, LinkEvent("state", state=STATE_CONNECTED))
                elif kind == "error":
                    self._emit(on_event, LinkEvent("error", text=line))
        finally:
            code = process.wait()
            if self.state != STATE_STOPPED:
                detail = self.tail(3)
                self._set_state(STATE_ERROR if code else STATE_STOPPED)
                self._emit(
                    on_event,
                    LinkEvent(
                        "exit",
                        text="联机内核已退出（退出码 " + str(code) + "）"
                        + ("：" + detail if detail else ""),
                        state=self.state,
                    ),
                )

    def stop(self) -> None:
        process = self.process
        self.process = None
        self._set_state(STATE_STOPPED)
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=6)
        except Exception:  # noqa: BLE001 - kill is best effort
            try:
                process.kill()
            except Exception:  # noqa: BLE001
                pass

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def tail(self, count: int = 6) -> str:
        with self._lock:
            return " | ".join(self.lines[-count:])

    # -- helpers ---------------------------------------------------------
    def _set_state(self, state: str) -> None:
        with self._lock:
            self.state = state

    @staticmethod
    def _emit(on_event: Callable[[LinkEvent], None] | None, event: LinkEvent) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:  # noqa: BLE001 - a UI callback must never kill the pump
            pass


def clear_legacy_store() -> None:
    """Delete the room cache written by the old local-JSON implementation."""
    try:
        target = workspace_root() / "cache" / "terracotta_rooms.json"
        if target.is_file():
            target.unlink()
    except OSError:
        pass
