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
from typing import Any, Iterator

from .paths import app_root, core_path

PROJECT_ROOT = app_root()
DEFAULT_EXE = PROJECT_ROOT / "build" / "mc_core.exe"


class CoreError(RuntimeError):
    """Raised when the core cannot be started or reports a fatal failure."""


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
        self._proc: subprocess.Popen[str] | None = None
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

    def kill(self) -> None:
        """Terminate the currently running core process, if any."""
        with self._lock:
            proc = self._proc
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
        with self._lock:
            self._proc = proc
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
            with self._lock:
                self._proc = None

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
        with self._lock:
            self._proc = proc
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
            with self._lock:
                self._proc = None

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
