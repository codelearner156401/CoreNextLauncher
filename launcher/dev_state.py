"""Developer-mode / offline-login session state.

These two switches are deliberately kept **out of** ``launcher_config.json``:

* they live in their own file (``developer_state.json``);
* they are force-reset to ``False`` every time the launcher starts;
* they are force-reset again when the launcher closes.

So developer mode and offline login can never survive a restart, even if the
process was killed while they were enabled or the file was edited by hand.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import data_dir

STATE_PATH = data_dir() / "developer_state.json"


@dataclass
class DeveloperState:
    """Session-only toggles shown inside the hidden developer options."""

    developer_unlocked: bool = False
    offline: bool = False
    offline_ack: bool = False

    def normalized(self) -> "DeveloperState":
        self.developer_unlocked = bool(self.developer_unlocked)
        self.offline = bool(self.offline and self.offline_ack)
        self.offline_ack = bool(self.offline)
        if self.offline:
            self.developer_unlocked = True
        return self

    @property
    def offline_active(self) -> bool:
        return bool(self.offline and self.offline_ack and self.developer_unlocked)


def disabled() -> DeveloperState:
    return DeveloperState()


def read_state(path: Path = STATE_PATH) -> DeveloperState:
    """Read the state file without trusting it (normalised, not enabled)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DeveloperState()
    if not isinstance(raw, dict):
        return DeveloperState()
    state = DeveloperState(
        developer_unlocked=bool(raw.get("developer_unlocked")),
        offline=bool(raw.get("offline")),
        offline_ack=bool(raw.get("offline_ack")),
    )
    return state.normalized()


def save_state(state: DeveloperState, path: Path = STATE_PATH) -> None:
    payload = {"version": 1, **asdict(state.normalized())}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def reset_session_state(path: Path = STATE_PATH) -> DeveloperState:
    """Write an all-disabled state and return it.

    A read-only data directory must never prevent the launcher from starting,
    so write failures are swallowed here; the in-memory state is still reset.
    """
    state = DeveloperState()
    try:
        save_state(state, path)
    except OSError:
        pass
    return state


# Short alias for callers that only need the disabled state.
reset_state = reset_session_state
