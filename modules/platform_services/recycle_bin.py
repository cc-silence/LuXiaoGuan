"""Platform recycle-bin dispatch; never fall back to permanent deletion."""
from .runtime import is_linux, is_windows


def move_to_recycle_bin(path, cancel):
    if cancel.is_set():
        raise InterruptedError("Operation cancelled.")
    if is_windows():
        from .windows_recycle_bin import move_to_recycle_bin as move
        return move(path, cancel)
    if is_linux():
        from PySide6.QtCore import QFile
        # Instance overload exists in the pinned Qt 6.3; no Qt 6.9-only probe.
        entry = QFile(str(path))
        if cancel.is_set():
            raise InterruptedError("Operation cancelled.")
        return entry.moveToTrash()
    raise OSError("The system recycle bin is unsupported.")
