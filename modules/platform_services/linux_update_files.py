"""Directory-fd Linux update storage. No path component may follow a link."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import uuid
from .runtime import is_linux
from .local_rename import rename_without_replace
from modules.app_update_services.manifest import canonical


class RootedFiles:
    def __init__(self, root):
        if not is_linux(): raise OSError('Directory-fd update storage requires Linux')
        self.root=Path(os.path.abspath(root))
        fd=os.open(self.root.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            for name in self.root.parts[1:]:
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                os.close(fd);fd=child
        except BaseException:
            os.close(fd);raise
        self.fd=fd

    def __enter__(self): return self
    def __exit__(self,*_): os.close(self.fd)

    @property
    def identity(self):
        info=os.fstat(self.fd);return [info.st_dev,info.st_ino]

    @contextmanager
    def parent(self, relative, create=False):
        if not isinstance(relative,str):raise ValueError('Invalid update path')
        parts=relative.split('/')
        if (not relative or len(relative)>512
                or any(p in {'','.', '..'} or '\\' in p or any(ord(c)<32 for c in p) for p in parts)):
            raise ValueError('Unsafe directory-relative update path')
        fd=os.dup(self.fd)
        try:
            for name in parts[:-1]:
                try: child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                except FileNotFoundError:
                    if not create: raise
                    try: os.mkdir(name,0o755,dir_fd=fd);os.fsync(fd)
                    except FileExistsError: pass
                    child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                os.close(fd);fd=child
            yield fd,parts[-1]
        finally: os.close(fd)

    @contextmanager
    def open_read(self, relative):
        with self.parent(relative) as (parent,name):
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=parent)
            with os.fdopen(fd,'rb') as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError('Update source is not a regular file')
                yield source

    def state(self, relative, check=lambda:None, *, max_bytes=2*1024**3):
        check()
        try:
            with self.parent(relative) as (parent,name):
                info=os.stat(name,dir_fd=parent,follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    target=os.readlink(name,dir_fd=parent)
                    if len(target)>512: raise ValueError('Excessive update link')
                    value=dict(kind='symlink',target=target)
                elif stat.S_ISREG(info.st_mode):
                    if info.st_size>max_bytes: raise ValueError('Update member exceeds limit')
                    digest=hashlib.sha256();size=0
                    with self.open_read(relative) as source:
                        before=os.fstat(source.fileno())
                        for block in iter(lambda:source.read(1024*1024),b''):
                            check();size+=len(block)
                            if size>max_bytes or size>info.st_size:raise ValueError('Update file grew while reading')
                            digest.update(block)
                        after=os.fstat(source.fileno())
                        if _identity(before)!=_identity(info) or _identity(after)!=_identity(info):
                            raise ValueError('Update file changed while reading')
                    value=dict(kind='file',sha256=digest.hexdigest(),size=info.st_size,mode=stat.S_IMODE(info.st_mode))
                else: raise ValueError('Special update file or directory')
                if _identity(os.stat(name,dir_fd=parent,follow_symlinks=False))!=_identity(info):
                    raise ValueError('Update target changed during inspection')
                return value
        except FileNotFoundError: return None

    def read_bytes(self, relative, limit):
        with self.open_read(relative) as source:
            if os.fstat(source.fileno()).st_size>limit: raise ValueError('Update record exceeds limit')
            data=source.read(limit+1)
            if len(data)>limit: raise ValueError('Update record exceeds limit')
            return data

    def create(self, relative, state, source=None, check=lambda:None):
        with self.parent(relative,create=True) as (parent,name):
            if state['kind']=='symlink':
                check();os.symlink(state['target'],name,dir_fd=parent);os.fsync(parent);return
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
            created=os.fstat(fd)
            try:
                size=0;digest=hashlib.sha256()
                with os.fdopen(fd,'wb') as output:
                    for block in iter(lambda:source.read(1024*1024),b''):
                        check();size+=len(block)
                        if size>state['size']:raise ValueError('Staged update source exceeds expected size')
                        digest.update(block);output.write(block)
                    if size!=state['size'] or digest.hexdigest()!=state['sha256']:
                        raise ValueError('Staged update source differs from inventory')
                    os.fchmod(output.fileno(),state['mode']);output.flush();os.fsync(output.fileno())
                os.fsync(parent)
            except BaseException:
                # Only this still-owned incomplete temporary inode may be removed.
                try:current=os.stat(name,dir_fd=parent,follow_symlinks=False)
                except FileNotFoundError:current=None
                if current is not None and (current.st_dev,current.st_ino)==(created.st_dev,created.st_ino):
                    os.unlink(name,dir_fd=parent);os.fsync(parent)
                raise

    def rename(self, source, destination):
        if PurePosixPath(source).parent!=PurePosixPath(destination).parent:
            raise ValueError('Update parking must remain in the target directory')
        with self.parent(source) as (fd,name):
            rename_without_replace(name,PurePosixPath(destination).name,source_dir_fd=fd,destination_dir_fd=fd)
            os.fsync(fd)

    def unlink(self, relative):
        with self.parent(relative) as (fd,name): os.unlink(name,dir_fd=fd);os.fsync(fd)

    def list_names(self,limit=24000):
        names=[]
        with os.scandir(self.fd) as entries:
            for entry in entries:
                if len(names)>=limit:raise ValueError('Excessive update directory entries')
                names.append(entry.name)
        return names

    def rmdir(self, relative):
        with self.parent(relative) as (fd,name):os.rmdir(name,dir_fd=fd);os.fsync(fd)

    def flush(self, relative):
        with self.parent(relative) as (fd,name):
            info=os.stat(name,dir_fd=fd,follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                with self.open_read(relative) as source:os.fsync(source.fileno())
            elif not stat.S_ISLNK(info.st_mode):raise ValueError('Special original update file')

    def write_json(self, relative, value, limit=32*1024*1024):
        return self.write_bytes(relative,canonical(value),limit)

    def write_bytes(self, relative, data, limit=32*1024*1024):
        if len(data)>limit: raise ValueError('Update journal exceeds limit')
        temporary=str(PurePosixPath(relative).with_name('.record-'+uuid.uuid4().hex))
        state=dict(kind='file',mode=0o600,size=len(data),sha256=hashlib.sha256(data).hexdigest())
        try:
            self.create(temporary,state,io.BytesIO(data))
            with self.parent(relative) as (fd,name):
                try:existing=os.stat(name,dir_fd=fd,follow_symlinks=False)
                except FileNotFoundError:existing=None
                if existing is not None and not stat.S_ISREG(existing.st_mode):raise ValueError('Linked update record')
                os.replace(PurePosixPath(temporary).name,name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
        finally:
            if self.state(temporary)==state:self.unlink(temporary)

    def read_json(self, relative, limit=32*1024*1024):
        return json.loads(self.read_bytes(relative,limit))


def _identity(info):
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_mode
