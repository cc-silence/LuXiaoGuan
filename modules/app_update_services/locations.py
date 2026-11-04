"""One application-owned update location, with read/recovery support for old stages."""
from contextlib import contextmanager
import os
from pathlib import Path
import re

CACHE_DIRECTORY = ".routermanager-update"
LEGACY_CACHE_DIRECTORY = ".winbox-update"
CACHE_DIRECTORIES = (CACHE_DIRECTORY, LEGACY_CACHE_DIRECTORY)


def linked(path):
    path = Path(path)
    try:
        # lstat inspects a Windows junction itself rather than its target.
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def validate_stage(path, root=None):
    stage = Path(path).absolute()
    if stage.parent.name not in CACHE_DIRECTORIES or not re.fullmatch(r"[a-f0-9]{64}", stage.name):
        raise ValueError("Invalid update staging location")
    for cursor in (stage, stage.parent, stage.parent.parent):
        if linked(cursor):
            raise ValueError("Linked update staging location")
    if root is not None and stage.parent.parent.resolve() != Path(root).resolve():
        raise ValueError("Update stage belongs to another installation")
    return stage


@contextmanager
def cache_lock(cache):
    """Use the installer lease also for download and cleanup, without waiting."""
    cache = Path(cache)
    lock_path = cache / "install.lock"
    if cache.name not in CACHE_DIRECTORIES or linked(cache.parent) or linked(cache) or linked(lock_path):
        raise ValueError("Invalid update cache lease")
    # The stable lease owns its container on the first download as well.
    cache.mkdir(exist_ok=True)
    if linked(cache) or linked(lock_path):
        raise ValueError("Invalid update cache lease")
    directory = None
    if os.name=='nt':handle=lock_path.open('a+b')
    else:
        import stat
        from modules.platform_services.linux_update_files import RootedFiles
        directory=RootedFiles(cache)
        try:
            fd=os.open('install.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,0o600,dir_fd=directory.fd)
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022:
                os.close(fd);raise ValueError('Unsafe Linux update lease')
            handle=os.fdopen(fd,'r+b')
        except BaseException:
            directory.__exit__();raise
    acquired = False
    try:
        handle.seek(0,os.SEEK_END)
        if handle.tell()==0:
            handle.write(b"0");handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        yield acquired
    finally:
        try:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            if directory is not None:directory.__exit__()
