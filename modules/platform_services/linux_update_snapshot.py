"""Prepare a stable old runtime for a Linux updater before installed files change.

The live installer remains disabled. This module creates no processes and never
writes installed files; snapshots live only in the validated private update stage.
"""
import errno
import hashlib
import json
import math
import time
import os
from pathlib import Path
import shutil
import stat
import uuid
from modules.app_update_services.linux_catalog import validate_entries, managed_linux_path
from modules.app_update_services.manifest import canonical
from modules.app_update_services.locations import validate_stage, cache_lock, linked
from .runtime import is_linux
from .local_rename import rename_without_replace


MAX_RECORD_BYTES = 16 * 1024 * 1024


def _deadline(cancel, timeout):
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 900:
        raise ValueError("Invalid runtime preparation timeout")
    end = time.monotonic() + timeout
    def check():
        if cancel(): raise InterruptedError("Runtime snapshot preparation cancelled")
        if time.monotonic() >= end: raise TimeoutError("Runtime snapshot preparation timed out")
    check()
    return check


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try: os.fsync(fd)
    finally: os.close(fd)


def _sync_file(path):
    with _open_regular(path) as stream:
        os.fsync(stream.fileno())


def _root(path):
    path = Path(path).absolute()
    if not path.is_dir() or any(linked(parent) for parent in (path, *path.parents)):
        raise ValueError("The updater snapshot requires an unlinked installation directory")
    return path


def _target(root, relative):
    path = root / managed_linux_path(relative)
    for parent in path.parents:
        if parent == root: break
        if linked(parent) or (parent.exists() and not parent.is_dir()):
            raise ValueError("Linked or non-directory runtime parent")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Runtime path escapes its snapshot")
    return path


def _permissions(path, info):
    return stat.S_IMODE(info.st_mode)


def _open_regular(path):
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("Linux no-follow file access is unavailable")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os,"O_NONBLOCK",0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Runtime member is not a regular file")
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    return stream


def _identity(path, info):
    # Creating a hardlink changes ctime and link count, not content identity.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, _permissions(path, info)


def _inspect(path, check=lambda:None):
    check()
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode):
        value = dict(kind="symlink", target=os.readlink(path))
    elif stat.S_ISREG(before.st_mode):
        digest = hashlib.sha256()
        with _open_regular(path) as stream:
            if _identity(path, os.fstat(stream.fileno())) != _identity(path, before):
                raise ValueError("Runtime member changed before reading")
            while True:
                check()
                block = stream.read(1024 * 1024)
                if not block: break
                digest.update(block)
            if _identity(path, os.fstat(stream.fileno())) != _identity(path, before):
                raise ValueError("Runtime member changed while reading")
        value = dict(kind="file", sha256=digest.hexdigest(), size=before.st_size,
                     mode=_permissions(path, before))
    else:
        raise ValueError("Special runtime files cannot be snapshotted")
    if _identity(path, path.lstat()) != _identity(path, before):
        raise ValueError("Runtime member changed during inspection")
    return value, _identity(path, before)


def _matches(actual, entry):
    return all(entry.get(key) == value for key, value in actual.items())


def _catalog(catalog):
    if (not isinstance(catalog, dict) or catalog.get("schema") != 2
            or catalog.get("product") != "RouterManager" or catalog.get("platform") != "linux-x64"):
        raise ValueError("A Linux installed-runtime inventory is required")
    return validate_entries(catalog.get("files"))


def _executable(path):
    with _open_regular(path) as source:
        header = source.read(20)
    if (len(header) != 20 or header[:7] != b"\x7fELF\x02\x01\x01"
            or int.from_bytes(header[16:18], "little") not in {2, 3}
            or int.from_bytes(header[18:20], "little") != 62):
        raise ValueError("The updater runtime requires a native Linux x86-64 executable")


def _write_record(path, value):
    data = canonical(value)
    if len(data) > MAX_RECORD_BYTES: raise ValueError("Runtime snapshot record exceeds its limit")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def verify_runtime_snapshot(stage, expected_catalog_sha256, *, cancel=lambda:False, timeout=120):
    if not is_linux(): raise OSError("Native Linux updater snapshots require Linux")
    return _verify_runtime_snapshot(stage, expected_catalog_sha256, _deadline(cancel, timeout))


