"""并发分片下载服务, 供百宝箱「高速下载」页使用。

纯标准库实现 (复用 launcher.services.net), 不导入 PySide6,
因此可以在测试和命令行里直接跑。包含三部分:

* GitHub 镜像地址改写 (仅 github.com / objects.githubusercontent.com /
  raw.githubusercontent.com 生效, 规则是"镜像前缀 + 原始完整 URL");
* 多线程 HTTP Range 分片下载, 服务端不支持分片时自动退回单流;
* 优先走 C++ 内核 download-url 命令, 内核不认识该命令时回退到内置下载器。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable

from ..paths import config_dir
from . import net

DEFAULT_MIRRORS = ("https://ghfast.top/", "https://gh-proxy.com/", "https://ghproxy.net/")
MIRROR_HOSTS = ("github.com", "objects.githubusercontent.com", "raw.githubusercontent.com")
MIRRORS_FILE = "download_mirrors.json"

DEFAULT_THREADS = 8
MAX_THREADS = 32
MIN_SHARD_SIZE = 1 << 20  # 1 MiB: 更小的文件用单流更快
CHUNK = 256 * 1024
SPEED_WINDOW = 1.0
DEFAULT_TIMEOUT = 30.0
REPORT_INTERVAL = 0.15

UNKNOWN_COMMAND_MARKERS = (
    "未知命令",
    "unknown command",
    "unknown_command",
    "unknown-command",
    "unsupported command",
    "不支持的命令",
)

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


class _Cancelled(Exception):
    """Internal: a stop request interrupted the transfer."""


class _RangeRejected(Exception):
    """Internal: the server answered a Range request with a full body."""


class _InFlight:
    """Registers open responses so a stop request can close their sockets."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[Any] = []
        self._closed = False

    def add(self, response: Any) -> Any:
        with self._lock:
            if self._closed:
                raise _Cancelled()
            self._items.append(response)
        return response

    def drop(self, response: Any) -> None:
        with self._lock:
            try:
                self._items.remove(response)
            except ValueError:
                pass

    def close_all(self) -> None:
        with self._lock:
            self._closed = True
            items, self._items = self._items, []
        for response in items:
            try:
                response.close()
            except Exception:
                pass


class _ByteCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = 0

    def add(self, amount: int) -> None:
        with self._lock:
            self._value += max(0, int(amount))

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


class SpeedMeter:
    """Bytes per second over a sliding window, plus UI formatting."""

    def __init__(self, window: float = SPEED_WINDOW) -> None:
        self.window = max(0.05, float(window))
        self._samples: list[tuple[float, int]] = []

    def reset(self) -> None:
        self._samples.clear()

    def update(self, done: int, now: float | None = None) -> float:
        """Feed a byte counter and return the current speed in bytes/second."""
        stamp = time.monotonic() if now is None else float(now)
        self._samples.append((stamp, max(0, int(done))))
        while len(self._samples) > 2 and stamp - self._samples[0][0] > self.window:
            self._samples.pop(0)
        first_stamp, first_done = self._samples[0]
        last_stamp, last_done = self._samples[-1]
        elapsed = last_stamp - first_stamp
        if elapsed <= 0:
            return 0.0
        return max(0.0, (last_done - first_done) / elapsed)


def format_rate(speed: float) -> str:
    """Render a byte/second rate the way the tab shows it."""
    rate = max(0.0, float(speed or 0.0))
    if rate >= 1024 * 1024:
        return f"{rate / (1024 * 1024):.1f} MB/s"
    if rate >= 1024:
        return f"{rate / 1024:.1f} KB/s"
    return f"{rate:.0f} B/s"


def looks_like_unknown_command(message: str) -> bool:
    """True when *message* says the kernel does not know the command."""
    text = (message or "").lower()
    return any(marker in text for marker in UNKNOWN_COMMAND_MARKERS)


# --------------------------------------------------------------------- mirrors
def mirror_path(path: Path | None = None) -> Path:
    return Path(path) if path else config_dir() / MIRRORS_FILE


