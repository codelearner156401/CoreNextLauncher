"""List Minecraft screenshots with their dimensions (no Pillow needed).

Minecraft writes PNG screenshots; the size is read from the IHDR chunk so the
gallery can show real dimensions without decoding every image.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

SUFFIXES = {".png", ".jpg", ".jpeg"}


@dataclass(frozen=True)
class ScreenshotInfo:
    path: Path
    size: int = 0
    mtime: float = 0.0
    width: int = 0
    height: int = 0

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}" if self.width and self.height else "—"


def folder(game_dir: str | Path) -> Path:
    return Path(game_dir) / "screenshots"


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with open(path, "rb") as handle:
            header = handle.read(32)
    except OSError:
        return 0, 0
    if header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) >= 24:
        width, height = struct.unpack(">II", header[16:24])
        return int(width), int(height)
    return 0, 0


def list_screenshots(game_dir: str | Path) -> list[ScreenshotInfo]:
    root = folder(game_dir)
    if not root.is_dir():
        return []
    result: list[ScreenshotInfo] = []
    try:
        for path in root.iterdir():
            if not path.is_file() or path.suffix.lower() not in SUFFIXES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            width, height = _image_size(path)
            result.append(
                ScreenshotInfo(
                    path=path,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    width=width,
                    height=height,
                )
            )
    except OSError:
        return []
    result.sort(key=lambda item: item.mtime, reverse=True)
    return result


def delete_screenshot(path: str | Path) -> None:
    target = Path(path)
    if target.parent.name.lower() != "screenshots":
        raise ValueError("只能删除 screenshots 目录中的文件")
    target.unlink()
