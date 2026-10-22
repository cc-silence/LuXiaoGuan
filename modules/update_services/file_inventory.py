"""使用目录枚举自带的文件信息，避免递归扫描时逐文件重复查询属性。"""
import os
from pathlib import Path


def iter_files(directory):
    """流式返回普通文件路径；不进入目录链接，不保存整棵目录树。"""
    pending = [Path(directory)]
    while pending:
        current = pending.pop()
        directories = []
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    directories.append(Path(entry.path))
                elif entry.is_file():
                    yield Path(entry.path)
        pending.extend(reversed(directories))
