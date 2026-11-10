"""Small HTTP helper used by every network-facing service.

Only the standard library is used so the launcher stays a single PyInstaller
bundle with no extra wheels.  The helper adds what ``urllib`` lacks: a proper
User-Agent, retries for transient failures, an explicit TLS context and
streaming downloads that are verified before they replace a real file.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "CoreNextLauncher/1.8.1 (Windows; Minecraft launcher)"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
CHUNK = 256 * 1024

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


class NetworkError(RuntimeError):
    """A request failed for a reason the caller can surface to the user."""


def query(params: dict[str, Any]) -> str:
    """Encode non-empty query parameters (lists become JSON arrays)."""
    clean: dict[str, str] = {}
    for key, value in params.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple)):
            clean[key] = json.dumps(list(value), ensure_ascii=False)
        elif isinstance(value, bool):
            clean[key] = "true" if value else "false"
        else:
            clean[key] = str(value)
    return urllib.parse.urlencode(clean)


def _retry_delay(attempt: int) -> float:
    return min(8.0, 0.6 * (2**attempt))


def open_url(
    url: str,
    *,
    method: str | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
):
    """Open *url* and return the response object.

    Retries connection errors and HTTP 5xx/429 responses.  HTTP 4xx responses
    are raised as :class:`NetworkError` with a readable body excerpt because
    retrying them is pointless.
    """
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        **(headers or {}),
    }
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        request = urllib.request.Request(
            url, data=data, headers=request_headers, method=method
        )
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:400]
            except Exception:  # pragma: no cover - body is best effort
                pass
            if exc.code in (408, 425, 429) or exc.code >= 500:
                last_error = exc
                if attempt + 1 < max(1, retries):
                    time.sleep(_retry_delay(attempt))
                    continue
            raise NetworkError(_http_message(url, exc.code, body)) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            last_error = exc
            reason = getattr(exc, "reason", exc)
            if attempt + 1 < max(1, retries):
                time.sleep(_retry_delay(attempt))
                continue
            raise NetworkError(f"连接服务器失败：{reason}") from exc
        except (http.client.HTTPException, OSError) as exc:
            last_error = exc
            if attempt + 1 < max(1, retries):
                time.sleep(_retry_delay(attempt))
                continue
            raise NetworkError(f"网络请求失败：{exc}") from exc
    raise NetworkError(f"网络请求失败：{last_error}")


def _http_message(url: str, code: int, body: str) -> str:
    host = urllib.parse.urlsplit(url).netloc or url
    if code == 404:
        return f"{host} 返回 404：请求的资源不存在（{url}）"
    if code == 401:
        return f"{host} 返回 401：API Key 无效或已过期"
    if code == 403:
        return f"{host} 返回 403：访问被拒绝或触发频率限制"
    if code == 429:
        return f"{host} 返回 429：请求过于频繁，请稍后再试"
    snippet = f"：{body.strip()[:200]}" if body.strip() else ""
    return f"{host} 返回 HTTP {code}{snippet}"


def read_bytes(url: str, *, timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> bytes:
    with open_url(url, timeout=timeout, retries=retries) as response:
        return response.read()


def get_json(url: str, *, timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> Any:
    with open_url(url, timeout=timeout, retries=retries) as response:
        payload = response.read().decode("utf-8", "replace")
    try:
        return json.loads(payload) if payload.strip() else {}
    except json.JSONDecodeError as exc:
        raise NetworkError(f"服务器返回了无法解析的数据（{url}）") from exc


def post_json(
    url: str,
    payload: Any,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    merged = {"Content-Type": "application/json", "Accept": "application/json", **(headers or {})}
    with open_url(
        url, method="POST", data=body, headers=merged, timeout=timeout, retries=retries
    ) as response:
        text = response.read().decode("utf-8", "replace")
    try:
        return json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise NetworkError(f"服务器返回了无法解析的数据（{url}）") from exc


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def sha1_text(path: Path) -> str:
    return file_sha1(Path(path))


def download(
    url: str,
    dest: Path,
    *,
    expected_sha1: str | None = None,
    expected_sha256: str | None = None,
    expected_sha512: str | None = None,
    expected_size: int | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    timeout: float = 60.0,
) -> Path:
    """Download *url* into *dest* atomically, verifying hashes when given.

    The body is streamed into ``<dest>.part``; the real file is only replaced
    after the size and hashes match, so an interrupted download can never leave
    a half-written JAR that Minecraft would try to load.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(dest.name + ".part")
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    sha512 = hashlib.sha512()
    done = 0
    last_report = 0.0
    try:
        with open_url(url, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or expected_size or 0)
            with open(temp, "wb") as handle:
                while True:
                    if cancel is not None and cancel():
                        raise NetworkError("下载已取消")
                    block = response.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)
                    sha1.update(block)
                    sha256.update(block)
                    sha512.update(block)
                    done += len(block)
                    now = time.monotonic()
                    if progress is not None and (now - last_report > 0.06 or done == total):
                        last_report = now
                        progress(done, total)
        if expected_size is not None and done != int(expected_size):
            raise NetworkError(f"文件大小不匹配：期望 {expected_size} 字节，实际 {done} 字节")
        if expected_sha1 and sha1.hexdigest().lower() != expected_sha1.lower():
            raise NetworkError("SHA1 校验失败，文件可能已损坏，请重试")
        if expected_sha256 and sha256.hexdigest().lower() != expected_sha256.lower():
            raise NetworkError("SHA256 校验失败，文件可能已损坏，请重试")
        if expected_sha512 and sha512.hexdigest().lower() != expected_sha512.lower():
            raise NetworkError("SHA512 校验失败，文件可能已损坏，请重试")
        if progress is not None:
            progress(done, done)
        os.replace(temp, dest)
        return dest
    except Exception:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
