"""优先用原生进程路径识别自身占用，模块查询有总时限，不扫描全系统模块。"""
import csv
import os
import subprocess
import time

from modules.platform_services.process_service import (
    get_process_executable_path, list_processes_by_names,
)
from modules.platform_services.runtime import is_windows, subprocess_creationflags


class FileHolderLookupError(RuntimeError):
    """携带已查到的占用者和未确认的文件，允许上层警告后继续尝试更新。"""

    def __init__(self, message, holders):
        super().__init__(message)
        self.holders = holders


def find_file_holders(file_paths, timeout=5):
    """优先匹配真实进程路径，再按文件名查询模块；全部外部查询共用五秒预算。

    失败明确返回部分结果和异常，不伪装成没有占用；不关闭进程、不跨任务缓存。
    """
    paths = list(dict.fromkeys(str(path) for path in file_paths))
    result = {path: [] for path in paths}
    if not paths or not is_windows():
        return result
    by_name = {}
    for path in paths:
        by_name.setdefault(os.path.basename(path).casefold(), []).append(path)

    candidates = list_processes_by_names([name for name in by_name if name.endswith(".exe")])
    for process in candidates:
        image = get_process_executable_path(process["pid"])
        for path in by_name.get(process["name"].casefold(), ()):
            if image and os.path.normcase(os.path.abspath(image)) != os.path.normcase(os.path.abspath(path)):
                continue
            result[path].append({**process, "own_image": True})

    deadline = time.monotonic() + timeout
    for module, targets in by_name.items():
        # 自身映像的持有者已经明确，关闭后仍有占用时会重新检测其它持有者。
        unresolved = [path for path in targets if not result[path]]
        if not unresolved:
            continue
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("tasklist", timeout)
            output = subprocess.check_output(
                ["tasklist", "/m", module, "/FO", "CSV", "/NH"],
                creationflags=subprocess_creationflags(), timeout=remaining,
                text=True, encoding="utf-8", errors="replace", stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            reason = ("文件占用查询超时，未能确认全部占用情况；将继续尝试更新，"
                      "若确有文件被占用，安装或复制时会明确报错。"
                      if isinstance(exc, subprocess.TimeoutExpired) else
                      "系统文件占用查询失败，未能确认全部占用情况；将继续尝试更新，实际失败会另行提示。")
            raise FileHolderLookupError(reason, result) from exc
        for row in csv.reader(output.splitlines()):
            if len(row) < 2 or not row[1].strip().isdigit():
                continue
            holder = {"name": row[0], "pid": row[1].strip()}
            for path in unresolved:
                result[path].append(dict(holder))
    return result
