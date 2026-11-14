"""Microsoft / Minecraft account model and DPAPI-protected persistence."""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass
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


_LOAD_ERROR = ""


def last_load_error() -> str:
    """Reason the last load_account() returned None (empty when it succeeded)."""
    return _LOAD_ERROR


def _text_value(raw: dict, key: str) -> str:
    value = raw.get(key)
    return "" if value is None else str(value)


def _number_value(raw: dict, key: str) -> float | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_account(path: Path = ACCOUNT_PATH) -> Account | None:
    global _LOAD_ERROR
    _LOAD_ERROR = ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _LOAD_ERROR = "未找到账号文件"
        return None
    except (OSError, json.JSONDecodeError) as exc:
        _LOAD_ERROR = f"读取账号文件失败：{exc}"
        return None
    try:
        if not isinstance(raw, dict):
            raise ValueError(f"顶层不是对象（{type(raw).__name__}）")
        if raw.get("dpapi"):
            plain = unprotect(base64.b64decode(raw["data"])).decode("utf-8")
            raw = json.loads(plain)
            if not isinstance(raw, dict):
                raise ValueError("解密后的数据不是对象")
        account = Account(
            name=_text_value(raw, "name"),
            uuid=_text_value(raw, "uuid"),
            access_token=_text_value(raw, "access_token"),
            expires_at=0.0,
            xuid=_text_value(raw, "xuid"),
            refresh_token=_text_value(raw, "refresh_token"),
            client_id=_text_value(raw, "client_id"),
            user_type=_text_value(raw, "user_type") or "msa",
        )
        expires_at = _number_value(raw, "expires_at")
        if not account.access_token or not account.uuid or expires_at is None:
            raise ValueError("缺少必要的登录字段")
        account.expires_at = expires_at
        account.cached_at = _number_value(raw, "cached_at") or 0.0
        if account.cached_at <= 0 and account.expires_at:
            # Older files predate cached_at; approximate the original login time.
            account.cached_at = max(0.0, float(account.expires_at) - 86400.0)
        return account
    except (OSError, ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        _LOAD_ERROR = f"账号信息不可用：{exc}"
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
