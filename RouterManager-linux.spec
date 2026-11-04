# -*- mode: python ; coding: utf-8 -*-
"""Debian 12 onedir candidate; never collect a Windows updater or local config."""
import sys
from pathlib import Path
ROOT=Path(SPECPATH)
sys.path.insert(0,str(ROOT))
from modules.platform_services.terminal_library import validate_linux_library
terminal=validate_linux_library(ROOT/'bin/router_terminal/libroutermanager_terminal.so')
excludes=['tkinter','matplotlib','numpy','pandas','scipy','IPython','pytest',
          'PySide6.QtQml','PySide6.QtQuick','PySide6.QtQuickWidgets','PySide6.QtWebEngineCore',
          'PySide6.QtWebEngineWidgets','PySide6.QtWebEngineQuick','PySide6.QtVirtualKeyboard']
a=Analysis([str(ROOT/'main.py')],pathex=[str(ROOT)],
    binaries=[(str(terminal),'bin/router_terminal')],datas=[],
    hiddenimports=['paramiko','bcrypt','cryptography','nacl','cffi','invoke','py7zr',
                   'secretstorage','jeepney'],excludes=excludes,noarchive=False,optimize=0)
# Analysis must inspect the native terminal to collect its Qt dependencies. The
# actual terminal is seeded once at its public application-root location later.
a.binaries=[row for row in a.binaries if row[0]!='bin/router_terminal/libroutermanager_terminal.so']
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='LuXiaoGuan',console=False,
        contents_directory='resources',upx=False)
collect=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='LuXiaoGuan')
