"""Thin, framework-agnostic client for the C++ core (build/mc_core.exe).

The core speaks a line-delimited JSON protocol on stdout and accepts one JSON
document on stdin:

    {"type": "status", "text": "..."}
    {"type": "progress", "done": 1, "total": 5, "current": "..."}
    {"type": "log", "text": "..."}
    {"type": "done", "data": ...}
    {"type": "error", "message": "..."}

This module only depends on the standard library so it can be reused from the
Qt UI, a CLI or tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

from .paths import app_root, core_path

PROJECT_ROOT = app_root()
DEFAULT_EXE = PROJECT_ROOT / "build" / "mc_core.exe"


class CoreError(RuntimeError):
    """Raised when the core cannot be started or reports a fatal failure."""


class CoreUnknownCommand(CoreError):
    """The kernel does not know the requested command.

    The message always contains "未知命令" (or "unknown"), so callers that only
    inspect the text can retry the work with a pure-Python implementation.
    """

    def __init__(self, message: str = "", command: str = "") -> None:
        text = (message or "").strip()
        if not text:
            text = f"内核未知命令：{command or 'download-url'}（当前内核版本不支持）"
        elif not _is_unknown_command(text):
            text = f"内核未知命令：{command or 'download-url'}（{text}）"
        super().__init__(text)
        self.command = command or "download-url"


_UNKNOWN_COMMAND_MARKERS = (
    "未知命令",
    "unknown command",
    "unknown_command",
    "unknown-command",
    "unsupported command",
    "不支持的命令",
)


def _is_unknown_command(message: str) -> bool:
    text = (message or "").lower()
    return any(marker in text for marker in _UNKNOWN_COMMAND_MARKERS)


def _decode_line(line: str) -> dict[str, Any]:
    line = line.strip()
    if not line:
        return {"type": "raw", "text": ""}
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return {"type": "raw", "text": line}
    if isinstance(obj, dict):
        return obj
    return {"type": "raw", "text": line}


class CoreClient:
    """Invoke ``mc_core.exe`` with a JSON payload and read its JSON events."""

    def __init__(self, exe_path: str | Path | None = None) -> None:
        self.exe_path = Path(exe_path) if exe_path else core_path()
        self.last_returncode: int | None = None
        self.last_stderr: str = ""
        # Only used by kill(): call()/stream() keep their process locally and
        # register it here so a stop request can reach the running child.
        self._active_proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ info
    @property
    def available(self) -> bool:
        return self.exe_path.is_file()

    def kernel_size(self) -> int:
        try:
            return self.exe_path.stat().st_size
        except OSError:
            return 0

    # --------------------------------------------------------------- process
    def _check_available(self) -> None:
        if not self.available:
            raise CoreError(f"未找到内核可执行文件：{self.exe_path}")

    def _popen(self, command: str) -> subprocess.Popen[str]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        return subprocess.Popen(
            [str(self.exe_path), command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(PROJECT_ROOT),
            creationflags=creationflags,
        )

    def clone(self) -> "CoreClient":
        """Return a client with the same kernel path but its own registration.

        Workers each own a clone so stopping one core command can never
        terminate the process of another command.
        """
        return CoreClient(self.exe_path)

    def _register_process(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._active_proc = proc

    def _release_process(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            if self._active_proc is proc:
                self._active_proc = None

    def kill(self) -> None:
        """Terminate the process this client currently has registered."""
        with self._lock:
            proc = self._active_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    # ------------------------------------------------------------ blocking
    def call(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> list[dict[str, Any]]:
        """Run a short-lived command and return every event it emitted."""
        self._check_available()
        proc = self._popen(command)
        self._register_process(proc)
        try:
            try:
                stdout, stderr = proc.communicate(
                    json.dumps(payload or {}, ensure_ascii=False) + "\n",
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise CoreError(f"内核命令 {command!r} 执行超时（{timeout:.0f}s）") from None
        finally:
            self._release_process(proc)

        self.last_returncode = proc.returncode
        self.last_stderr = (stderr or "").strip()
        events = [_decode_line(line) for line in (stdout or "").splitlines() if line.strip()]
        if proc.returncode != 0 and not any(e.get("type") == "error" for e in events):
            raise CoreError(self.last_stderr or f"内核退出码 {proc.returncode}")
        return events

    # ------------------------------------------------------------- streaming
    def stream(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield core events as soon as each output line arrives."""
        self._check_available()
        proc = self._popen(command)
        self._register_process(proc)
        self.last_returncode = None
        self.last_stderr = ""

        stderr_chunks: list[str] = []

        def drain_stderr() -> None:
            if proc.stderr is None:
                return
            try:
                for chunk in proc.stderr:
                    stderr_chunks.append(chunk)
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=drain_stderr, daemon=True)
        reader.start()
        try:
            assert proc.stdin is not None and proc.stdout is not None
            try:
                proc.stdin.write(json.dumps(payload or {}, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

            for line in proc.stdout:
                if line.strip():
                    yield _decode_line(line)
        finally:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            reader.join(timeout=2)
            self.last_returncode = proc.returncode
            self.last_stderr = "".join(stderr_chunks).strip()
            self._release_process(proc)

    # -------------------------------------------------------------- download
    def download_url(
        self,
        url: str,
        dest: str | Path,
        *,
        sha1: str = "",
        threads: int = 8,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Run the kernel "download-url" command and return its done payload.

        Events are forwarded to *on_event* as they arrive (progress / status /
        done / error). An error event raises CoreError with the kernel text; a
        kernel that does not know the command raises CoreUnknownCommand so the
        caller can switch to the built-in downloader. *cancel* is polled between
        events, and kill() is called so the child stops downloading immediately.
        """
        target = Path(dest)
        payload: dict[str, Any] = {
            "url": str(url or ""),
            "dest": str(target),
            "sha1": str(sha1 or ""),
            "threads": int(threads or 1),
        }
        done_data: dict[str, Any] | None = None
        events = self.stream("download-url", payload)
        try:
            for event in events:
                if on_event is not None:
                    try:
                        on_event(event)
                    except Exception:
                        pass
                kind = event.get("type")
                if kind == "error":
                    message = str(event.get("message") or "").strip()
                    self.kill()
                    if _is_unknown_command(message):
                        raise CoreUnknownCommand(message, "download-url")
                    raise CoreError(message or "内核下载失败")
                if kind == "done":
                    data = event.get("data")
                    done_data = dict(data) if isinstance(data, dict) else {}
                if cancel is not None and cancel():
                    self.kill()
                    raise CoreError("下载已取消")
        finally:
            events.close()

        if cancel is not None and cancel():
            raise CoreError("下载已取消")
        if done_data is None:
            if self.last_returncode not in (0, None):
                stderr = self.last_stderr.strip()
                if _is_unknown_command(stderr):
                    raise CoreUnknownCommand(stderr, "download-url")
                raise CoreError(stderr or f"内核退出码 {self.last_returncode}")
            if target.is_file() and target.stat().st_size > 0:
                return {"path": str(target), "size": target.stat().st_size}
            raise CoreUnknownCommand("", "download-url")

        produced = Path(str(done_data.get("path") or "").strip() or str(target))
        if not produced.is_file() or produced.stat().st_size <= 0:
            # 内核回了 done 却没有真正写盘: 旧版内核不认识该命令时的表现。
            raise CoreUnknownCommand(f"完成事件未产生文件（{produced}）", "download-url")
        done_data.setdefault("path", str(produced))
        done_data.setdefault("size", produced.stat().st_size)
        return done_data

    # -------------------------------------------------------------- helpers
    def scan(self, mc_dir: str | Path) -> list[dict[str, Any]]:
        """Return the versions reported by ``mc_core.exe scan``."""
        events = self.call("scan", {"mc_dir": str(mc_dir)}, timeout=20)
        for event in events:
            if event.get("type") == "done":
                data = event.get("data")
                return data if isinstance(data, list) else []
            if event.get("type") == "error":
                raise CoreError(str(event.get("message", "内核返回错误")))
        return []
