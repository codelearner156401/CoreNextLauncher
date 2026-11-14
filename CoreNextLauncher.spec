# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

_root = Path(SPECPATH)


def _entries(relative, destination):
    """Bundle a path only when it exists, so a fresh checkout still builds."""
    target = _root / relative
    if not target.exists():
        return []
    return [(str(target), destination)]


_datas = [("launcher/resources", "launcher/resources")]
_datas += _entries("tools/easytier", "tools/easytier")
_datas += _entries("licenses", "licenses")
_datas += _entries("THIRD_PARTY_NOTICES.md", ".")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[('build/mc_core.exe', 'build')],
    datas=_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CoreNextLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/corelauncher.ico'],
)
