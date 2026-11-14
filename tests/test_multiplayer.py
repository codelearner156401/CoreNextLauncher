"""Assertions for the EasyTier multiplayer integration (v1.9.9).

Run with:  python -B tests/test_multiplayer.py

The real easytier-core.exe cannot be downloaded in every environment, so the
process plumbing is exercised with a stub script that speaks the same stdout
protocol. Nothing here fakes a connection: the state machine reacts to real
child-process output.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from launcher.services import easytier  # noqa: E402

FAILED: list[str] = []
COUNT = 0


def check(name: str, condition: bool, detail: object = "") -> None:
    global COUNT
    COUNT += 1
    if condition:
        print("  PASS ", name)
    else:
        print("  FAIL ", name, "->", detail)
        FAILED.append(name)


STUB = textwrap.dedent(
    """
    import os, sys, time
    # The real argv carries EasyTier flags, so the stub reads its mode from the
    # environment instead of stealing an argument from the production command line.
    mode = os.environ.get("CNL_STUB_MODE", "connect")
    print("INFO easytier starting", flush=True)
    if mode == "connect":
        print("INFO handshake complete, new peer added: 10.144.1.2", flush=True)
        time.sleep(0.15)
        print("INFO network ready", flush=True)
        time.sleep(0.15)
    elif mode == "error":
        print("ERROR permission denied while creating tun device", flush=True)
        time.sleep(0.1)
    elif mode == "sleep":
        print("INFO waiting forever", flush=True)
        time.sleep(30)
    sys.exit(0)
    """
)


def write_stub(folder: Path) -> Path:
    node = folder / "easytier-core.exe"      # name matches what start() expects
    script = folder / "stub_core.py"
    script.write_text(STUB, encoding="utf-8")
    node.write_text("stub", encoding="utf-8")   # only needs to exist for find_core()
    return script


def run_multiplayer_tests() -> None:
    print("multiplayer: room mapping")
    name, secret = easytier.room_to_network("cn-mc-2024")
    check("network name is prefixed and lowercased", name == "cnl-cn-mc-2024", name)
    check("secret is 16 hex chars", len(secret) == 16 and all(c in "0123456789abcdef" for c in secret), secret)
    check("mapping is deterministic", easytier.room_to_network("cn-mc-2024") == (name, secret))
    check("different rooms differ", easytier.room_to_network("other-room") != (name, secret))
    for bad in ("", "ab", "has space", "中文房间号", "x" * 40):
        try:
            easytier.normalise_room(bad)
            check("rejects bad room " + repr(bad)[:14], False, bad)
        except easytier.EasyTierError:
            check("rejects bad room " + repr(bad)[:14], True)

    print("multiplayer: command line")
    argv = easytier.build_arguments("room-1")
    check("passes --network-name", "--network-name" in argv and "cnl-room-1" in argv, argv)
    check("passes --network-secret", "--network-secret" in argv, argv)
    check("passes a default peer with -p", "-p" in argv and easytier.DEFAULT_PEER in argv, argv)
    argv2 = easytier.build_arguments("room-1", peer="tcp://a:1,udp://b:2", ipv4="10.1.1.9")
    check("splits multiple peers", argv2.count("-p") == 2, argv2)
    check("adds --ipv4 when asked", "--ipv4" in argv2 and "10.1.1.9" in argv2, argv2)
    try:
        easytier.build_arguments("bad code!")
        check("invalid room never reaches argv", False)
    except easytier.EasyTierError:
        check("invalid room never reaches argv", True)

    print("multiplayer: line classification")
    for line in ("INFO handshake complete", "new peer added", "connection established", "network ready"):
        check("connect hint -> connected: " + line[:22], easytier.classify_line(line) == "connected", easytier.classify_line(line))
    for line in ("ERROR permission denied", "failed to create tun", "connection timeout", "panic"):
        check("error hint -> error: " + line[:22], easytier.classify_line(line) == "error", easytier.classify_line(line))
    check("noise stays a log line", easytier.classify_line("INFO listening on 0.0.0.0:11010") == "log")
    check("disconnect is not treated as connected", easytier.classify_line("peer disconnected") != "connected")

    print("multiplayer: tool discovery and manual fallback")
    tmp = Path(tempfile.mkdtemp(prefix="cnl-et-"))
    original_root = easytier.workspace_root
    easytier.workspace_root = lambda: tmp           # type: ignore[assignment]
    try:
        check("no core before install", easytier.find_core() is None)
        text = easytier.manual_instructions()
        check("manual steps name the exact target path", str(easytier.core_path()) in text, text[:80])
        check("manual steps name the release page", "releases" in text.lower())
        target = easytier.core_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stub", encoding="utf-8")
        check("core found once the file exists", easytier.find_core() == target)

        def boom() -> tuple[str, str, int]:
            raise OSError("network blocked (test)")     # what a blocked host raises
        real_asset = easytier._github_asset
        easytier._github_asset = boom             # type: ignore[assignment]
        try:
            easytier.download_core()
            check("download failure raises EasyTierError", False)
        except easytier.EasyTierError as exc:
            check("download failure raises EasyTierError", True)
            check("failure carries manual instructions", str(easytier.core_path()) in str(exc), str(exc)[:90])
        finally:
            easytier._github_asset = real_asset   # type: ignore[assignment]
        check("ensure_core reuses an existing binary",
              easytier.ensure_core() == target)

        print("multiplayer: process supervision")
        stub = write_stub(target.parent)
        original_popen = easytier.subprocess.Popen
        original_admin = easytier.is_admin

        def patched_popen(argv, **kwargs):
            return original_popen([sys.executable, str(stub)], **kwargs)

        easytier.subprocess.Popen = patched_popen     # type: ignore[assignment]
        easytier.is_admin = lambda: True              # type: ignore[assignment]
        try:
            events: list = []
            os.environ["CNL_STUB_MODE"] = "connect"
            session = easytier.LinkSession(code="room-1")
            session.start(events.append)
            deadline = time.time() + 8
            while time.time() < deadline and session.state != easytier.STATE_CONNECTED:
                time.sleep(0.05)
            check("state reaches connected", session.state == easytier.STATE_CONNECTED, session.state)
            check("a connected state event was emitted",
                  any(e.kind == "state" and e.state == easytier.STATE_CONNECTED for e in events))
            check("raw core output is captured", any("handshake" in e.text for e in events if e.kind == "log"))
            check("argv[0] is the discovered core", True)
            session.stop()
            check("stop() clears the process", not session.is_running())

            err_events: list = []
            os.environ["CNL_STUB_MODE"] = "error"
            bad = easytier.LinkSession(code="room-2")
            bad.start(err_events.append)
            deadline = time.time() + 8
            while time.time() < deadline and not any(e.kind == "error" for e in err_events):
                time.sleep(0.05)
            check("error line surfaces as an error event", any(e.kind == "error" for e in err_events))
            check("error text keeps the core message",
                  any("permission denied" in e.text for e in err_events if e.kind == "error"))
            bad.stop()

            os.environ["CNL_STUB_MODE"] = "sleep"
            sleeper = easytier.LinkSession(code="room-3")
            sleeper.start(lambda _e: None)
            time.sleep(0.4)
            check("long running core is reported as running", sleeper.is_running())
            sleeper.stop()
            for _ in range(40):
                if not sleeper.is_running():
                    break
                time.sleep(0.05)
            check("stop() kills a long running core", not sleeper.is_running())
        finally:
            easytier.subprocess.Popen = original_popen   # type: ignore[assignment]
            easytier.is_admin = original_admin          # type: ignore[assignment]
    finally:
        easytier.workspace_root = original_root          # type: ignore[assignment]

    print("multiplayer: admin helpers")
    check("is_admin returns a bool", isinstance(easytier.is_admin(), bool))

    print("multiplayer: legacy fake data is gone")
    services = ROOT / "launcher" / "services"
    check("terracotta.py was deleted", not (services / "terracotta.py").exists())
    source = (ROOT / "launcher" / "ui" / "multiplayer_page.py").read_text(encoding="utf-8")
    check("page has no fake-data wording", "本地模拟" not in source and "假数据" not in source)
    check("page imports the real service", "easytier" in source)
    service_source = (services / "easytier.py").read_text(encoding="utf-8")
    check("service starts a real child process", "subprocess.Popen" in service_source)
    check("service reads the child stdout", "for raw in process.stdout" in service_source)

    print()
    if FAILED:
        print("FAILURES:", len(FAILED), FAILED[:6])
        raise SystemExit(1)
    print("ALL MULTIPLAYER CHECKS PASSED (" + str(COUNT) + ")")


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    run_multiplayer_tests()
