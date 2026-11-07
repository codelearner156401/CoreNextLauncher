"""Minecraft server list ping (Java Edition) and a small server bookmarks store.

Implements the vanilla Server List Ping handshake directly on a TCP socket so
the launcher can show MOTD, player counts and latency without pulling in a
third-party protocol library.
"""

from __future__ import annotations

import base64
import json
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import data_dir


class PingError(RuntimeError):
    pass


@dataclass
class ServerEntry:
    name: str
    address: str
    port: int = 25565
    version_id: str = ""
    note: str = ""

    @property
    def target(self) -> str:
        return f"{self.address}:{self.port}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "address": self.address,
            "port": self.port,
            "version_id": self.version_id,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ServerEntry":
        return cls(
            name=str(raw.get("name") or raw.get("address") or "服务器"),
            address=str(raw.get("address") or ""),
            port=int(raw.get("port") or 25565),
            version_id=str(raw.get("version_id") or ""),
            note=str(raw.get("note") or ""),
        )


@dataclass
class ServerStatus:
    entry: ServerEntry
    online: bool = False
    latency_ms: int = 0
    version_name: str = ""
    protocol: int = 0
    players_online: int = 0
    players_max: int = 0
    motd: str = ""
    favicon: str = ""
    sample: list[str] = field(default_factory=list)
    error: str = ""


SERVERS_PATH = data_dir() / "servers.json"


# ------------------------------------------------------------------ varints
def _write_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 32
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _read_varint(sock: socket.socket) -> int:
    result = 0
    for shift in range(0, 35, 7):
        raw = sock.recv(1)
        if not raw:
            raise PingError("服务器提前关闭了连接")
        byte = raw[0]
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result
    raise PingError("服务器返回了无效的数据包")


def _write_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return _write_varint(len(encoded)) + encoded


def _read_string(sock: socket.socket) -> str:
    length = _read_varint(sock)
    if length < 0 or length > 4 * 1024 * 1024:
        raise PingError("服务器返回的字符串长度异常")
    chunks = bytearray()
    while len(chunks) < length:
        block = sock.recv(length - len(chunks))
        if not block:
            raise PingError("服务器提前关闭了连接")
        chunks.extend(block)
    return chunks.decode("utf-8", "replace")


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        block = sock.recv(size - len(data))
        if not block:
            raise PingError("服务器提前关闭了连接")
        data.extend(block)
    return bytes(data)


def _packet(payload: bytes) -> bytes:
    return _write_varint(len(payload)) + payload


# ------------------------------------------------------------------ address
def split_address(text: str, default_port: int = 25565) -> tuple[str, int]:
    raw = str(text or "").strip()
    if raw.lower().startswith(("mc://", "minecraft://")):
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0]
    if raw.startswith("["):  # [::1]:25565
        host, _, rest = raw[1:].partition("]")
        port = rest.lstrip(":")
        return host, int(port) if port.isdigit() else default_port
    if raw.count(":") == 1:
        host, port = raw.split(":", 1)
        if port.isdigit():
            return host, int(port)
    return raw, default_port


def _flatten_chat(component) -> str:
    if component is None:
        return ""
    if isinstance(component, str):
        return component
    if isinstance(component, list):
        return "".join(_flatten_chat(item) for item in component)
    if isinstance(component, dict):
        text = str(component.get("text") or "")
        extra = component.get("extra")
        if extra:
            text += _flatten_chat(extra)
        translate = component.get("translate")
        if translate and not text:
            text = str(translate)
        return text
    return str(component)


