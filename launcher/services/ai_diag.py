"""DeepSeek-powered log diagnosis.

The launcher collects the tail of ``logs/latest.log``, the newest crash report,
the installed mod list and the Java/memory settings, then asks DeepSeek for a
plain-language verdict.  The API key is stored with DPAPI in ``secrets.json``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..constants import DEFAULT_AI_ENDPOINT, DEFAULT_AI_MODEL
from ..secrets import load_secret, save_secret
from . import net
from .environment import collect_environment

ProgressCallback = Callable[[str], None]

SECRET_NAME = "deepseek_api_key"


def load_api_key() -> str:
    return load_secret(SECRET_NAME)


def save_api_key(value: str) -> None:
    save_secret(SECRET_NAME, value.strip())


def api_key_configured() -> bool:
    return bool(load_api_key())

SYSTEM_PROMPT = (
    "你是 Minecraft 启动器内置的故障诊断专家，服务对象是不懂技术的普通玩家。"
    "你会收到最新日志片段、崩溃报告、已安装 Mod 列表以及 Java/内存配置。"
    "请用简体中文回答，结构固定为：\n"
    "结论：一句话说明最可能的原因。\n"
    "依据：引用日志里的关键行（只引用真实存在的行）。\n"
    "修复步骤：按优先级编号，每条都要具体到“点哪里、填什么值”。\n"
    "可疑 Mod：如果有，直接点名；没有就说“未发现”。\n"
    "不要编造日志中不存在的信息；不确定时明确说明还需要哪些信息。"
)


@dataclass
class Diagnosis:
    text: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)


def collect_context(game_dir: str | Path, crash_text: str = "") -> str:
    """Build the user prompt from the real files in the game directory."""
    parts: list[str] = [collect_environment(game_dir)]
    game = Path(game_dir)

    crash = crash_text.strip()
    if not crash:
        from . import crash as crash_service

        analysis = crash_service.analyze(game)
        if analysis is not None:
            crash = analysis.raw
    if crash:
        parts.append("=== 崩溃报告（节选） ===\n" + crash[:9000])
    else:
        parts.append("=== 崩溃报告 ===\n（目录中没有 crash-reports）")

    log_path = game / "logs" / "latest.log"
    log_text = ""
    try:
        if log_path.is_file():
            data = log_path.read_bytes()
            log_text = data[-24000:].decode("utf-8", "replace")
    except OSError:
        log_text = ""
    parts.append("=== latest.log 末尾 ===\n" + (log_text or "（没有 latest.log）"))
    parts.append("请分析这次启动失败 / 崩溃，告诉我最可能的原因和具体修复步骤。")
    return "\n\n".join(parts)


def _post(api_key: str, model: str, messages: list[dict], timeout: float = 120.0) -> dict:
    if not api_key.strip():
        raise net.NetworkError("还没有填写 DeepSeek API Key，请先在「设置 → AI 诊断」中保存")
    payload = {
        "model": model or DEFAULT_AI_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1600,
        "stream": False,
    }
    try:
        data = net.post_json(
            DEFAULT_AI_ENDPOINT,
            payload,
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            timeout=timeout,
            retries=1,
        )
    except net.NetworkError as exc:
        message = str(exc)
        if "401" in message:
            raise net.NetworkError("DeepSeek API Key 无效或已过期，请重新填写") from exc
        if "402" in message:
            raise net.NetworkError("DeepSeek 账户余额不足，请充值后重试") from exc
        if "429" in message:
            raise net.NetworkError("请求太频繁，请稍等一分钟再试") from exc
        raise
    if not isinstance(data, dict) or not data.get("choices"):
        raise net.NetworkError("DeepSeek 返回了空结果，请稍后重试")
    choice = data["choices"][0] or {}
    message = choice.get("message") or {}
    text = str(message.get("content") or "").strip()
    if not text:
        raise net.NetworkError("DeepSeek 没有返回分析内容")
    return {"text": text, "model": str(data.get("model") or model), "usage": data.get("usage") or {}}


def diagnose(
    api_key: str,
    model: str,
    game_dir: str | Path,
    crash_text: str = "",
    progress: ProgressCallback | None = None,
) -> Diagnosis:
    if progress:
        progress("正在整理日志与 Mod 列表…")
    context = collect_context(game_dir, crash_text)
    if progress:
        progress("正在请求 DeepSeek 分析（通常 10~30 秒）…")
    result = _post(
        api_key,
        model,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
    )
    return Diagnosis(text=result["text"], model=result["model"], usage=result["usage"])


def test_key(api_key: str, model: str = DEFAULT_AI_MODEL) -> str:
    """Send a tiny request so the user gets immediate feedback."""
    result = _post(
        api_key,
        model,
        [{"role": "user", "content": "只回复“连接成功”四个字。"}],
        timeout=30,
    )
    return result["text"]
