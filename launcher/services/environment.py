"""Summarise the environment (versions, Java, memory, mods) for the AI prompt."""

from __future__ import annotations

import os
from pathlib import Path

from . import mods as mods_service
from .java_scan import probe_java


def _installed_versions(game_dir: Path) -> list[str]:
    folder = game_dir / "versions"
    if not folder.is_dir():
        return []
    try:
        return sorted(
            [p.name for p in folder.iterdir() if (p / f"{p.name}.json").is_file()],
            reverse=True,
        )[:12]
    except OSError:
        return []


def collect_environment(game_dir: str | Path) -> str:
    game = Path(game_dir)
    lines = [f"游戏目录：{game}"]

    try:
        from ..config import load_config

        config = load_config()
    except Exception:  # pragma: no cover - config must never break diagnosis
        config = None

    last_version = getattr(config, "last_version", "") if config else ""
    versions = _installed_versions(game)
    if versions:
        lines.append("已安装版本：" + "、".join(versions))
    if last_version:
        lines.append(f"最近启动版本：{last_version}")

    java_path = getattr(config, "java_path", "") if config else ""
    if java_path:
        target = Path(java_path)
        if not target.is_file():
            import shutil

            found = shutil.which(java_path)
            if found:
                target = Path(found)
        install = probe_java(target, "配置") if target.is_file() else None
        if install is None:
            lines.append(f"Java 路径：{java_path}（无法运行或不存在）")
        else:
            lines.append(
                f"Java 路径：{install.path}（{install.version or '未知版本'}，"
                f"{install.arch or '?'} 位，{install.vendor or '未知发行版'}）"
            )
    max_mem = getattr(config, "max_mem", "") if config else ""
    min_mem = getattr(config, "min_mem", "") if config else ""
    if max_mem or min_mem:
        lines.append(f"内存设置：Xmx={max_mem or 'auto'}，Xms={min_mem or 'auto'}")

    mod_files = mods_service.list_mod_files(game)
    if mod_files:
        enabled = [p for p in mod_files if not p.name.lower().endswith(".disabled")]
        names = []
        for path in enabled[:60]:
            meta = mods_service.read_mod_meta(path)
            label = str(meta.get("name") or meta.get("mod_id") or path.stem)
            version = str(meta.get("version") or "")
            names.append(f"{label} {version}".strip())
        lines.append(
            f"已启用 Mod（{len(enabled)}/{len(mod_files)}）：" + "、".join(names)
        )
        disabled = [p.name for p in mod_files if p.name.lower().endswith(".disabled")]
        if disabled:
            lines.append("已禁用 Mod：" + "、".join(disabled[:30]))

    if os.name == "nt":
        lines.append("操作系统：Windows")
    return "\n".join(lines)
