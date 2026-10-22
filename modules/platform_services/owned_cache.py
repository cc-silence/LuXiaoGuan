"""自有缓存删除边界：拒绝链接，先枚举再按身份逐项删除。"""

import os
import stat
import time
from pathlib import Path


def checked_path(root, path):
    """校验绝对根及所有父级不含重解析点，返回目标 lstat；不创建目录。"""
    root, path = Path(root), Path(path)
    if not root.is_absolute() or not path.is_absolute() or ".." in path.parts or ".." in root.parts:
        raise ValueError(f"Unsafe cache path: {path}")
    path.relative_to(root)
    for part in reversed((path, *path.parents)):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Cache link/reparse point retained: {part}")
    if path.resolve() != path or root.resolve() != root:
        raise ValueError(f"Noncanonical cache path retained: {path}")
    return info


def inventory(root, path, cancel=None, deadline=None, limit=20000):
    """有界枚举普通文件和目录；未知类型或任何链接使整个候选保留。"""
    deadline = deadline if deadline is not None else time.monotonic() + 30
    pending, entries = [Path(path)], []
    while pending:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Cache maintenance cancelled; remaining files retained")
        if time.monotonic() >= deadline or len(entries) >= limit:
            raise TimeoutError(f"Cache scan budget exceeded: {path}")
        current = pending.pop()
        info = checked_path(root, current)
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError(f"Unknown cache entry retained: {current}")
        entries.append((current, info))
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(current) as children:
                for child in children:
                    if len(pending) + len(entries) >= limit:
                        raise TimeoutError(f"Cache entry budget exceeded: {path}")
                    pending.append(Path(child.path))
    return entries


def delete_inventory(root, entries, cancel=None, deadline=None, protected=lambda _path: False):
    """逆序删除已核验条目，每次复核身份和引用；错误直接返回调用方报告。"""
    deadline = deadline if deadline is not None else time.monotonic() + 30
    for path, previous in reversed(entries):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Cache maintenance cancelled; remaining files retained")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Cache deletion budget exceeded: {path}")
        if protected(path):
            raise ValueError(f"Referenced cache retained: {path}")
        current = checked_path(root, path)
        if (current.st_dev, current.st_ino, current.st_mode) != (previous.st_dev, previous.st_ino, previous.st_mode):
            raise ValueError(f"Changed cache identity retained: {path}")
        if stat.S_ISDIR(current.st_mode):
            path.rmdir()
        else:
            if (current.st_size, current.st_mtime_ns) != (previous.st_size, previous.st_mtime_ns):
                raise ValueError(f"Changed cache file retained: {path}")
            path.unlink()