# --------------------------------------------------------------------- ping
def ping(address: str, port: int | None = None, timeout: float = 3.5) -> ServerStatus:
    host, parsed_port = split_address(address)
    server_port = int(port or parsed_port or 25565)
    entry = ServerEntry(name=host, address=host, port=server_port)
    status = ServerStatus(entry=entry)
    if not host or not (1 <= server_port <= 65535):
        status.error = "地址或端口无效"
        return status
    started = time.perf_counter()
    try:
        with socket.create_connection((host, server_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            handshake = (
                _write_varint(0x00)
                + _write_varint(767)  # protocol number; vanilla replies regardless
                + _write_string(host)
                + struct.pack(">H", server_port)
                + _write_varint(1)
            )
            sock.sendall(_packet(handshake) + _packet(_write_varint(0x00)))
            _read_varint(sock)  # length
            packet_id = _read_varint(sock)
            if packet_id != 0x00:
                raise PingError(f"服务器返回了意外的数据包（id={packet_id}）")
            payload = _read_string(sock)
            # Optional ping/pong round trip for a more accurate latency.
            try:
                sock.sendall(_packet(_write_varint(0x01) + struct.pack(">q", int(time.time() * 1000))))
                _read_varint(sock)
                if _read_varint(sock) == 0x01:
                    _read_exact(sock, 8)
            except (OSError, PingError):
                pass
    except socket.timeout:
        status.error = "连接超时"
        return status
    except ConnectionRefusedError:
        status.error = "连接被拒绝（服务器未启动或端口错误）"
        return status
    except socket.gaierror:
        status.error = "无法解析服务器地址"
        return status
    except (OSError, PingError) as exc:
        status.error = str(exc) or "连接失败"
        return status

    status.latency_ms = max(1, int((time.perf_counter() - started) * 1000))
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        status.error = "服务器返回的状态不是有效 JSON"
        return status
    if not isinstance(data, dict):
        status.error = "服务器返回的状态格式异常"
        return status

    version = data.get("version") or {}
    if isinstance(version, dict):
        status.version_name = str(version.get("name") or "")
        try:
            status.protocol = int(version.get("protocol") or 0)
        except (TypeError, ValueError):
            status.protocol = 0
    players = data.get("players") or {}
    if isinstance(players, dict):
        try:
            status.players_online = int(players.get("online") or 0)
            status.players_max = int(players.get("max") or 0)
        except (TypeError, ValueError):
            pass
        sample = players.get("sample")
        if isinstance(sample, list):
            status.sample = [
                str(item.get("name"))
                for item in sample
                if isinstance(item, dict) and item.get("name")
            ][:12]
    description = data.get("description")
    status.motd = _flatten_chat(description).strip()
    favicon = data.get("favicon")
    if isinstance(favicon, str) and favicon.startswith("data:image"):
        status.favicon = favicon
    status.online = True
    return status


def ping_many(entries: list[ServerEntry], timeout: float = 3.5, workers: int = 6) -> list[ServerStatus]:
    if not entries:
        return []

    def run(entry: ServerEntry) -> ServerStatus:
        result = ping(entry.address, entry.port, timeout=timeout)
        result.entry = entry
        return result

    with ThreadPoolExecutor(max_workers=min(workers, len(entries))) as pool:
        return list(pool.map(run, entries))


def favicon_pixmap_data(status: ServerStatus) -> bytes:
    """Decode the base64 PNG icon returned by the server (empty on failure)."""
    if not status.favicon:
        return b""
    try:
        _prefix, _separator, payload = status.favicon.partition(",")
        return base64.b64decode(payload)
    except (ValueError, TypeError):
        return b""


def favicon_cache_path(entry: ServerEntry) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in entry.target)
    return data_dir() / "server_icons" / f"{safe}.png"


# -------------------------------------------------------------------- store
def load_servers(path: Path = SERVERS_PATH) -> list[ServerEntry]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = raw.get("servers") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    servers: list[ServerEntry] = []
    for item in items:
        if isinstance(item, dict) and item.get("address"):
            servers.append(ServerEntry.from_dict(item))
    return servers


def save_servers(servers: list[ServerEntry], path: Path = SERVERS_PATH) -> None:
    payload = {"version": 1, "servers": [server.to_dict() for server in servers]}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
