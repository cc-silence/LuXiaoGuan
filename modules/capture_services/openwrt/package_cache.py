"""Shared package-cache boundaries for IPK, APK and native index files."""
from pathlib import Path
from modules.platform_services import owned_cache


def prepare_cache_directory(path):
    path = Path(path).absolute()
    for existing in (path, *path.parents):
        if existing.exists() or existing.is_symlink():
            break
    else:
        raise OSError("缓存目录所在磁盘或共享不可用。")
    owned_cache.checked_path(existing, existing)
    path.mkdir(parents=True, exist_ok=True)
    owned_cache.checked_path(path, path)
    return path


def checked_cache_target(root, target):
    root, target = Path(root).absolute(), Path(target).absolute()
    target.relative_to(root)
    prepare_cache_directory(target.parent)
    owned_cache.checked_path(root, target.parent)
    if target.exists() or target.is_symlink():
        owned_cache.checked_path(root, target)
    return target
