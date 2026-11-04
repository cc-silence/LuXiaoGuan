"""Refuse packaging with a Qt runtime different from the pinned GUI dependency."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def expected_qt_version(path=None):
    text=Path(path or ROOT/'requirements.txt').read_text(encoding='utf-8')
    matches=re.findall(r'^PySide6==([0-9]+\.[0-9]+\.[0-9]+)[ \t]*$',text,re.MULTILINE)
    if len(matches)!=1:raise RuntimeError('Require one exact PySide6 release pin')
    return matches[0]


def require_qt_runtime():
    import PySide6
    from PySide6.QtCore import qVersion
    expected=expected_qt_version()
    actual=PySide6.__version__;qt=qVersion()
    if actual!=expected or qt!=expected:
        raise RuntimeError('Qt runtime mismatch: required '+expected+', PySide6='+actual+', Qt='+qt+
            '. Install requirements-ci.txt into the build environment before packaging.')
    return dict(pyside=actual,qt=qt)
