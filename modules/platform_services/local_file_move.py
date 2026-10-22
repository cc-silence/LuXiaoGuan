"""本地栏内移动；Windows rename 拒绝覆盖。"""
import os
import stat
from pathlib import Path
from .file_metadata import same_local_path


def move_local(path, parent, folder, expected, folder_expected, cancel):
    """只移动当前目录的直接子项到同级文件夹；拒绝链接、覆盖和自身移动。"""
    source, root, target = Path(path), Path(parent), Path(folder)
    if (not all(value.is_absolute() for value in (source,root,target))
            or not same_local_path(source.parent,root) or not same_local_path(target.parent,root)
            or same_local_path(source,target)):
        raise ValueError('移动目标必须是当前目录内的另一个文件夹。')
    for value in (source,target,*source.parents):
        info=value.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('不移动链接或重解析点，请使用系统资源管理器。')
    before=source.lstat();destination=target/source.name;info=target.lstat()
    identity=(before.st_mode,before.st_size,before.st_mtime,before.st_uid,before.st_gid)
    if expected is None or tuple(expected)!=identity:
        raise ValueError('来源已变化，请刷新后重新移动。')
    if not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)):
        raise ValueError('只支持普通文件和文件夹。')
    if (not stat.S_ISDIR(info.st_mode) or folder_expected is None
            or (info.st_mode,info.st_uid,info.st_gid)!=(folder_expected[0],folder_expected[3],folder_expected[4])):
        raise ValueError('目标文件夹已变化，请刷新后重试。')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('目标已有同名项目，未覆盖：'+str(destination))
    if cancel.is_set():raise InterruptedError('操作已取消。')
    # Windows rename 不覆盖已有项目；不回退为复制后删除。
    os.rename(source,destination)
    return str(destination)


