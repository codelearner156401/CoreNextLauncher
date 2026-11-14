"""Microsoft device-code login, Xbox Live / XSTS and Minecraft Services flow.

All requests go to Microsoft / Mojang official endpoints. No password ever
reaches this application: the user enters credentials on microsoft.com.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from .account import Account
from .constants import (
    DEFAULT_MS_CLIENT_ID,
    MC_ENTITLEMENTS_URL,
    MC_LOGIN_URL,
    MC_PROFILE_URL,
    MS_DEVICE_CODE_URL,
    MS_SCOPE,
    MS_TOKEN_URL,
    XBL_AUTH_URL,
    XSTS_AUTH_URL,
)


class AuthError(RuntimeError):
    pass


class AuthNetworkError(AuthError):
    """Raised when Microsoft services cannot be reached."""


def _request_json(
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError as exc:
                # A proxy or captive portal can answer 200 with HTML.
                raise AuthError(
                    f"服务器返回了非 JSON 响应（HTTP {response.status}）：{body[:200]}"
                ) from exc
            if not isinstance(payload, dict):
                # Callers read the payload with .get(); an array or bare scalar
                # here used to escape as AttributeError.
                raise AuthError(
                    f"服务器返回了非对象 JSON（HTTP {response.status}）：{str(payload)[:200]}"
                )
            return response.status, payload
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"error": f"HTTP {exc.code}", "error_description": body[:500]}
        return exc.code, payload
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise AuthNetworkError(f"连接 Microsoft 服务失败：{reason}") from exc


def _post_form(url: str, fields: dict[str, str]) -> tuple[int, dict]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    return _request_json(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request_json(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json", **(headers or {})},
        method="POST",
    )


def _get_json(url: str, access_token: str) -> tuple[int, dict]:
    return _request_json(url, headers={"Authorization": f"Bearer {access_token}"}, method="GET")


def error_text(payload: dict, prefix: str) -> str:
    description = (
        payload.get("error_description")
        or payload.get("error")
        or payload.get("Message")
        or payload.get("message")
        or "未知错误"
    )
    return f"{prefix}：{description}"


def request_device_code(client_id: str = DEFAULT_MS_CLIENT_ID) -> dict:
    status, payload = _post_form(
        MS_DEVICE_CODE_URL, {"client_id": client_id, "scope": MS_SCOPE}
    )
    if status != 200 or "device_code" not in payload or "user_code" not in payload:
        raise AuthError(error_text(payload, "申请 Microsoft 设备码失败"))
    return payload


def poll_device_token(
    client_id: str, device_code: str
) -> tuple[int, dict]:
    return _post_form(
        MS_TOKEN_URL,
        {
            "client_id": client_id,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        },
    )


def xbox_authenticate(ms_access_token: str) -> tuple[str, str]:
    status, payload = _post_json(
        XBL_AUTH_URL,
        {
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={ms_access_token}",
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        },
        {"x-xbl-contract-version": "1"},
    )
    if status != 200 or "Token" not in payload:
        raise AuthError(error_text(payload, "Xbox Live 认证失败"))
    try:
        user_hash = payload["DisplayClaims"]["xui"][0]["uhs"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AuthError("Xbox Live 返回格式异常") from exc
    return payload["Token"], str(user_hash)


XSTS_MESSAGES = {
    "2148916233": "该 Microsoft 账号还没有 Xbox 档案，请先到 xbox.com 创建档案",
    "2148916235": "该账号所在的地区不支持 Xbox Live",
    "2148916238": "未成年账号需要先加入 Microsoft 家庭组",
    "2148916236": "该账号需要完成年龄验证",
    "2148916237": "该账号被 Xbox Live 限制登录",
}


def xsts_authorize(xbl_token: str) -> tuple[str, str, str]:
    status, payload = _post_json(
        XSTS_AUTH_URL,
        {
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        },
        {"x-xbl-contract-version": "1"},
    )
    if status != 200 or "Token" not in payload:
        code = str(payload.get("XErr", ""))
        if code in XSTS_MESSAGES:
            raise AuthError(XSTS_MESSAGES[code])
        raise AuthError(error_text(payload, "Xbox 安全令牌验证失败"))
    try:
        claims = payload["DisplayClaims"]["xui"][0]
        user_hash = claims["uhs"]
        xuid = claims.get("xid", "")
    except (KeyError, IndexError, TypeError) as exc:
        raise AuthError("Xbox 安全令牌返回格式异常") from exc
    return payload["Token"], str(user_hash), str(xuid)


def minecraft_login(xsts_token: str, user_hash: str) -> tuple[str, float]:
    status, payload = _post_json(
        MC_LOGIN_URL, {"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"}
    )
    if status not in (200, 201) or "access_token" not in payload:
        raise AuthError(error_text(payload, "Minecraft Services 登录失败"))
    return payload["access_token"], float(payload.get("expires_in", 86400))


def has_entitlement(mc_access_token: str) -> bool:
    status, payload = _get_json(MC_ENTITLEMENTS_URL, mc_access_token)
    if status != 200:
        # The profile endpoint is authoritative enough; do not fail the login
        # just because the optional entitlement endpoint is unavailable.
        return True
    items = payload.get("items")
    return not (isinstance(items, list) and len(items) == 0)


def minecraft_profile(mc_access_token: str) -> dict:
    status, payload = _get_json(MC_PROFILE_URL, mc_access_token)
    if status == 404:
        raise AuthError("该账号没有 Minecraft Java Edition 档案，无法使用正版登录")
    if status != 200 or not payload.get("id") or not payload.get("name"):
        raise AuthError(error_text(payload, "读取 Minecraft 玩家档案失败"))
    return payload


def _dashed_uuid(value: str) -> str:
    compact = value.replace("-", "").lower()
    if len(compact) != 32:
        return value
    return f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:32]}"


def complete_login(
    token_response: dict,
    client_id: str = DEFAULT_MS_CLIENT_ID,
    status: Callable[[str], None] | None = None,
    refresh_fallback: str = "",
) -> Account:
    def report(text: str) -> None:
        if status:
            status(text)

    ms_token = token_response.get("access_token")
    if not ms_token:
        raise AuthError(error_text(token_response, "Microsoft 返回了无效的令牌"))
    refresh_token = token_response.get("refresh_token") or refresh_fallback

    report("正在验证 Xbox Live…")
    xbl_token, user_hash = xbox_authenticate(ms_token)

    report("正在验证 Xbox 安全令牌…")
    xsts_token, user_hash, xuid = xsts_authorize(xbl_token)

    report("正在登录 Minecraft Services…")
    mc_token, expires_in = minecraft_login(xsts_token, user_hash)

    if not has_entitlement(mc_token):
        raise AuthError("该 Microsoft 账号未拥有 Minecraft Java Edition，无法使用正版登录")

    report("正在读取玩家档案…")
    profile = minecraft_profile(mc_token)
    return Account(
        name=str(profile["name"]),
        uuid=_dashed_uuid(str(profile["id"])),
        access_token=mc_token,
        expires_at=time.time() + expires_in,
        xuid=xuid,
        refresh_token=refresh_token,
        client_id=client_id,
        user_type="msa",
        cached_at=time.time(),
    )


def refresh_account(account: Account, status: Callable[[str], None] | None = None) -> Account:
    if not account.refresh_token:
        raise AuthError("登录已过期且没有刷新令牌，请重新登录")
    client_id = account.client_id or DEFAULT_MS_CLIENT_ID
    status_, payload = _post_form(
        MS_TOKEN_URL,
        {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": account.refresh_token,
            "scope": MS_SCOPE,
        },
    )
    if status_ != 200 or "access_token" not in payload:
        raise AuthError(error_text(payload, "刷新 Microsoft 登录失败"))
    return complete_login(
        payload, client_id, status, refresh_fallback=account.refresh_token
    )