def normalize_mirrors(items: Iterable[str]) -> list[str]:
    """Clean mirror prefixes: scheme, trailing slash, de-duplicated."""
    cleaned: list[str] = []
    for raw in items or ():
        text = str(raw or "").strip()
        if not text or text.startswith("#"):
            continue
        if "://" not in text:
            text = "https://" + text
        text = text.rstrip("/") + "/"
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


def parse_mirror_text(text: str) -> list[str]:
    """Parse the editable list: one prefix per line, also , ; and spaces."""
    return normalize_mirrors(re.split(r"[\s,;]+", text or ""))


def load_mirrors(path: Path | None = None) -> list[str]:
    target = mirror_path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return list(DEFAULT_MIRRORS)
    items = raw.get("mirrors") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return list(DEFAULT_MIRRORS)
    cleaned = normalize_mirrors(items)
    return cleaned or list(DEFAULT_MIRRORS)


def save_mirrors(mirrors: Iterable[str], path: Path | None = None) -> Path:
    target = mirror_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mirrors": normalize_mirrors(mirrors)}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def mirror_host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def is_mirrorable(url: str) -> bool:
    """Only GitHub download hosts may be rewritten."""
    try:
        scheme = urllib.parse.urlsplit(str(url or "")).scheme.lower()
    except ValueError:
        return False
    if scheme not in ("http", "https"):
        return False
    host = mirror_host(url)
    if not host:
        return False
    return any(host == item or host.endswith("." + item) for item in MIRROR_HOSTS)


def apply_mirror(
    url: str, mirrors: Iterable[str] | None = None, enabled: bool = True
) -> str:
    """Return the first mirror URL for *url* (unchanged when not applicable)."""
    original = str(url or "").strip()
    if not enabled or not is_mirrorable(original):
        return original
    for prefix in normalize_mirrors(mirrors or DEFAULT_MIRRORS):
        return prefix + original
    return original


def candidate_urls(
    url: str, mirrors: Iterable[str] | None = None, enabled: bool = True
) -> list[str]:
    """Mirror URLs first, then the original as the last resort."""
    original = str(url or "").strip()
    if not original:
        return []
    if not enabled or not is_mirrorable(original):
        return [original]
    urls = [prefix + original for prefix in normalize_mirrors(mirrors or DEFAULT_MIRRORS)]
    urls.append(original)
    return urls


# ------------------------------------------------------------------ transfer
def discard_partial(dest: str | Path) -> bool:
    """Delete the .part file next to *dest*; True when a file was removed."""
    target = Path(dest)
    temp = target.with_name(target.name + ".part")
    try:
        temp.unlink()
        return True
    except OSError:
        return False


def _probe(url: str, timeout: float) -> tuple[int, bool]:
    """Return (total_bytes, supports_ranges) for *url*."""
    response = None
    try:
        response = net.open_url(
            url, headers={"Range": "bytes=0-0"}, timeout=timeout, retries=2
        )
        status = getattr(response, "status", 200)
        length = int(response.headers.get("Content-Length") or 0)
        if status == 206:
            content_range = response.headers.get("Content-Range") or ""
            match = re.search(r"/(\d+)", content_range)
            total = int(match.group(1)) if match else length
            return total, True
        return length, False
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _split(total: int, workers: int) -> list[tuple[int, int]]:
    count = max(1, int(workers))
    base = total // count
    spans: list[tuple[int, int]] = []
    start = 0
    for index in range(count):
        end = total if index == count - 1 else start + base
        if end > start:
            spans.append((start, end - 1))
        start = end
    return spans or [(0, max(0, total - 1))]


def _copy(response: Any, handle: Any, stop: threading.Event, counter: _ByteCounter) -> None:
    while True:
        if stop.is_set():
            raise _Cancelled()
        block = response.read(CHUNK)
        if not block:
            return
        handle.write(block)
        counter.add(len(block))


