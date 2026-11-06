"""Non-blocking, reentrant leases for local bundle readers and writers."""
from contextlib import contextmanager
from functools import wraps
import inspect
import hashlib
import os
from pathlib import Path
import threading
from .common_bundle_repository import safe_path, bundle_root

_local = threading.local()


def storage_lock_path(root):
    """Coordinate by canonical resource root without requiring it to be writable."""
    from modules.config_services.config_store import user_config_dir
    key = os.path.normcase(str(Path(root).resolve()))
    locks = user_config_dir() / "offline-storage-locks"
    path = safe_path(locks, hashlib.sha256(os.fsencode(key)).hexdigest() + ".lock")
    locks.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def bundle_lock(root, *, shared=False):
    root = Path(root).absolute()
    safe_path(root, "current.json")
    key = os.path.normcase(str(root.resolve()))
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = {}
    if key in held:
        if held[key] and not shared:
            raise OSError("Cannot upgrade an active bundle read lease to a write lease")
        yield
        return
    with storage_lock_path(root).open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        from modules.platform_services.file_lease import file_lease, lease_conflict
        lease = file_lease(handle, shared=shared)
        try:
            lease.__enter__()
        except OSError as exc:
            if not lease_conflict(exc):
                raise
            raise OSError("\u79bb\u7ebf\u5305\u6b63\u5728\u4e0b\u8f7d\u3001\u5b89\u88c5\u6216\u63d0\u53d6\uff0c\u8bf7\u7b49\u5f85\u5b8c\u6210\u540e\u91cd\u8bd5\u3002") from exc
        held[key] = shared
        try:
            yield
        finally:
            held.pop(key)
            lease.__exit__(None, None, None)


def locked_bundle(function=None, *, shared=False):
    """Keep one root stable for the full operation, including returned copies."""
    if function is None:
        return lambda operation: locked_bundle(operation, shared=shared)
    signature = inspect.signature(function)
    @wraps(function)
    def locked(*args, **kwargs):
        values = signature.bind(*args, **kwargs)
        values.apply_defaults()
        root = values.arguments.get("root")
        if root is None:
            root = bundle_root(values.arguments.get("kind", "common"))
        with bundle_lock(root, shared=shared):
            return function(*args, **kwargs)
    return locked
