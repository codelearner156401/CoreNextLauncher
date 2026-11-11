"""Browse the official Mojang version manifest for the download page."""

from __future__ import annotations

from dataclasses import dataclass

from . import net

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


@dataclass(frozen=True)
class VersionEntry:
    id: str
    type: str
    release_time: str
    url: str
    recommended: bool = False


def fetch_versions() -> list[VersionEntry]:
    payload = net.get_json(MANIFEST_URL)
    versions = payload.get("versions") if isinstance(payload, dict) else None
    if not isinstance(versions, list):
        raise RuntimeError("Mojang 版本清单格式异常")
    latest = payload.get("latest") if isinstance(payload, dict) else {}
    recommended_ids = set()
    if isinstance(latest, dict):
        recommended_ids = {
            str(latest.get("release") or ""),
            str(latest.get("snapshot") or ""),
        }
    entries: list[VersionEntry] = []
    for item in versions:
        if not isinstance(item, dict):
            continue
        version_id = str(item.get("id") or "").strip()
        if not version_id:
            continue
        entries.append(
            VersionEntry(
                id=version_id,
                type=str(item.get("type") or "release"),
                release_time=str(item.get("releaseTime") or ""),
                url=str(item.get("url") or ""),
                recommended=version_id in recommended_ids,
            )
        )
    return entries
