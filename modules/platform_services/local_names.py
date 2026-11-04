"""Native single-component filenames, independent of remote POSIX paths."""
from pathlib import PureWindowsPath
from .runtime import is_windows


def valid_local_name(name):
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\0" in name:
        return False
    if is_windows():
        return not (any(ord(c) < 32 or c in '<>:"\\|?*' for c in name)
                    or name.endswith((" ", ".")) or PureWindowsPath(name).is_reserved())
    return True