def _fetch(
    url: str,
    path: Path,
    start: int | None,
    end: int | None,
    stop: threading.Event,
    counter: _ByteCounter,
    timeout: float,
    inflight: _InFlight,
) -> None:
    headers: dict[str, str] = {}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    response = inflight.add(
        net.open_url(url, headers=headers, timeout=timeout, retries=2)
    )
    try:
        status = getattr(response, "status", 200)
        if start is not None:
            if status != 206:
                raise _RangeRejected(f"{url} 不支持分片下载（HTTP {status}）")
            with open(path, "r+b") as handle:
                handle.seek(start)
                _copy(response, handle, stop, counter)
        else:
            with open(path, "wb") as handle:
                _copy(response, handle, stop, counter)
    finally:
        inflight.drop(response)
        try:
            response.close()
        except Exception:
            pass


def is_http_message(message: str) -> bool:
    """True when the text already names an HTTP status (from net)."""
    return bool(re.search(r"\bHTTP \d{3}\b|\b[45]\d{2}\b", message or ""))


def _describe(exc: BaseException, url: str) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if is_http_message(message):
        return message
    return f"{message}（{mirror_host(url) or url}）"


def _download_one(
    url: str,
    dest: Path,
    temp: Path,
    threads: int,
    sha1: str,
    progress: ProgressCallback | None,
    cancel: CancelCallback | None,
    timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    total, ranges_ok = _probe(url, timeout)
    workers = max(1, min(int(threads or 1), MAX_THREADS))
    stop = threading.Event()
    counter = _ByteCounter()
    errors: list[BaseException] = []
    inflight = _InFlight()
    last_report = [0.0]
    sharded = bool(ranges_ok and total >= MIN_SHARD_SIZE and workers > 1)

    def cancelled() -> bool:
        return cancel is not None and cancel()

    def report(force: bool = False) -> None:
        if progress is None:
            return
        now = time.monotonic()
        if not force and now - last_report[0] < REPORT_INTERVAL:
            return
        last_report[0] = now
        progress(counter.value, total)

    def run_worker(start: int | None, end: int | None) -> None:
        try:
            _fetch(url, temp, start, end, stop, counter, timeout, inflight)
        except _Cancelled:
            return
        except (OSError, ValueError):
            if stop.is_set():
                return
            errors.append(net.NetworkError("下载连接被中断"))
            stop.set()
        except BaseException as exc:
            if stop.is_set():
                return
            errors.append(exc)
            stop.set()

    def spawn(start: int | None, end: int | None) -> threading.Thread:
        thread = threading.Thread(target=run_worker, args=(start, end), daemon=True)
        thread.start()
        return thread

    if sharded:
        with open(temp, "wb") as handle:
            handle.truncate(total)
        spans = _split(total, min(workers, max(1, total // MIN_SHARD_SIZE)))
        threads_list = [spawn(begin, finish) for begin, finish in spans]
    else:
        threads_list = [spawn(None, None)]

    try:
        while any(thread.is_alive() for thread in threads_list):
            if cancelled():
                stop.set()
                inflight.close_all()
            report()
            time.sleep(0.05)
        for thread in threads_list:
            thread.join(timeout=5.0)
        if cancelled():
            raise _Cancelled()
        if stop.is_set() and not errors:
            raise _Cancelled()
        if errors:
            raise net.NetworkError(_describe(errors[0], url))
        report(force=True)
        if total > 0 and counter.value != total:
            raise net.NetworkError(
                f"下载不完整：收到 {counter.value} 字节，预期 {total} 字节"
            )
        if sha1:
            actual = net.file_sha1(temp)
            expected = sha1.strip().lower()
            if actual.lower() != expected:
                raise net.NetworkError(
                    f"文件校验失败（SHA1 不匹配）：期望 {expected}，实际 {actual}"
                )
        os.replace(temp, dest)
    except BaseException:
        discard_partial(dest)
        raise

    seconds = max(0.0, time.monotonic() - started)
    return {
        "path": str(dest),
        "bytes": counter.value,
        "total": total,
        "seconds": seconds,
        "speed": (counter.value / seconds) if seconds > 0 else 0.0,
        "sharded": sharded,
        "url": url,
    }


def download(
    url: str,
    dest: str | Path,
    *,
    threads: int = DEFAULT_THREADS,
    sha1: str = "",
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    mirrors: Iterable[str] | None = None,
    use_mirror: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Download *url* into *dest* with sharded workers and mirror fallback.

    The body is written to a temporary .part file first; the real file is only
    replaced once every byte arrived (and the SHA1 matched, when given), so a
    cancelled or failed transfer leaves no stray temporary file behind.
    """
    if not str(url or "").strip():
        raise net.NetworkError("下载地址为空")
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    candidates = candidate_urls(url, mirrors, use_mirror)
    failures: list[str] = []
    for candidate in candidates:
        if cancel is not None and cancel():
            discard_partial(target)
            raise net.NetworkError("下载已取消")
        temp = target.with_name(target.name + ".part")
        try:
            return _download_one(
                candidate, target, temp, threads, sha1, progress, cancel, timeout
            )
        except _Cancelled:
            discard_partial(target)
            raise net.NetworkError("下载已取消") from None
        except _RangeRejected as exc:
            failures.append(str(exc))
            discard_partial(target)
        except net.NetworkError as exc:
            discard_partial(target)
            failures.append(str(exc))
            if cancel is not None and cancel():
                raise net.NetworkError("下载已取消") from None
    detail = "；".join(failures[-3:]) or "未知原因"
    raise net.NetworkError(f"下载失败（已尝试 {len(candidates)} 个地址）：{detail}")


# ------------------------------------------------------------------- dispatch
def _core_result(
    data: dict[str, Any] | None, dest: str | Path, started: float, url: str
) -> dict[str, Any]:
    info = data if isinstance(data, dict) else {}
    path_text = str(info.get("path") or "").strip() or str(dest)
    target = Path(path_text)
    size = 0
    for key in ("size", "total", "bytes"):
        try:
            size = int(info.get(key) or 0)
        except (TypeError, ValueError):
            size = 0
        if size > 0:
            break
    if size <= 0 and target.is_file():
        try:
            size = target.stat().st_size
        except OSError:
            size = 0
    seconds = max(0.0, time.monotonic() - started)
    return {
        "path": str(target),
        "bytes": size,
        "total": size,
        "seconds": seconds,
        "speed": (size / seconds) if seconds > 0 else 0.0,
        "sharded": True,
        "url": url,
        "engine": "内核分片下载",
    }


def _event_forwarder(
    progress: ProgressCallback | None,
) -> Callable[[dict[str, Any]], None] | None:
    """Translate kernel events into (done, total) progress calls."""
    if progress is None:
        return None

    def forward(event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "progress":
            try:
                progress(int(event.get("done") or 0), int(event.get("total") or 0))
            except (TypeError, ValueError):
                return
        elif kind == "done":
            data = event.get("data")
            info = data if isinstance(data, dict) else {}
            try:
                size = int(info.get("size") or info.get("total") or 0)
            except (TypeError, ValueError):
                size = 0
            if size > 0:
                progress(size, size)

    return forward


def download_via(
    client: Any,
    url: str,
    dest: str | Path,
    *,
    sha1: str = "",
    threads: int = DEFAULT_THREADS,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    mirrors: Iterable[str] | None = None,
    use_mirror: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Prefer the C++ kernel command, fall back to the built-in downloader.

    *client* is anything exposing available / download_url (a
    launcher.core_client.CoreClient); None skips the kernel. The fallback also
    happens when the kernel does not know the command, so an older kernel can
    never break the tab.
    """
    started = time.monotonic()
    candidates = candidate_urls(url, mirrors, use_mirror)
    primary = candidates[0] if candidates else str(url or "").strip()
    if client is not None and getattr(client, "available", True):
        try:
            data = client.download_url(
                primary,
                dest,
                sha1=sha1,
                threads=threads,
                on_event=_event_forwarder(progress),
                cancel=cancel,
            )
            return _core_result(data, dest, started, primary)
        except Exception as exc:  # noqa: BLE001 - classified below
            message = str(exc) or exc.__class__.__name__
            if not looks_like_unknown_command(message):
                raise
    result = download(
        url,
        dest,
        threads=threads,
        sha1=sha1,
        progress=progress,
        cancel=cancel,
        mirrors=mirrors,
        use_mirror=use_mirror,
        timeout=timeout,
    )
    result["engine"] = "内置下载器"
    return result
