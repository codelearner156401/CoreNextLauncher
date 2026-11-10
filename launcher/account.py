"""Microsoft / Minecraft account model and DPAPI-protected persistence."""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .dpapi import protect, unprotect
from .paths import data_dir

ACCOUNT_PATH = data_dir() / "account.json"


@dataclass
class Account:
    name: str
    uuid: str
    access_token: str
    expires_at: float
    xuid: str = ""
    refresh_token: str = ""
    client_id: str = ""
    user_type: str = "msa"
    cached_at: float = 0.0

    def expired(self, margin: float = 600.0) -> bool:
        return time.time() >= float(self.expires_at) - margin

    def within_cache(self, days: int = 30) -> bool:
        """Whether this login may be reused offline within the cache window."""
        cached = float(self.cached_at or 0.0)
        if cached <= 0:
            return False
        return (time.time() - cached) <= max(1, days) * 86400

    def launch_account(self) -> dict:
        return {
            "name": self.name,
            "uuid": self.uuid,
            "access_token": self.access_token,
            "user_type": self.user_type or "msa",
            "xuid": self.xuid or "0",
            "clientid": self.client_id,
        }


def load_account(path: Path = ACCOUNT_PATH) -> Account | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        if raw.get("dpapi"):
            plain = unprotect(base64.b64decode(raw["data"])).decode("utf-8")
            raw = json.loads(plain)
        known = {field.name for field in fields(Account)}
        values = {key: value for key, value in raw.items() if key in known}
        account = Account(**values)
        if account.cached_at <= 0 and account.expires_at:
            # Older files predate cached_at; approximate the original login time.
            account.cached_at = max(0.0, float(account.expires_at) - 86400.0)
        return account
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def save_account(account: Account, path: Path = ACCOUNT_PATH) -> None:
    payload = json.dumps(asdict(account), ensure_ascii=False).encode("utf-8")
    protected = protect(payload)
    wrapper = {
        "version": 1,
        "dpapi": True,
        "data": base64.b64encode(protected).decode("ascii"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def clear_account(path: Path = ACCOUNT_PATH) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
