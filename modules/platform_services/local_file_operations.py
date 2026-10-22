"""本机回收站操作；后台调用，拒绝不明确的目标，不回退为永久删除。"""
import stat
from pathlib import Path, PureWindowsPath
from .windows_recycle_bin import move_to_recycle_bin
from .file_metadata import same_local_path


def recycle_local_entry(path, parent, expected, cancel):
    """核实已确认目录内的单个文件或文件夹，交给系统回收站处理。"""
    target=Path(path)
    folder=Path(parent)
    if not target.is_absolute() or not folder.is_absolute() or target.parent==target:
        raise ValueError('删除目标必须是目录内的绝对路径，不能删除磁盘根目录。')
    if not same_local_path(target.parent,folder):
        raise ValueError('删除目标不在已确认目录内，请刷新后重试。')
    if any(part in {'.','..'} or part.endswith((' ','.')) for part in target.parts[1:]):
        raise ValueError('删除路径不明确，请刷新后重试。')
    # 不跟随链接或重解析点，避免所见目录与实际操作对象不同。
    for current in (target,*target.parents):
        info=current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('链接或重解析点暂不支持删除，请使用系统资源管理器。')
    info=target.lstat()
    identity=(info.st_mode,info.st_size,info.st_mtime,info.st_uid,info.st_gid)
    if expected is None or tuple(expected)!=identity:
        raise ValueError('文件已发生变化，请刷新后重新确认删除。')
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError('只支持普通文件或文件夹。')
    if cancel.is_set():
        raise InterruptedError('操作已取消。')
    success=move_to_recycle_bin(str(target),cancel)
    if not success:
        raise OSError('无法将文件移到回收站，请检查权限、文件占用和回收站设置；未尝试永久删除。')
    return str(target)


def create_local_directory(parent, name, cancel):
    """在明确的本地目录中新建单个文件夹；不覆盖、不递归创建路径。"""
    folder=Path(parent)
    if not folder.is_absolute():
        raise ValueError('父目录必须是绝对路径，请刷新后重试。')
    if (not isinstance(name,str) or not name or name in {'.','..'}
            or any(ord(c)<32 or c in '<>:"/\\|?*' for c in name)
            or name.endswith((' ','.')) or PureWindowsPath(name).is_reserved()):
        raise ValueError('文件夹名称无效，请勿使用路径、系统保留名称或特殊字符。')
    for current in (folder,*folder.parents):
        info=current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('链接或重解析点内暂不支持新建，请使用系统资源管理器。')
    if not folder.is_dir():
        raise ValueError('父目录不存在或不是文件夹，请刷新后重试。')
    if cancel.is_set():
        raise InterruptedError('操作已取消。')
    target=folder/name
    target.mkdir()
    return str(target)
