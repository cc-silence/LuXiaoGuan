"""Small allowlisted application environment evidence; no account or filesystem paths."""
import re
import sys


def sanitized_runtime_evidence(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid runtime evidence")
    result = {}
    for name in ("system_version", "qt_platform", "qt_version", "pyside_version", "python_version", "theme"):
        value = data.get(name)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", value):
            result[name] = value
    value = data.get("scale")
    if type(value) is int and 50 <= value <= 500:
        result["scale"] = value
    for name in ("translucent_window", "styled_frame"):
        if type(data.get(name)) is bool:
            result[name] = data[name]
    return result


def runtime_evidence(window):
    from PySide6 import __version__
    from PySide6.QtCore import Qt, qVersion
    from PySide6.QtGui import QGuiApplication
    version = sys.getwindowsversion() if sys.platform == "win32" else None
    manager = getattr(window, "scale_manager", None)
    frame = getattr(window, "window_frame", None)
    return sanitized_runtime_evidence(dict(
        system_version="Windows " + ".".join(map(str, (version.major, version.minor, version.build))) if version else sys.platform,
        qt_platform=QGuiApplication.platformName(), qt_version=qVersion(), pyside_version=__version__,
        python_version=".".join(map(str,sys.version_info[:3])), theme=getattr(window,"theme_name","unknown"),
        scale=getattr(manager,"global_scale",None),
        translucent_window=window.testAttribute(Qt.WA_TranslucentBackground),
        styled_frame=bool(frame and frame.testAttribute(Qt.WA_StyledBackground))))
