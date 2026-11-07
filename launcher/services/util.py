"""Small formatting helpers shared by the 百宝箱 services."""

from __future__ import annotations

import time


def human_size(size: int | float) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def human_speed(bytes_per_second: float) -> str:
    return f"{human_size(bytes_per_second)}/s"


def human_age(timestamp: float, now: float | None = None) -> str:
    if not timestamp:
        return "—"
    seconds = max(0, int((now or time.time()) - timestamp))
    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    days = hours // 24
    if days < 30:
        return f"{days} 天前"
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


def timestamp_slug() -> str:
    return time.strftime("%Y%m%d_%H%M%S")
