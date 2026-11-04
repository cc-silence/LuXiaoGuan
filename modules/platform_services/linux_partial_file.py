"""Private partial-download files anchored to the verified Linux stage dirfd."""
from contextlib import contextmanager
import os
import stat


class RootedPartial:
    def __init__(self, files, name):
        self.files, self.name = files, name
        self.expected = None

    def _validate(self, info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('Invalid private partial download')
        return info

    def lstat(self):
        with self.files.parent(self.name) as (fd, name):
            info = self._validate(os.stat(name, dir_fd=fd, follow_symlinks=False))
        self.expected = (info.st_dev, info.st_ino, info.st_size)
        return info

    def is_symlink(self):
        self.lstat()
        return False

    @contextmanager
    def open(self, mode):
        if mode not in {'rb', 'wb', 'ab'}:
            raise ValueError('Invalid partial file mode')
        flags = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        flags |= os.O_RDONLY if mode == 'rb' else os.O_RDWR | os.O_CREAT
        with self.files.parent(self.name) as (parent, name):
            fd = os.open(name, flags, 0o600, dir_fd=parent)
            try:
                info = self._validate(os.fstat(fd))
                identity = (info.st_dev, info.st_ino, info.st_size)
                if self.expected is not None and identity != self.expected:
                    raise ValueError('Partial download changed before opening')
                if mode == 'wb':os.ftruncate(fd, 0)
                elif mode == 'ab':os.lseek(fd, 0, os.SEEK_END)
                stream = os.fdopen(fd, 'rb' if mode == 'rb' else 'wb')
                fd = None
                with stream:
                    try:yield stream
                    finally:
                        if not stream.closed and mode != 'rb':
                            stream.flush();os.fsync(stream.fileno());os.fsync(parent)
                            info = os.fstat(stream.fileno())
                            self.expected = (info.st_dev, info.st_ino, info.st_size)
            finally:
                if fd is not None:os.close(fd)

    def unlink(self, missing_ok=False):
        try:self.lstat()
        except FileNotFoundError:
            if missing_ok:return
            raise
        self.files.unlink(self.name)
        self.expected = None
