"""Read a bounded external upload selection in the file worker."""
import os
import stat
import time
from pathlib import Path


def inspect_upload_paths(paths, cancel):
    if not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= 10000:
        raise ValueError('请选择 1 至 10000 个本地文件或文件夹。')
    entries = []
    seen = set()
    deadline = time.monotonic() + 30
    for value in paths:
        if cancel.is_set():
            raise InterruptedError('操作已取消。')
        if time.monotonic() >= deadline:
            raise TimeoutError('本地文件检查超时，请检查文件位置后重试。')
        path = Path(value)
        if not path.is_absolute() or not path.name:
            raise ValueError('请拖入文件或文件夹，不支持磁盘根目录或相对路径。')
        key = os.path.normcase(os.path.normpath(str(path)))
        if key in seen:
            continue
        seen.add(key)
        info = path.lstat()
        if (not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('不支持上传链接或特殊文件。')
        entries.append(dict(name=path.name, path=str(path), size=info.st_size,
                            mode=info.st_mode, modified=info.st_mtime,
                            identity=(info.st_mode, info.st_size, info.st_mtime, info.st_uid, info.st_gid)))
    return entries
