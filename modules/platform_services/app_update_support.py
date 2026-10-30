"""Admission for the currently implemented native application updater."""
import platform
import struct
from .runtime import is_linux, is_windows


def update_unavailable_reason():
    if is_linux():
        return ("\u004c\u0069\u006e\u0075\u0078 \u81ea\u52a8\u66f4\u65b0\u5668\u5c1a\u672a\u5b8c\u6210\u9002\u914d\uff0c"
                "\u5f53\u524d\u4e0d\u80fd\u4f7f\u7528 Windows \u66f4\u65b0\u5305\u3002")
    if not is_windows():
        return "The application updater does not support this operating system."
    if struct.calcsize("P") != 8 or platform.machine().casefold() not in {"amd64", "x86_64"}:
        return "The current update channel requires a Windows x86-64 process."
    return ""


def require_native_updater():
    reason = update_unavailable_reason()
    if reason:
        raise ValueError(reason)
    return "win-x64"


def package_unavailable_reason():
    if not (is_windows() or is_linux()):
        return "Package downloads do not support this operating system."
    if struct.calcsize("P") != 8 or platform.machine().casefold() not in {"amd64", "x86_64"}:
        return "The package download channel requires an x86-64 process."
    return ""


def require_package_platform():
    reason = package_unavailable_reason()
    if reason:
        raise ValueError(reason)
    return "linux-x64" if is_linux() else "win-x64"
