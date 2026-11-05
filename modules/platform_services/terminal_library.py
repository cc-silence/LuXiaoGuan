"""Select the bundled native terminal without loading another platform's binary."""
from pathlib import Path
import struct
import platform
from .runtime import is_linux, is_windows


def terminal_library_path(base):
    folder = Path(base) / "bin/router_terminal"
    if is_windows():
        return folder / "winbox_terminal.dll"
    if is_linux():
        if struct.calcsize("P") != 8 or platform.machine().casefold() not in {"amd64", "x86_64"}:
            raise OSError("The native Linux terminal requires an x86-64 process")
        return folder / "libroutermanager_terminal.so"
    raise OSError("The native terminal does not support this operating system")


def validate_linux_library(path):
    """Read only the ELF identification before any native library code can execute."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Linked native terminal libraries are not accepted")
    with path.open("rb") as source:
        header = source.read(20)
    if (len(header) != 20 or header[:7] != b"\x7fELF\x02\x01\x01"
            or int.from_bytes(header[16:18], "little") != 3
            or int.from_bytes(header[18:20], "little") != 62):
        raise ValueError("The native terminal must be a Linux x86-64 shared library")
    return path
