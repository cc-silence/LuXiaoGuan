"""Read-only Linux result metadata, isolated from the GUI and bounded by its caller."""
import base64
import json
import os
from pathlib import Path
import stat
import sys

LOCAL_FILESYSTEMS = frozenset({"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs",
                               "tmpfs", "ramfs", "vfat", "exfat", "ntfs3"})


def mount_types():
    with open("/proc/self/mountinfo", "rb") as source:
        raw = source.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Mount inventory exceeds its bound")
    result = {}
    for line in raw.decode("utf-8").splitlines():
        left, separator, right = line.partition(" - ")
        if not separator:
            raise ValueError("Invalid mount inventory")
        fields, values = left.split(), right.split()
        if not fields or not fields[0].isdigit() or not values:
            raise ValueError("Invalid mount inventory")
        result[int(fields[0])] = values[0]
    return result


def is_local_fd(fd, mounts):
    with open("/proc/self/fdinfo/" + str(fd), "rb") as source:
        raw = source.read(4097)
    if len(raw) > 4096:
        return False
    ids = [line.partition(":")[2].strip() for line in raw.decode("ascii").splitlines()
           if line.startswith("mnt_id:")]
    return len(ids) == 1 and mounts.get(int(ids[0])) in LOCAL_FILESYSTEMS


def _identity(info):
    return info.st_dev, info.st_ino


def _unchanged(edges):
    for parent, name, child in edges:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or _identity(current) != _identity(os.fstat(child)):
            return False
    return True


def probe_path(path, mounts):
    if (not isinstance(path, str) or len(path) > 512 or not path.startswith("/")
            or path.startswith("//") or any(ord(c) < 32 for c in path)
            or any(part in {"", ".", ".."} for part in path.split("/")[1:])):
        return "unknown"
    descriptors, edges = [], []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        parent = os.open("/", flags)
        descriptors.append(parent)
        parts = path.split("/")[1:]
        for index, name in enumerate(parts):
            if not is_local_fd(parent, mounts):
                return "unknown"
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return "missing" if _unchanged(edges) else "unknown"
            if stat.S_ISLNK(info.st_mode):
                return "unknown"
            if index == len(parts) - 1:
                # A file may itself be a mount point. Do not open it, read its
                # contents or infer local storage merely from its parent.
                if info.st_dev != os.fstat(parent).st_dev:
                    return "unknown"
                return "present" if stat.S_ISREG(info.st_mode) and _unchanged(edges) else "unknown"
            if not stat.S_ISDIR(info.st_mode):
                return "unknown"
            child = os.open(name, flags, dir_fd=parent)
            descriptors.append(child)
            if _identity(os.fstat(child)) != _identity(info):
                return "unknown"
            edges.append((parent, name, child))
            parent = child
        return "unknown"
    except (OSError, ValueError, UnicodeError):
        return "unknown"
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def run_worker(argv, output=None):
    outgoing = sys.stdout if output is None else output
    if (not sys.platform.startswith("linux") or len(argv) != 3
            or argv[1] != "--file-presence-worker" or len(argv[2]) > 65536):
        return 2
    try:
        paths = json.loads(base64.b64decode(argv[2], validate=True))
        if (not isinstance(paths, list) or not 1 <= len(paths) <= 64
                or any(not isinstance(path, str) or len(path) > 512 for path in paths)):
            return 2
        mounts = mount_types()
        result = [probe_path(path, mounts) for path in paths]
        if mount_types() != mounts:
            result = ["unknown"] * len(paths)
    except (OSError, ValueError, UnicodeError, TypeError):
        return 2
    outgoing.write(json.dumps(result, ensure_ascii=True) + "\n")
    outgoing.flush()
    return 0


def worker_command(paths):
    payload = base64.b64encode(json.dumps(paths, ensure_ascii=True).encode("ascii")).decode("ascii")
    args = [sys.executable]
    if not getattr(sys, "frozen", False):
        args.append(str(Path(__file__).resolve().parents[2] / "main.py"))
    return args + ["--file-presence-worker", payload]
