"""Nonblocking byte-range leases shared by Windows and POSIX resource users."""
from contextlib import contextmanager
import errno
import os

_FAIL_IMMEDIATELY = 0x00000001
_EXCLUSIVE_LOCK = 0x00000002


@contextmanager
def file_lease(handle, *, shared=False):
    if os.name != "nt":
        import fcntl
        fcntl.flock(handle.fileno(), (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    lock = kernel.LockFileEx
    lock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                     wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    lock.restype = wintypes.BOOL
    unlock = kernel.UnlockFileEx
    unlock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                       wintypes.DWORD, ctypes.POINTER(Overlapped)]
    unlock.restype = wintypes.BOOL
    native = wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno()))
    overlap = Overlapped()
    if not lock(native, _FAIL_IMMEDIATELY | (0 if shared else _EXCLUSIVE_LOCK), 0, 1, 0, ctypes.byref(overlap)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not unlock(native, 0, 1, 0, ctypes.byref(overlap)):
            raise ctypes.WinError(ctypes.get_last_error())


def lease_conflict(error):
    return (getattr(error, "winerror", None) in (32, 33)
            if os.name == "nt" else error.errno in (errno.EAGAIN, errno.EACCES))
