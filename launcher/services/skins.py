"""Local skin library, Steve/Alex detection and Mojang skin fetching."""

from __future__ import annotations

import base64
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QImage

from ..paths import skins_dir
from . import net

INDEX_NAME = "skins.json"
MODEL_LABELS = {"steve": "史蒂夫（Steve）", "alex": "艾丽丝（Alex）"}


@dataclass
class SkinEntry:
    path: Path
    name: str
    model: str
    source: str = "local"


def _index_path() -> Path:
    return skins_dir() / INDEX_NAME


def _load_index() -> dict:
    try:
        raw = json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_index(data: dict) -> None:
    skins_dir().mkdir(parents=True, exist_ok=True)
    _index_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def sanitize_name(name: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip(" .")
    return value[:60] or "skin"


def detect_model(image: QImage) -> str:
    """Heuristic Steve/Alex detection using the classic 64x64 slim layout."""
    if image.isNull() or image.width() != 64 or image.height() != 64:
        return "steve"
    regions = ((40, 16, 16, 16), (32, 48, 16, 16), (0, 16, 16, 16), (16, 48, 16, 16))
    for x0, y0, w, h in regions:
        for x in range(x0, x0 + w):
            for y in range(y0, y0 + h):
                if image.pixelColor(x, y).alpha() == 0:
                    return "alex"
    return "steve"


def load_image(path: str | Path) -> QImage:
    image = QImage(str(path))
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def make_entry(path: Path, index: dict) -> SkinEntry:
    image = load_image(path)
    model = str((index.get(path.name) or {}).get("model") or detect_model(image))
    if model not in MODEL_LABELS:
        model = detect_model(image)
    source = str((index.get(path.name) or {}).get("source") or "local")
    name = str((index.get(path.name) or {}).get("name") or path.stem)
    return SkinEntry(path=path, name=name, model=model, source=source)


def list_skins() -> list[SkinEntry]:
    folder = skins_dir()
    folder.mkdir(parents=True, exist_ok=True)
    index = _load_index()
    entries = []
    for path in sorted(folder.glob("*.png")):
        if path.name == INDEX_NAME:
            continue
        if not load_image(path).isNull():
            entries.append(make_entry(path, index))
    return entries


def import_skin(source: str | Path, name: str = "", model: str = "auto") -> SkinEntry:
    source = Path(source)
    image = load_image(source)
    if image.isNull():
        raise ValueError("无法读取皮肤图片，请选择 PNG 文件")
    if image.width() != 64 or image.height() not in (32, 64):
        raise ValueError("皮肤尺寸必须为 64×64 或 64×32 PNG")
    display = sanitize_name(name or source.stem)
    folder = skins_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{display}.png"
    counter = 2
    while target.exists():
        target = folder / f"{display}-{counter}.png"
        counter += 1
    shutil.copy2(source, target)
    detected = detect_model(image)
    chosen = detected if model not in ("steve", "alex") else model
    index = _load_index()
    index[target.name] = {"name": display, "model": chosen, "source": "local"}
    _save_index(index)
    return SkinEntry(path=target, name=display, model=chosen, source="local")


def set_model(entry: SkinEntry, model: str) -> SkinEntry:
    if model not in MODEL_LABELS:
        return entry
    index = _load_index()
    index[entry.path.name] = {
        "name": entry.name,
        "model": model,
        "source": entry.source,
    }
    _save_index(index)
    return SkinEntry(entry.path, entry.name, model, entry.source)


def delete_skin(entry: SkinEntry) -> None:
    try:
        entry.path.unlink()
    except FileNotFoundError:
        pass
    index = _load_index()
    index.pop(entry.path.name, None)
    _save_index(index)


def fetch_official_skin(account, name: str = "") -> SkinEntry:
    if account is None or not getattr(account, "uuid", ""):
        raise ValueError("请先完成 Microsoft 正版登录")
    uuid = str(account.uuid).replace("-", "")
    profile = net.get_json(
        f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
    )
    textures = ""
    for prop in profile.get("properties", []) if isinstance(profile, dict) else []:
        if isinstance(prop, dict) and prop.get("name") == "textures":
            try:
                textures = base64.b64decode(str(prop.get("value") or "")).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                textures = ""
            break
    if not textures:
        raise ValueError("该正版账号没有可用的皮肤数据")
    data = json.loads(textures)
    skin_url = (
        data.get("textures", {}).get("SKIN", {}).get("url", "")
        if isinstance(data, dict)
        else ""
    )
    if not skin_url:
        raise ValueError("该正版账号没有设置皮肤")
    folder = skins_dir()
    folder.mkdir(parents=True, exist_ok=True)
    display = sanitize_name(name or f"{account.name}-正版皮肤")
    target = folder / f"{display}.png"
    counter = 2
    while target.exists():
        target = folder / f"{display}-{counter}.png"
        counter += 1
    net.download(skin_url, target)
    image = load_image(target)
    model = detect_model(image)
    index = _load_index()
    index[target.name] = {"name": display, "model": model, "source": "Mojang"}
    _save_index(index)
    return SkinEntry(target, display, model, "Mojang")
