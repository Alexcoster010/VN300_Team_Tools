# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path


ROOT = Path(os.environ.get("VN300_PROJECT_ROOT", SPECPATH)).resolve()
APP_DIR = ROOT / "app"
ANALYSIS_DIR = ROOT / "analysis"
ICON = ROOT / "packaging" / "assets" / "VN300TeamTools.ico"
VERSION_FILE = os.environ.get("VN300_VERSION_FILE")
PATHEX = [str(APP_DIR), str(ANALYSIS_DIR)]


def executable(script, name, console):
    analysis = Analysis(
        [str(script)],
        pathex=PATHEX,
        binaries=[],
        datas=[],
        hiddenimports=[],
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
        optimize=0,
    )
    archive = PYZ(analysis.pure)
    return EXE(
        archive,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ICON),
        version=VERSION_FILE,
    )


desktop = executable(APP_DIR / "vn300_qt_app.py", "VN300TeamTools", False)
analyzer = executable(APP_DIR / "vn300_analyzer_entry.py", "VN300Analyzer", True)
updater = executable(APP_DIR / "vn300_update_helper.py", "VN300UpdateHelper", False)
