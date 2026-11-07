"""Qt worker threads that keep the C++ core off the GUI thread."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from .core_client import CoreClient


class ScanWorker(QThread):
    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, client: CoreClient, game_dir: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.game_dir = game_dir

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            self.succeeded.emit(self.client.scan(self.game_dir))
        except Exception as exc:  # surfaced in the log page
            self.failed.emit(str(exc))


class LaunchWorker(QThread):
    event = Signal(object)
    finished_with = Signal(int)
    failed = Signal(str)

    def __init__(self, client: CoreClient, payload: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.payload = payload

    def run(self) -> None:
        try:
            for event in self.client.stream("launch", self.payload):
                self.event.emit(event)
            stderr = (self.client.last_stderr or "").strip()
            if stderr:
                self.event.emit(
                    {
                        "type": "log",
                        "text": "游戏 / Java stderr：\n" + stderr[-12000:],
                    }
                )
            self.finished_with.emit(int(self.client.last_returncode or 0))
        except Exception as exc:
            message = self.client.last_stderr or str(exc)
            self.failed.emit(message)

    def stop(self) -> None:
        self.client.kill()


class InstallWorker(QThread):
    event = Signal(object)
    finished_with = Signal(int)
    failed = Signal(str)

    def __init__(self, client: CoreClient, payload: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.payload = payload

    def run(self) -> None:
        try:
            for event in self.client.stream("install", self.payload):
                self.event.emit(event)
            self.finished_with.emit(int(self.client.last_returncode or 0))
        except Exception as exc:
            message = self.client.last_stderr or str(exc)
            self.failed.emit(message)

    def stop(self) -> None:
        self.client.kill()
