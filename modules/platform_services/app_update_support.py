"""Admission for the currently implemented native application updater."""
import platform
import struct
from .runtime import is_linux, is_windows


def update_unavailable_reason():
    if is_linux():
        if struct.calcsize('P')!=8 or platform.machine().casefold() not in {'amd64','x86_64'}:
            return 'Linux automatic updates require x86-64.'
        from .linux_update_admission import unavailable_reason
        return unavailable_reason()
    if not is_windows():
        return "The application updater does not support this operating system."
    if struct.calcsize("P") != 8 or platform.machine().casefold() not in {"amd64", "x86_64"}:
        return "The current update channel requires a Windows x86-64 process."
    return ""


def require_native_updater():
    reason = update_unavailable_reason()
    if reason:
        raise ValueError(reason)
    return "linux-x64" if is_linux() else "win-x64"


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
