"""JVM presets and memory recommendation for the visual JVM editor."""

from __future__ import annotations

from dataclasses import dataclass

PRESET_ORDER = ("auto", "g1", "zgc", "performance", "custom")

PRESET_LABELS = {
    "auto": "自动（由内核决定，推荐）",
    "g1": "均衡 · G1GC",
    "zgc": "低延迟 · ZGC（Java 17+）",
    "performance": "性能 · G1GC + 预触碰",
    "custom": "自定义",
}

G1_ARGS = (
    "-XX:+UseG1GC",
    "-XX:MaxGCPauseMillis=200",
    "-XX:+ParallelRefProcEnabled",
)
ZGC_ARGS = ("-XX:+UseZGC",)
PERFORMANCE_ARGS = G1_ARGS + ("-XX:+AlwaysPreTouch",)

MAX_ARGS = 64
MAX_ARG_LENGTH = 256


@dataclass(frozen=True)
class MemoryAdvice:
    min_mb: int
    max_mb: int
    reason: str

    @property
    def max_text(self) -> str:
        return f"{self.max_mb}M"

    @property
    def min_text(self) -> str:
        return f"{self.min_mb}M"


def preset_args(preset: str) -> list[str]:
    """Effective JVM arguments for a preset (auto = let the core decide)."""
    preset = preset if preset in PRESET_LABELS else "auto"
    if preset == "g1":
        return list(G1_ARGS)
    if preset == "zgc":
        return list(ZGC_ARGS)
    if preset == "performance":
        return list(PERFORMANCE_ARGS)
    return []


def preset_for_args(args: list[str]) -> str:
    """Best-effort preset detection for an existing argument list."""
    normalized = {item.strip() for item in args if item and item.strip()}
    if not normalized:
        return "auto"
    if "-XX:+UseZGC" in normalized:
        return "zgc"
    if "-XX:+AlwaysPreTouch" in normalized and "-XX:+UseG1GC" in normalized:
        return "performance"
    if "-XX:+UseG1GC" in normalized:
        return "g1"
    return "custom"


def recommend_memory(total_mb: int) -> MemoryAdvice:
    """Pick a safe Xmx/Xms pair from the installed physical memory."""
    total = max(2048, int(total_mb))
    if total <= 8192:
        max_mb = min(4096, max(2048, total // 2))
    elif total <= 16384:
        max_mb = 6144
    elif total <= 32768:
        max_mb = 8192
    else:
        max_mb = min(16384, (total // 2 // 512) * 512)
    max_mb = max(2048, max_mb)
    min_mb = max(1024, min(max_mb // 4, 4096))
    reason = (
        f"检测到物理内存 {total / 1024:.1f} GB："
        f"建议 Xmx {max_mb} MB / Xms {min_mb} MB，为系统、显卡与其他程序预留空间。"
    )
    return MemoryAdvice(min_mb=min_mb, max_mb=max_mb, reason=reason)


def sanitize_args(args) -> list[str]:
    """Trim, de-duplicate and bound a user supplied JVM argument list."""
    if not isinstance(args, (list, tuple)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in args:
        if not isinstance(raw, str):
            continue
        if any(ch in raw for ch in ("\r", "\n", "\x00")):
            continue
        text = raw.strip()
        if not text or len(text) > MAX_ARG_LENGTH:
            continue
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= MAX_ARGS:
            break
    return result


def arguments_from_flags(
    g1: bool,
    zgc: bool,
    pretouch: bool,
    parallel_ref: bool,
    utf8: bool,
    extra: list[str] | None = None,
) -> list[str]:
    """Build the effective list from the visual checkboxes plus custom args."""
    args: list[str] = []
    if zgc and not g1:
        args.extend(ZGC_ARGS)
    elif g1:
        args.extend(G1_ARGS)
    if pretouch:
        args.append("-XX:+AlwaysPreTouch")
    if parallel_ref and "-XX:+ParallelRefProcEnabled" not in args:
        args.append("-XX:+ParallelRefProcEnabled")
    if utf8:
        args.append("-Dfile.encoding=UTF-8")
    if extra:
        for item in sanitize_args(extra):
            if item not in args:
                args.append(item)
    return sanitize_args(args)