def _verify_runtime_snapshot(stage, expected_catalog_sha256, check):
    check()
    stage = validate_stage(stage)
    runtime = _root(stage / "runtime")
    record = stage / "runtime-snapshot.json"
    if linked(record) or not record.is_file() or record.stat().st_size > MAX_RECORD_BYTES:
        raise ValueError("Verified runtime snapshot record is missing")
    with _open_regular(record) as stream:
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES: raise ValueError("Runtime snapshot record exceeds its limit")
    value = json.loads(raw)
    catalog = value.get("catalog")
    if (value.get("schema") != 1 or hashlib.sha256(canonical(catalog)).hexdigest() != expected_catalog_sha256
            or value.get("catalog_sha256") != expected_catalog_sha256):
        raise ValueError("The updater runtime does not match its recorded inventory")
    entries = _catalog(catalog)
    for relative, entry in entries.items():
        actual, _identity_value = _inspect(_target(runtime, relative), check)
        if not _matches(actual, entry): raise ValueError("Updater runtime snapshot changed")
    _executable(runtime / "LuXiaoGuan")
    return runtime


def prepare_runtime_snapshot(root, stage, installed_catalog, *, cancel=lambda:False, timeout=120):
    if not is_linux(): raise OSError("Native Linux updater snapshots require Linux")
    check = _deadline(cancel, timeout)
    root = _root(root); stage = validate_stage(stage, root)
    if not stage.is_dir(): raise ValueError("Prepare the update stage before its runtime snapshot")
    entries = _catalog(installed_catalog)
    catalog_sha = hashlib.sha256(canonical(installed_catalog)).hexdigest()
    with cache_lock(stage.parent) as acquired:
        if not acquired:
            raise BlockingIOError("Another update owns the runtime preparation lease")
        if (stage / "runtime").exists():
            return _verify_runtime_snapshot(stage, catalog_sha, check)
        if any(stage.glob(".runtime-*")):
            raise ValueError("An interrupted runtime preparation requires reconciliation")
        temporary = stage / (".runtime-" + uuid.uuid4().hex)
        temporary.mkdir(mode=0o700)
        linked_files, copied_files = 0, 0
        try:
            # Files precede links; no member is a link/directory parent in the
            # validated catalog. All bytes/modes are checked on both sides.
            ordered = sorted(entries.items(), key=lambda pair: pair[1]["kind"] == "symlink")
            directories = {temporary}
            for relative, entry in ordered:
                check()
                source, destination = _target(root, relative), _target(temporary, relative)
                before, identity = _inspect(source, check)
                if not _matches(before, entry): raise ValueError("Installed runtime differs from its baseline")
                destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                parent = destination.parent
                while parent != temporary:
                    directories.add(parent); parent = parent.parent
                if entry["kind"] == "symlink":
                    os.symlink(entry["target"], destination)
                else:
                    try:
                        os.link(source, destination, follow_symlinks=False)
                        linked_files += 1
                    except OSError as exc:
                        if exc.errno not in {errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS}:
                            raise
                        with _open_regular(source) as incoming, destination.open("xb") as output:
                            while True:
                                check()
                                block = incoming.read(1024 * 1024)
                                if not block: break
                                output.write(block)
                            destination.chmod(entry["mode"])
                            output.flush(); os.fsync(output.fileno())
                        copied_files += 1
                if entry["kind"] == "file": _sync_file(destination)
                actual, _ = _inspect(destination, check)
                if not _matches(actual, entry) or _identity(source, source.lstat()) != identity:
                    raise ValueError("Runtime snapshot verification failed")
            for directory in sorted(directories, key=lambda path:len(path.parts), reverse=True):
                check(); _sync_directory(directory)
            _executable(temporary / "LuXiaoGuan")
            _write_record(stage / "runtime-snapshot.json", dict(schema=1, catalog_sha256=catalog_sha,
                catalog=installed_catalog, hardlinked_files=linked_files, copied_files=copied_files))
            rename_without_replace(temporary, stage / "runtime")
            _sync_directory(stage)
            return _verify_runtime_snapshot(stage, catalog_sha, check)
        finally:
            if temporary.exists():
                if temporary.parent.resolve() != stage.resolve() or linked(temporary):
                    raise ValueError("Unsafe runtime staging cleanup target")
                shutil.rmtree(temporary)
