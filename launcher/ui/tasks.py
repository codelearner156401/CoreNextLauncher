"""Background task helper used by the 百宝箱 and settings pages."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

_ACTIVE: set["Task"] = set()


class Task(QThread):
    """Run a blocking function with progress callbacks off the GUI thread.

    ``function(progress, is_cancelled)`` receives a ``progress(message, done,
    total)`` callback and a cancellation predicate matching the service layer.
    """

    progressed = Signal(str, int, int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, function: Callable[[Callable, Callable], Any], parent=None) -> None:
        super().__init__(parent)
        self._function = function
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            result = self._function(self.progressed.emit, self._cancel.is_set)
        except Exception as exc:  # surfaced through the caller's status label
            if not self._cancel.is_set():
                self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        if not self._cancel.is_set():
            self.succeeded.emit(result)


def start_task(
    owner: QWidget,
    function: Callable[[Callable, Callable], Any],
    *,
    on_done: Callable[[Any], None] | None = None,
    on_failed: Callable[[str], None] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> Task:
    """Start *function* in a ``Task`` kept alive by *owner* until it ends."""
    task = Task(function, owner)
    if on_progress is not None:
        task.progressed.connect(on_progress)
    if on_done is not None:
        task.succeeded.connect(on_done)
    if on_failed is not None:
        task.failed.connect(on_failed)

    tasks = getattr(owner, "_tasks", None)
    if tasks is None:
        tasks = set()
        setattr(owner, "_tasks", tasks)
    tasks.add(task)
    _ACTIVE.add(task)

    def cleanup() -> None:
        tasks.discard(task)
        _ACTIVE.discard(task)
        task.deleteLater()

    task.finished.connect(cleanup)
    task.start()
    return task


def cancel_tasks(owner: QWidget, timeout: float = 2.0) -> None:
    """Cancel this owner's tasks and wait briefly so Qt can destroy them."""
    tasks = list(getattr(owner, "_tasks", set()))
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    deadline = time.monotonic() + timeout
    for task in tasks:
        remaining = max(0, int((deadline - time.monotonic()) * 1000))
        try:
            task.wait(remaining)
        except RuntimeError:
            pass


def shutdown_tasks(timeout: float = 6.0) -> None:
    """Stop every background task before the Qt application exits.

    A QThread that is still running when its QObject is destroyed aborts the
    process, so this is best-effort: cancel, wait for the shared deadline, and
    only terminate a thread that ignored cancellation.
    """
    tasks = list(_ACTIVE)
    for task in tasks:
        task.cancel()
    deadline = time.monotonic() + timeout
    for task in tasks:
        remaining = max(0, int((deadline - time.monotonic()) * 1000))
        try:
            if task.wait(remaining):
                continue
            if task.isRunning():
                task.terminate()
                task.wait(500)
        except RuntimeError:
            continue
