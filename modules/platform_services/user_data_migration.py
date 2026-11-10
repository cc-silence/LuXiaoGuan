"""One-time, same-volume migration of user directories without overwriting files."""
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import time
from uuid import uuid4

from .file_lease import file_lease
from .owned_cache import checked_path

LEGACY_DIRECTORY = "RouterManager"
CONFLICT_DIRECTORY = "legacy-RouterManager-conflicts"


def migrate_directory(source, target):
    source, target = Path(source).absolute(), Path(target).absolute()
    if source.parent != target.parent or source.name != LEGACY_DIRECTORY or target.name != "LuXiaoGuan":
        raise ValueError("Unexpected user-data migration paths")
    base = source.parent
    if not source.exists() and not source.is_symlink():
        return dict(moved=0, conflicts=0)
    checked_path(base, base)
    lock = base / ".LuXiaoGuan-data-migration.lock"
    if lock.exists() or lock.is_symlink():
        checked_path(base, lock)
    # Exclusive creation protects existing lock files from truncation.
    try:
        handle = lock.open("xb")
    except FileExistsError:
        handle = lock.open("r+b")
    with handle, file_lease(handle):
        if not source.exists() and not source.is_symlink():
            return dict(moved=0, conflicts=0)
        if not stat.S_ISDIR(checked_path(base, source).st_mode):
            raise ValueError("Legacy user data is not a directory")
        if not target.exists() and not target.is_symlink():
            # Renaming the root is constant-time, even for a large cache.
            source.rename(target)
            return dict(moved=1, conflicts=0)
        if not stat.S_ISDIR(checked_path(base, target).st_mode):
            raise ValueError("New user data is not a directory")
        deadline = time.monotonic() + 30
        count = 0
        conflicts = 0
        backup = None

        def merge(old, new):
            nonlocal count, conflicts, backup
            if time.monotonic() > deadline or count >= 100000:
                raise TimeoutError("User-data migration budget exceeded; remaining files retained")
            count += 1
            old_info = checked_path(base, old)
            if not (stat.S_ISDIR(old_info.st_mode) or stat.S_ISREG(old_info.st_mode)):
                raise ValueError("Unsupported user-data entry retained: " + str(old))
            if not new.exists() and not new.is_symlink():
                old.rename(new)
                return
            new_info = checked_path(base, new)
            if stat.S_ISDIR(old_info.st_mode) and stat.S_ISDIR(new_info.st_mode):
                # Snapshot names only; never follow links or retain an unbounded walk.
                with os.scandir(old) as entries:
                    for entry in entries:
                        merge(Path(entry.path), new / entry.name)
                old.rmdir()
                return
            if backup is None:
                backup_root = target / CONFLICT_DIRECTORY
                if backup_root.exists() or backup_root.is_symlink():
                    if not stat.S_ISDIR(checked_path(base, backup_root).st_mode):
                        raise ValueError("Conflict backup path is not a directory")
                else:
                    backup_root.mkdir(mode=0o700)
                backup = backup_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex)
                backup.mkdir(mode=0o700)
            destination = backup / old.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            old.rename(destination)
            conflicts += 1

        merge(source, target)
        return dict(moved=count, conflicts=conflicts, backup=str(backup) if backup else "")


def prepare_user_directories():
    """Run before configuration imports, preserving explicit test/user overrides."""
    from . import application_paths as paths
    if os.environ.get("ROUTERMANAGER_USER_CONFIG_DIR"):
        return ()
    if paths.is_linux():
        targets = tuple(dict.fromkeys(Path(paths.linux_user_directory(kind)) for kind in ("config", "state", "data")))
    else:
        targets = (paths.user_state_dir(),)
    result = tuple(migrate_directory(target.with_name(LEGACY_DIRECTORY), target) for target in targets)
    from modules.config_services.portable_settings_migration import prepare
    prepare()
    return result
