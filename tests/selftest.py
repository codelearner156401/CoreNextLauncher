"""Self-test for the CoreLauncher service layer and the Qt UI.

Run from the project root:

    python tests/selftest.py

No network access is required: HTTP calls are replaced with local fixtures and
the Minecraft server ping is answered by a tiny in-process server.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from launcher import APP_VERSION
from launcher.config import CONFIG_PATH, AppConfig, load_config, save_config
from launcher.services import backup, crash, modpack, mods, screenshots, servers, updater

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def expect_raises(name: str, function, exception: type) -> None:
    try:
        function()
    except exception:
        print(f"  PASS  {name}")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  {name} (unexpected {type(exc).__name__}: {exc})")
        FAILURES.append(name)
        return
    print(f"  FAIL  {name} (no exception)")
    FAILURES.append(name)


# --------------------------------------------------------------------- fixtures
def make_mod_jar(path: Path, mod_id: str, name: str, version: str, depends: dict | None = None) -> None:
    payload = {
        "schemaVersion": 1,
        "id": mod_id,
        "name": name,
        "version": version,
        "description": f"{name} test fixture",
        "depends": depends or {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps(payload))
        archive.writestr("assets/readme.txt", "fixture")


def make_png(path: Path, width: int = 320, height: int = 180) -> None:
    import zlib

    raw = b""
    for _ in range(height):
        raw += b"\x00" + bytes([40, 80, 160]) * width

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def build_game_dir(root: Path) -> Path:
    game = root / "game"
    (game / "versions" / "1.20.1").mkdir(parents=True, exist_ok=True)
    (game / "versions" / "1.20.1" / "1.20.1.json").write_text(
        json.dumps({"id": "1.20.1", "type": "release", "mainClass": "net.minecraft.client.main.Main"}),
        encoding="utf-8",
    )
    make_mod_jar(game / "mods" / "sodium.jar", "sodium", "Sodium", "0.5.13", {"fabric-api": "*"})
    make_mod_jar(game / "mods" / "iris.jar.disabled", "iris", "Iris", "1.7.0")
    (game / "saves" / "world").mkdir(parents=True, exist_ok=True)
    (game / "saves" / "world" / "level.dat").write_bytes(b"level")
    make_png(game / "screenshots" / "2026-10-30_21.00.00.png")
    (game / "crash-reports").mkdir(parents=True, exist_ok=True)
    (game / "crash-reports" / "crash-2026-10-30_21.00.00-client.txt").write_text(
        "---- Minecraft Crash Report ----\n"
        "Description: Rendering screen\n"
        "Minecraft Version: 1.20.1\n"
        "Java Version: 17.0.8, Microsoft\n"
        "java.lang.OutOfMemoryError: Java heap space\n"
        "JVM Flags: -Xmx2G -Xms1G\n"
        "Fabric Mods: sodium 0.5.13, iris 1.7.0\n",
        encoding="utf-8",
    )
    (game / "logs").mkdir(parents=True, exist_ok=True)
    (game / "logs" / "latest.log").write_text("[21:00:00] [main/INFO]: test\n", encoding="utf-8")
    return game


# ----------------------------------------------------------------------- tests
def test_config(root: Path) -> None:
    print("config")
    config = AppConfig(update_repo="https://github.com/example/CoreLauncher/", backup_keep=99)
    config.normalized()
    check("repo normalised", config.update_repo == "example/CoreLauncher", config.update_repo)
    check("backup_keep clamped", config.backup_keep == 30, str(config.backup_keep))
    path = root / "config.json"
    save_config(config, path)
    loaded = load_config(path)
    check("config roundtrip", loaded.update_repo == "example/CoreLauncher")


def test_crash(game: Path) -> None:
    print("crash analysis")
    analysis = crash.analyze(game)
    check("crash report found", analysis is not None)
    if analysis is None:
        return
    titles = [finding.title for finding in analysis.findings]
    check("OOM detected", "内存不够用" in titles, str(titles))
    check("game version parsed", analysis.game_version == "1.20.1", analysis.game_version)
    check("java version parsed", "17.0.8" in analysis.java_version, analysis.java_version)
    check("advice mentions memory", "内存" in crash.plain_text(analysis))
    since = crash.analyze_since(game, time.time() - 5)
    check("analyze_since recent", since is not None)
    old = crash.analyze_since(game, time.time() + 60)
    check("analyze_since future", old is None)


def test_mods(game: Path) -> None:
    print("mod management")
    loaded, folder = mods.load_mods(game)
    check("mods loaded", len(loaded) == 2, str(len(loaded)))
    check("disabled detected", any(not item.enabled for item in loaded))
    sodium = next(item for item in loaded if item.mod_id == "sodium")
    check("metadata read", sodium.name == "Sodium" and sodium.version == "0.5.13")
    report = mods.scan_conflicts(loaded)
    check("missing dependency detected", any(dep == "fabric-api" for _s, dep, _j in report.missing))
    new_path = mods.set_mod_enabled(sodium.path, False)
    check("disable renames jar", new_path.name.endswith(".jar.disabled"), new_path.name)
    check("file exists after rename", new_path.is_file())
    again = mods.set_mod_enabled(new_path, True)
    check("enable restores name", again.name == "sodium.jar", again.name)


def test_backup(game: Path) -> None:
    print("save backup")
    first = backup.backup_saves(game, keep=2)
    check("first backup created", len(first.created) == 1, str(first.created))
    second = backup.backup_saves(game, keep=2)
    check("unchanged world skipped", not second.created and second.skipped)
    level = game / "saves" / "world" / "level.dat"
    time.sleep(0.02)
    level.write_bytes(b"level changed")
    os.utime(level, (time.time() + 1, time.time() + 1))
    third = backup.backup_saves(game, keep=2)
    check("changed world backed up", len(third.created) == 1)
    archives = list(backup.backups_dir(game).glob("world_*.zip"))
    check("retention applied", len(archives) <= 2, str(archives))
    with zipfile.ZipFile(third.created[0]) as archive:
        check("zip contains world", "world/level.dat" in archive.namelist())


def test_screenshots(game: Path) -> None:
    print("screenshots")
    items = screenshots.list_screenshots(game)
    check("screenshot listed", len(items) == 1, str(len(items)))
    if items:
        check("dimensions parsed", (items[0].width, items[0].height) == (320, 180), items[0].resolution)
    target = game / "screenshots" / "2026-10-30_21.00.00.png"
    screenshots.delete_screenshot(target)
    check("screenshot deleted", not target.exists())
    expect_raises("refuse delete outside screenshots", lambda: screenshots.delete_screenshot(game / "level.dat"), ValueError)
    make_png(target)


def make_mrpack(path: Path) -> None:
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": "1.0.0",
        "name": "Test Pack",
        "summary": "Self-test pack",
        "files": [
            {
                "path": "mods/example.jar",
                "hashes": {"sha1": "0" * 40},
                "env": {"client": "required", "server": "optional"},
                "downloads": ["https://example.invalid/example.jar"],
                "fileSize": 10,
            },
            {
                "path": "mods/server-only.jar",
                "hashes": {"sha1": "1" * 40},
                "env": {"client": "unsupported", "server": "required"},
                "downloads": ["https://example.invalid/server-only.jar"],
                "fileSize": 10,
            },
        ],
        "dependencies": {"minecraft": "1.20.1", "fabric-loader": "0.16.10"},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("modrinth.index.json", json.dumps(index))
        archive.writestr("overrides/config/example.toml", "enabled = true\n")
        archive.writestr("client-overrides/options.txt", "guiScale:2\n")


def test_modpack(root: Path) -> None:
    print("mrpack import")
    archive = root / "pack.mrpack"
    make_mrpack(archive)
    info = modpack.read_pack(archive)
    check("pack name", info.name == "Test Pack", info.name)
    check("mc version", info.mc_version == "1.20.1", info.mc_version)
    check("loader", info.loader == "fabric" and info.loader_version == "0.16.10")
    check("client files only", len(info.files) == 1, str(len(info.files)))
    check("override count", info.override_count == 2, str(info.override_count))
    game = root / "import-game"
    game.mkdir()
    written = modpack.extract_overrides(archive, game)
    check("overrides extracted", written == 2, str(written))
    check("nested file", (game / "config" / "example.toml").is_file())
    (game / "config" / "example.toml").unlink()
    (game / "options.txt").write_text("guiScale:4\n", encoding="utf-8")
    written_again = modpack.extract_overrides(archive, game, overwrite=False)
    check("only missing override written", written_again == 1, str(written_again))
    check("existing override preserved", (game / "options.txt").read_text() == "guiScale:4\n")
    written_force = modpack.extract_overrides(archive, game, overwrite=True)
    check("overwrite honoured", written_force == 2, str(written_force))
    bad = root / "bad.mrpack"
    with zipfile.ZipFile(bad, "w") as archive_zip:
        archive_zip.writestr("readme.txt", "not a pack")
    expect_raises("reject non-mrpack", lambda: modpack.read_pack(bad), modpack.ModpackError)


class FakeMinecraftServer(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()

    @staticmethod
    def _varint(value: int) -> bytes:
        out = bytearray()
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                out.append(byte | 0x80)
            else:
                out.append(byte)
                return bytes(out)

    @staticmethod
    def _read_varint(conn: socket.socket) -> int:
        result = 0
        for shift in range(0, 35, 7):
            byte = conn.recv(1)[0]
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
        return result

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                return
            try:
                conn.settimeout(3)
                self._read_varint(conn)  # packet length
                self._read_varint(conn)  # packet id (handshake)
                self._read_varint(conn)  # protocol
                length = self._read_varint(conn)
                conn.recv(length)  # host
                conn.recv(2)  # port
                self._read_varint(conn)  # next state
                self._read_varint(conn)  # status packet length
                self._read_varint(conn)  # status packet id
                status = {
                    "version": {"name": "1.20.1", "protocol": 763},
                    "players": {"max": 20, "online": 3, "sample": [{"name": "Steve"}]},
                    "description": {"text": "Hello ", "extra": [{"text": "World"}]},
                    "favicon": "data:image/png;base64,AAAA",
                }
                payload = json.dumps(status).encode()
                body = b"\x00" + self._varint(len(payload)) + payload
                conn.sendall(self._varint(len(body)) + body)
            except (OSError, IndexError):
                pass
            finally:
                conn.close()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


def test_servers() -> None:
    print("server ping")
    host, port = servers.split_address("example.com:25566")
    check("address split", (host, port) == ("example.com", 25566))
    host, port = servers.split_address("[::1]:25565")
    check("ipv6 split", (host, port) == ("::1", 25565))
    fake = FakeMinecraftServer()
    fake.start()
    try:
        status = servers.ping("127.0.0.1", fake.port, timeout=3)
        check("server online", status.online, status.error)
        check("motd flattened", status.motd == "Hello World", status.motd)
        check("players parsed", (status.players_online, status.players_max) == (3, 20))
        check("version parsed", status.version_name == "1.20.1")
        check("sample parsed", status.sample == ["Steve"])
        check("latency measured", status.latency_ms >= 0)
    finally:
        fake.stop()
    offline = servers.ping("127.0.0.1", 1, timeout=0.6)
    check("offline handled", not offline.online and offline.error)


def test_updater() -> None:
    print("updater")
    check("version parse", updater.parse_version("v1.6.0") == (1, 6, 0))
    check("newer detected", updater.is_newer("v1.10.0", "1.9.9"))
    check("same not newer", not updater.is_newer("v1.6.0", APP_VERSION))
    check("older not newer", not updater.is_newer("v1.0.0", APP_VERSION))
    asset = updater._pick_asset(
        [
            {"name": "CoreLauncher-arm64.exe", "browser_download_url": "https://x/arm64"},
            {"name": "CoreLauncher.exe", "browser_download_url": "https://x/x64", "size": 42},
            {"name": "CoreLauncher.exe.blockmap", "browser_download_url": "https://x/bm"},
        ]
    )
    check("asset prefers portable exe", asset is not None and asset.name == "CoreLauncher.exe")
    check("asset size kept", asset is not None and asset.size == 42)
    with tempfile.TemporaryDirectory() as temp:
        bat, script = updater.build_update_command(
            Path(temp) / "new.exe", Path(temp) / "CoreLauncher.exe", pid=4242
        )
        check("bat written", bat.is_file())
        check("bat waits for pid", "PID=4242" in script)
        check("bat restarts target", 'start "" "%TARGET%"' in script)
        bat.unlink()


def test_java_scan() -> None:
    print("java scan")
    from launcher.services import java_install

    expectations = {
        "1.8.9": 8,
        "1.12.2": 8,
        "1.16.5": 8,
        "1.17.1": 16,
        "1.20.1": 17,
        "1.20.4": 17,
        "1.20.6": 21,
        "1.21.1": 21,
        "1.21.4": 21,
        "26.2": 25,
    }
    for version, expected in expectations.items():
        check(
            f"java major for {version}",
            java_install.suggested_major(version) == expected,
            str(java_install.suggested_major(version)),
        )
    try:
        installs = __import__("launcher.services.java_scan", fromlist=["scan_java"]).scan_java(None)
    except Exception as exc:  # noqa: BLE001
        check("java scan runs", False, str(exc))
        return
    check("java scan returns list", isinstance(installs, list))
    check(
        "java entries well formed",
        all(item.path and item.major >= 0 for item in installs),
        str(installs[:2]),
    )


def test_ui(root: Path) -> None:
    print("ui construction")
    from PySide6.QtWidgets import QApplication

    from launcher import theme
    from launcher.core_client import CoreClient
    from launcher.main_window import MainWindow

    game = build_game_dir(root)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    # The UI test runs from the source tree, where the launcher stores its
    # config next to main.py.  Preserve any real config and put it back.
    original_config = CONFIG_PATH.read_bytes() if CONFIG_PATH.is_file() else None
    theme.apply_theme(app, "dark")
    check(
        "dark stylesheet includes toolbox rules",
        "QTableWidget#ModTable" in app.styleSheet()
        and "QFrame#DropZone" in app.styleSheet(),
    )
    config = AppConfig(game_dir=str(game), java_path="java")
    window = MainWindow(CoreClient(), config)
    window.show()
    for _ in range(25):
        app.processEvents()
        time.sleep(0.03)
    check("main tabs", window.tabs.count() == 4)
    window.toolbox_page.set_game_dir(str(game))
    window.tabs.setCurrentIndex(2)
    for index in range(window.toolbox_page.tabs.count()):
        window.toolbox_page.tabs.setCurrentIndex(index)
        for _ in range(12):
            app.processEvents()
            time.sleep(0.03)
    check("mods table rows", window.toolbox_page.mods_tab.table.rowCount() == 2)
    check("saves listed", window.toolbox_page.shortcuts_tab.save_list.count() == 1)
    check("screenshots loaded", len(window.toolbox_page.screenshots_tab.items) == 1)
    check(
        "diagnose finds report",
        "crash" in window.toolbox_page.diagnose_tab.report_info.text(),
        window.toolbox_page.diagnose_tab.report_info.text(),
    )
    window._on_theme_changed("light")
    for _ in range(6):
        app.processEvents()
        time.sleep(0.02)
    check("light stylesheet applied", "QTableWidget#ModTable" in app.styleSheet())
    window._on_theme_changed("dark")
    app.processEvents()
    window.close()
    for _ in range(5):
        app.processEvents()
        time.sleep(0.02)
    check("window closed", not window.isVisible())
    if original_config is None:
        CONFIG_PATH.unlink(missing_ok=True)
    else:
        CONFIG_PATH.write_bytes(original_config)


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        game = build_game_dir(root)
        test_config(root)
        test_crash(game)
        test_mods(game)
        test_backup(game)
        test_screenshots(game)
        test_modpack(root)
        test_servers()
        test_updater()
        test_java_scan()
        test_ui(root)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
