"""路由器文件管理执行器，只在后台调用。"""

import os
import posixpath
import stat
import time
from pathlib import Path
from uuid import uuid4
from modules.platform_services.local_file_publish import publish_new_file



class RouterManagement:
    """复用已登录连接，所有写入均需主线程先确认。"""

    def __init__(self, ssh, runner):
        self.ssh, self.runner = ssh, runner

    @staticmethod
    def path(value):
        value = str(value)
        if not value.startswith('/') or '\x00' in value or any(ord(c) < 32 for c in value):
            raise ValueError('路径必须是有效的绝对路径。')
        return posixpath.normpath(value)

    @staticmethod
    def identity(info):
        return (info.st_mode, info.st_size, info.st_mtime, info.st_uid, info.st_gid)

    @staticmethod
    def check(cancel, deadline):
        if cancel.is_set():
            raise InterruptedError('操作已取消。')
        if time.monotonic() >= deadline:
            raise TimeoutError('操作超时。')

    def list_directory(self, path, cancel):
        """有界枚举目录，不跟随符号链接。"""
        path = self.path(path)
        deadline = time.monotonic() + 30
        with self.ssh.transfer(cancel, timeout=30):
            sftp = self.ssh.open_sftp(timeout=10, cancel_event=cancel)
            sftp.get_channel().settimeout(10)
            entries = []
            for item in sftp.listdir_iter(path, read_aheads=1):
                self.check(cancel, deadline)
                if item.filename in ('.', '..') or '/' in item.filename:
                    continue
                if len(entries) >= 10000:
                    raise ValueError('目录超过 10000 项，请打开子目录。')
                if not isinstance(item.st_mode,int) or not isinstance(item.st_size,int):
                    raise ValueError("文件属性不完整，请检查文件传输服务。")
                entries.append(dict(name=item.filename, path=posixpath.join(path, item.filename),
                    size=item.st_size, mode=item.st_mode, modified=item.st_mtime,
                    identity=self.identity(item)))
            return dict(path=path, entries=entries)

    def mutate(self, action, path, cancel, expected=None, value=None):
        """执行单个已确认修改，拒绝排队期间已变更的目标。"""
        path = self.path(path)
        if path == '/':
            raise ValueError('不能修改根目录本身。')
        with self.ssh.transfer(cancel, timeout=30):
            self.check(cancel, time.monotonic()+30)
            sftp = self.ssh.open_sftp(timeout=10, cancel_event=cancel)
            sftp.get_channel().settimeout(10)
            if action == 'mkdir':
                sftp.mkdir(path)
                return path
            info = sftp.lstat(path)
            if expected is None or self.identity(info) != tuple(expected):
                raise ValueError('文件已变化，请刷新后重新确认。')
            if action == 'delete':
                if stat.S_ISDIR(info.st_mode):
                    sftp.rmdir(path)
                else:
                    sftp.remove(path)
            elif action == 'rename':
                destination = self.path(value)
                if posixpath.dirname(destination) != posixpath.dirname(path):
                    raise ValueError('重命名不能移动到其他目录。')
                sftp.rename(path, destination)
            elif action == 'chmod':
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError('不修改符号链接目标权限。')
                mode = int(str(value), 8)
                if not 0 <= mode <= 0o777:
                    raise ValueError('权限范围为 000-777。')
                sftp.chmod(path, mode)
            else:
                raise ValueError('Unknown file action')
            return path

    def transfer_file(self, upload, local, remote, cancel, progress, expected=None, local_expected=None):
        """分块传输到独占暂存文件，完成后再原子发布。"""
        remote = self.path(remote)
        local = Path(local)
        token = uuid4().hex
        temporary = remote + '.winbox-' + token if upload else local.with_name(local.name+'.winbox-'+token)
        deadline = time.monotonic()+3600
        created = False
        with self.ssh.transfer(cancel, timeout=60):
            sftp = self.ssh.open_sftp(timeout=10, cancel_event=cancel)
            sftp.get_channel().settimeout(10)
            try:
                if upload:
                    before = local.stat()
                    if not stat.S_ISREG(before.st_mode) or local.is_symlink():
                        raise ValueError('请选择普通文件上传，不传输符号链接。')
                    source = local.open('rb')
                    try:
                        target = sftp.open(str(temporary), 'wx')
                    except BaseException:
                        source.close()
                        raise
                    total = before.st_size
                else:
                    before = sftp.lstat(remote)
                    if not stat.S_ISREG(before.st_mode):
                        raise ValueError('请选择普通文件下载，不传输符号链接。')
                    if expected is not None and self.identity(before) != tuple(expected):
                        raise ValueError('文件已变化，请刷新后重试。')
                    source = sftp.open(remote, 'rb')
                    try:
                        target = Path(temporary).open('xb')
                    except BaseException:
                        source.close()
                        raise
                    total = before.st_size
                created = True
                completed = 0
                last_report = -1.0
                with source, target:
                    while True:
                        self.check(cancel, deadline)
                        chunk = source.read(128*1024)
                        if not chunk:
                            break
                        target.write(chunk)
                        completed += len(chunk)
                        if completed > total:
                            raise ValueError('传输期间源文件已变化，未替换原文件，请刷新后重试。')
                        now = time.monotonic()
                        if now-last_report >= 0.25 or completed == total:
                            progress(dict(completed=completed, total=total))
                            last_report = now
                    target.flush()
                self.check(cancel, deadline)
                after = local.stat() if upload else sftp.lstat(remote)
                if completed != total or self.identity(before) != self.identity(after):
                    raise ValueError('传输期间源文件已变化，未替换原文件，请刷新后重试。')
                if upload:
                    if expected is not None:
                        current = sftp.lstat(remote)
                        if self.identity(current) != tuple(expected) or not stat.S_ISREG(current.st_mode):
                            raise ValueError('目标文件已变化，未覆盖，请刷新后重试。')
                        sftp.chmod(str(temporary), stat.S_IMODE(current.st_mode))
                        sftp.chown(str(temporary), current.st_uid, current.st_gid)
                        sftp.posix_rename(str(temporary), remote)
                    else:
                        sftp.rename(str(temporary), remote)
                else:
                    if local_expected is not None:
                        current = local.lstat()
                        if not stat.S_ISREG(current.st_mode) or self.identity(current) != tuple(local_expected):
                            raise ValueError('本机目标文件已变化，未覆盖，请刷新后重试。')
                        os.replace(temporary, local)
                    else:
                        publish_new_file(temporary, local)
                created = False
                return str(local if not upload else remote)
            finally:
                if created:
                    try:
                        sftp.remove(str(temporary)) if upload else Path(temporary).unlink()
                    except OSError as exc:
                        # Leave only the uniquely named staging file; never remove the destination.
                        from modules.capture_services.capture_logging import LOGGER
                        LOGGER.warning('Transfer staging cleanup failed type=%s', type(exc).__name__)

