# -*- mode: python ; coding: utf-8 -*-
"""RouterManager GUI and independent update transaction worker."""
import os
import sys
from pathlib import Path
ROOT=Path(SPECPATH)
sys.path.insert(0,str(ROOT))
from modules.release_identity import PACKAGE_DIRECTORY,APPLICATION_EXECUTABLE,APPLICATION_ICON
stage=Path(os.environ["ROUTERMANAGER_RELEASE_STAGE"])
version_file=str(stage/"version.txt")
excludes=["tkinter","matplotlib","numpy","pandas","scipy","IPython","pytest",
          "PySide6.QtQml","PySide6.QtQuick","PySide6.QtQuickWidgets","PySide6.QtWebEngineCore",
          "PySide6.QtWebEngineWidgets","PySide6.QtWebEngineQuick","PySide6.QtVirtualKeyboard"]
a=Analysis([str(ROOT/"main.py")],pathex=[str(ROOT)],
    binaries=[(str(ROOT/"bin/router_terminal/winbox_terminal.dll"),"bin/router_terminal")],
    datas=[],hiddenimports=["paramiko","bcrypt","cryptography","nacl","cffi","invoke","py7zr"],
    excludes=excludes,noarchive=False,optimize=0)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name=Path(APPLICATION_EXECUTABLE).stem,console=False,
    icon=str(ROOT/APPLICATION_ICON),version=version_file,contents_directory="resources",upx=False)
w=Analysis([str(ROOT/"scripts/app_update_helper.py")],pathex=[str(ROOT)],binaries=[],datas=[],
    hiddenimports=[],excludes=excludes+["PySide6","shiboken6"],noarchive=False,optimize=0)
wp=PYZ(w.pure)
worker=EXE(wp,w.scripts,w.binaries,w.datas,name="RouterManagerUpdateWorker",console=False,
    icon=str(ROOT/APPLICATION_ICON),version=version_file,upx=False)
collect=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name=PACKAGE_DIRECTORY)
