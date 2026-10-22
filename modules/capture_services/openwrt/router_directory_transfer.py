"""文件夹有界复制，复用原子文件传输并在全部完成后发布。"""
import errno
import os
import posixpath
import stat
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

MAX_ENTRIES = 10000
MAX_DEPTH = 64


def _local_info(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('不传输链接或重解析点：' + str(path))
    return info


def _name(name, windows=False):
    if not name or name in {'.', '..'} or '/' in name or chr(92) in name or any(ord(c) < 32 for c in name):
        raise ValueError('文件夹内含非法名称：' + repr(name))
    if windows:
        reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {'COM'+str(i) for i in range(1,10)} | {'LPT'+str(i) for i in range(1,10)}
        if any(c in name for c in '<>:"|?*') or name.endswith((' ', '.')) or name.split('.')[0].upper() in reserved:
            raise ValueError('路由器文件名不能保存到 Windows：' + name)
    return name


def transfer_directory(manager, upload, local, remote, cancel, progress, expected=None):
    """写入前检查完整目录；失败仅清理本次登记的暂存项，保留旧目标。"""
    local = Path(local).absolute()
    remote = manager.path(remote)
    connection, fingerprint = manager.ssh._client, manager.ssh.host_fingerprint
    deadline = time.monotonic()+3600
    files, folders = [], []
    identities = {}
    seen = set()

    def check():
        manager.check(cancel, deadline)
        if not manager.ssh.connected or manager.ssh._client is not connection or manager.ssh.host_fingerprint != fingerprint:
            raise ConnectionError('路由器连接已断开或更换，请重新登录。')

    def remote_call(callback, cleanup=False):
        if not cleanup: check()
        event = Event() if cleanup else cancel
        with manager.ssh.transfer(event, timeout=30):
            if manager.ssh._client is not connection or not manager.ssh.connected:
                raise ConnectionError('原路由器连接已不可用，请重新登录。')
            sftp=manager.ssh.open_sftp(timeout=10,cancel_event=event)
            sftp.get_channel().settimeout(10)
            result=callback(sftp)
            if not cleanup:check()
            return result

    def assert_remote_parents(path):
        current='/'
        for part in path.strip('/').split('/'):
            if not part:continue
            current=posixpath.join(current,part)
            info=remote_call(lambda fs,p=current:fs.lstat(p))
            if not stat.S_ISDIR(info.st_mode):raise ValueError('路径中含链接或非文件夹：'+current)

    def missing_remote(path):
        try: remote_call(lambda fs:fs.lstat(path))
        except OSError as exc:
            if isinstance(exc,FileNotFoundError) or exc.errno==errno.ENOENT:return
            raise
        raise FileExistsError('同名目标文件夹已存在，请换目录或重命名：'+path)

    def add(relative, info, directory):
        if len(files)+len(folders)>=MAX_ENTRIES:raise ValueError('文件夹超过 10000 项，请分批传输子文件夹。')
        key=relative if upload else relative.casefold()
        if key in seen:raise ValueError('目标存在同名冲突：'+relative)
        seen.add(key);identities[relative]=manager.identity(info)
        (folders if directory else files).append(relative)

    check();progress(dict(completed=0,total=0,stage='正在检查文件夹内容…'))
    if upload:
        before=_local_info(local)
        if not stat.S_ISDIR(before.st_mode):raise ValueError('上传来源不是文件夹。')
        if expected is not None and manager.identity(before)!=tuple(expected):raise ValueError('文件夹已变化，请刷新后重试。')
        assert_remote_parents(posixpath.dirname(remote));missing_remote(remote)
        stack=[(local,'',0)]
        while stack:
            folder,relative,depth=stack.pop();check()
            if depth>MAX_DEPTH:raise ValueError('文件夹嵌套超过 64 层，请分批传输。')
            with os.scandir(folder) as entries:
                for child in entries:
                    check();name=_name(child.name)
                    path=Path(child.path);info=_local_info(path)
                    rel=posixpath.join(relative,name)
                    directory=stat.S_ISDIR(info.st_mode)
                    if not directory and not stat.S_ISREG(info.st_mode):raise ValueError('不传输特殊文件：'+str(path))
                    add(rel,info,directory)
                    if directory:stack.append((path,rel,depth+1))
    else:
        assert_remote_parents(remote)
        before=remote_call(lambda fs:fs.lstat(remote))
        if expected is not None and manager.identity(before)!=tuple(expected):raise ValueError('文件夹已变化，请刷新后重试。')
        # Check every existing local ancestor before creating staging contents.
        for parent in (local.parent,*local.parent.parents):
            if not stat.S_ISDIR(_local_info(parent).st_mode):raise ValueError('本地目标路径中含链接或非文件夹。')
        if local.exists() or local.is_symlink():raise FileExistsError('同名目标文件夹已存在，请换目录或重命名：'+str(local))
        stack=[(remote,'',0)]
        while stack:
            folder,relative,depth=stack.pop();check()
            if depth>MAX_DEPTH:raise ValueError('文件夹嵌套超过 64 层，请分批传输。')
            value=manager.list_directory(folder,cancel);check()
            for entry in value['entries']:
                name=_name(entry['name'],windows=True);rel=posixpath.join(relative,name)
                path=posixpath.join(folder,name)
                info=remote_call(lambda fs,p=path:fs.lstat(p))
                directory=stat.S_ISDIR(info.st_mode)
                if not directory and not stat.S_ISREG(info.st_mode):raise ValueError('不传输路由器链接或特殊文件：'+path)
                add(rel,info,directory)
                if directory:stack.append((path,rel,depth+1))
    check()
    total=sum(identities[rel][1] for rel in files)
    done=0
    token=uuid4().hex
    staging=remote+'.winbox-'+token if upload else local.with_name(local.name+'.winbox-'+token)
    made_dirs=[];made_files=[]

    def stage_path(rel):return posixpath.join(staging,rel) if upload else Path(staging).joinpath(*rel.split('/'))
    def cleanup():
        end=time.monotonic()+30
        for path in reversed(made_files):
            if time.monotonic()>=end:raise TimeoutError('暂存目录清理超时。')
            remote_call(lambda fs,p=path:fs.remove(p),True) if upload else Path(path).unlink()
        for path in reversed(made_dirs):
            if time.monotonic()>=end:raise TimeoutError('暂存目录清理超时。')
            remote_call(lambda fs,p=path:fs.rmdir(p),True) if upload else Path(path).rmdir()

    try:
        remote_call(lambda fs:fs.mkdir(staging)) if upload else Path(staging).mkdir()
        made_dirs.append(staging)
        for rel in sorted(folders,key=lambda rel:rel.count('/')):
            check();path=stage_path(rel)
            remote_call(lambda fs,p=path:fs.mkdir(p)) if upload else Path(path).mkdir()
            made_dirs.append(path)
        for index,rel in enumerate(files):
            check()
            source=local.joinpath(*rel.split('/')) if upload else posixpath.join(remote,rel)
            source_info=_local_info(source) if upload else remote_call(lambda fs,p=source:fs.lstat(p))
            if manager.identity(source_info)!=identities[rel]:raise ValueError('传输时来源已变化，未发布目标：'+rel)
            def report(value):
                check();progress(dict(completed=done+int(value['completed']),total=total,name=rel,stage='正在传输：'+rel))
            value=manager.transfer_file(upload,source if upload else stage_path(rel),stage_path(rel) if upload else source,
                cancel,report,expected=None if upload else identities[rel])
            made_files.append(value);done+=identities[rel][1]
        check()
        after=_local_info(local) if upload else remote_call(lambda fs:fs.lstat(remote))
        if manager.identity(after)!=manager.identity(before):raise ValueError('传输时文件夹已变化，请刷新后重试。')
        # Reject concurrent changes to a source file already copied.
        for rel in files+folders:
            check();path=local.joinpath(*rel.split('/')) if upload else posixpath.join(remote,rel)
            info=_local_info(path) if upload else remote_call(lambda fs,p=path:fs.lstat(p))
            if manager.identity(info)!=identities[rel]:raise ValueError('传输时来源已变化，未发布目标：'+rel)
        if upload:
            missing_remote(remote);remote_call(lambda fs:fs.rename(staging,remote))
        else:
            if local.exists() or local.is_symlink():raise FileExistsError('传输时目标目录已被其他操作创建，原内容已保留。')
            os.rename(staging,local)
        made_dirs.clear();made_files.clear()
        progress(dict(completed=total,total=total,stage='文件夹传输完成'))
        return str(remote if upload else local)
    except BaseException as exc:
        try:cleanup()
        except Exception as cleanup_error:
            raise RuntimeError(str(exc)+'；暂存清理未完成，残留位置：'+str(staging)+' ('+type(cleanup_error).__name__+')') from exc
        raise
