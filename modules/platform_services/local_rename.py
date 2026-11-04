"""Atomic publication of files or directories without replacing a destination."""
import ctypes
import errno
import os
from functools import lru_cache
from .runtime import is_linux, is_windows


@lru_cache(maxsize=1)
def _linux_api():
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError:
        raise OSError(errno.ENOSYS, "Atomic no-replace rename is unavailable.") from None
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                       ctypes.c_uint)
    rename.restype = ctypes.c_int
    # Retain the library along with its callable.
    return libc, rename


def rename_without_replace(source, destination, *, source_dir_fd=None, destination_dir_fd=None):
    if is_windows():
        if source_dir_fd is not None or destination_dir_fd is not None:
            raise OSError(errno.ENOSYS, "Directory-relative rename requires Linux")
        os.rename(source, destination)
        return
    if not is_linux():
        raise OSError(errno.ENOSYS, "Atomic no-replace rename is unsupported.")
    _libc, rename = _linux_api()
    # RENAME_NOREPLACE checks existence inside the kernel transaction.
    source_fd = -100 if source_dir_fd is None else source_dir_fd
    destination_fd = -100 if destination_dir_fd is None else destination_dir_fd
    if rename(source_fd, os.fsencode(source), destination_fd, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), os.fspath(destination))
