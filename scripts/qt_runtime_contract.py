"""Refuse packaging with a Qt runtime different from the pinned GUI dependency."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def require_qt_runtime():
    import PySide6
    from PySide6.QtCore import qVersion
    text=(ROOT/'requirements.txt').read_text(encoding='utf-8')
    match=re.search(r'^PySide6==([0-9]+\.[0-9]+\.[0-9]+)\s*$',text,re.MULTILINE)
    if match is None:raise RuntimeError('Missing exact PySide6 release pin')
    expected=match[1]
    actual=PySide6.__version__;qt=qVersion()
    if actual!=expected or qt!=expected:
        raise RuntimeError('Qt runtime mismatch: required '+expected+', PySide6='+actual+', Qt='+qt+
            '. Install requirements-ci.txt into the build environment before packaging.')
    return dict(pyside=actual,qt=qt)
