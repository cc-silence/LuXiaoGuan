"""栏内文件移动，复用任务取消、连接绑定和批量部分结果。"""
import posixpath
import stat
import time
from modules.platform_services.local_file_move import move_local


def move_remote(manager, path, parent, folder, expected, folder_expected, cancel):
    """通过标准 SFTP rename 移动，不使用会覆盖目标的 posix_rename。"""
    source,root,target=map(manager.path,(path,parent,folder))
    if posixpath.dirname(source)!=root or posixpath.dirname(target)!=root or source==target:
        raise ValueError('移动目标必须是当前目录内的另一个文件夹。')
    with manager.ssh.transfer(cancel,timeout=30):
        manager.check(cancel,time.monotonic()+30)
        sftp=manager.ssh.open_sftp(timeout=10,cancel_event=cancel)
        sftp.get_channel().settimeout(10)
        current=target
        while True:
            info=sftp.lstat(current)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('目标路径含链接或不是文件夹。')
            if current=='/':break
            current=posixpath.dirname(current)
        info=sftp.lstat(target)
        if (folder_expected is None or (info.st_mode,info.st_uid,info.st_gid)
                !=(folder_expected[0],folder_expected[3],folder_expected[4])):
            raise ValueError('目标文件夹已变化，请刷新后重试。')
        before=sftp.lstat(source)
        if expected is None or manager.identity(before)!=tuple(expected):
            raise ValueError('来源已变化，请刷新后重新移动。')
        if not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)):
            raise ValueError('不移动链接或特殊文件。')
        destination=posixpath.join(target,posixpath.basename(source))
        try:sftp.lstat(destination)
        except OSError as exc:
            if not isinstance(exc,FileNotFoundError) and getattr(exc,'errno',None)!=2:raise
        else:raise FileExistsError('目标已有同名项目，未覆盖：'+destination)
        manager.check(cancel,time.monotonic()+30)
        sftp.rename(source,destination)
        return destination
