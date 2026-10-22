"""提权进程关闭服务。

主要作用：以管理员权限批量结束更新预检确认过的进程，同时不弹出 cmd 控制台窗口。

为什么不用 cmd.exe 提权：更新预检关闭受保护进程时，旧做法是 ShellExecuteExW + runas
提权执行 "cmd.exe /c taskkill ..."。cmd.exe 是控制台程序，ShellExecuteExW 的
SW_HIDE 不会阻止新建控制台窗口，因此每次关进程都会闪一个黑色 cmd 窗口。
本模块改为提权启动 WinBox.exe 本体（windowed，无控制台）隐藏入口执行同样的
taskkill，界面无任何弹窗。

调用约定：普通进程使用 run_elevated_pid_kill()；隐藏管理员进程使用
run_kill_request_file()。本模块不依赖 Qt，也不创建任何界面控件。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from modules.platform_services.process_service import kill_process_by_pid
from modules.platform_services.update_elevation import run_elevated_update as run_elevated
from modules.platform_services.runtime import windowless_worker_executable


KILL_PROCESSES_ARG = "--kill-processes"


def run_elevated_pid_kill(pids: list) -> tuple[bool, int]:
    """通过共享管理员授权启动隐藏入口批量结束进程，返回是否启动成功。

    参数：pids 是更新预检阶段已列出并经用户确认的进程 ID 列表。
    返回值：(成功结束进程数信息)。首个值是是否成功启动提权入口并完成操作；
    后续真实关闭结果仍由调用方重新枚举进程确认，不依赖退出码。
    副作用：首次需要时申请 UAC，后续复用授权；提权进程以管理员权限强制结束这些 PID。
    """

    normalized = []
    for value in pids:
        try:
            pid = int(value)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in normalized:
            normalized.append(pid)
    if not normalized:
        return False, -1
    return _launch_hidden_worker(normalized)


def run_kill_request_file(argv: list[str]) -> int:
    """隐藏管理员入口：读取请求 JSON，执行杀进程并写结果 JSON。

    参数：argv 是进程命令行，必须包含 KILL_PROCESSES_ARG 及随后的请求、结果路径。
    返回值：0 表示成功写结果；异常时返回 1 并写入错误信息。
    副作用：会结束请求中列出的进程；不加载 Qt，不创建任何窗口。
    """

    request_path = Path(argv[argv.index(KILL_PROCESSES_ARG) + 1])
    result_path = Path(argv[argv.index(KILL_PROCESSES_ARG) + 2])
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        pids = [int(value) for value in request.get("pids", []) if str(value).strip().isdigit()]
        killed = 0
        for pid in pids:
            if kill_process_by_pid(pid):
                killed += 1
        _write_json(result_path, {"success": True, "killed": killed, "total": len(pids)})
        return 0
    except Exception as exc:
        try:
            _write_json(result_path, {"success": False, "error": str(exc)})
        except Exception:
            pass
        return 1


def _launch_hidden_worker(pids: list[int]) -> tuple[bool, int]:
    """写入请求/结果 JSON 并提权启动 WinBox.exe 隐藏入口。

    参数：pids 是已归一化的进程 ID 列表。
    返回值：(启动并完成返回 True 且退出码 0，否则 False, 退出码)。
    副作用：首次需要时申请 UAC，后续复用同一授权。
    """

    executable = windowless_worker_executable()
    temp_root = Path(tempfile.mkdtemp(prefix="winbox-kill-"))
    request_path = temp_root / "request.json"
    result_path = temp_root / "result.json"
    _write_json(request_path, {"pids": pids})
    if getattr(sys, "frozen", False):
        # 打包后提权运行 windowed 的 WinBox.exe 本体，无控制台、不弹 cmd 窗口。
        arguments = subprocess.list2cmdline([KILL_PROCESSES_ARG, str(request_path), str(result_path)])
        workdir = executable.parent
    else:
        # 源码也使用无控制台宿主，进入同一 main.py 后台分支。
        main_path = Path(__file__).resolve().parents[2] / "main.py"
        arguments = subprocess.list2cmdline(
            [str(main_path), KILL_PROCESSES_ARG, str(request_path), str(result_path)]
        )
        workdir = main_path.parent
    try:
        launched, exit_code = run_elevated(
            executable,
            arguments,
            timeout_ms=60000,
            show_window=False,
            working_directory=workdir,
        )
        if not launched:
            return False, exit_code
        if not result_path.is_file():
            return False, exit_code
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not result.get("success") or exit_code != 0:
            return False, exit_code
        return True, 0
    finally:
        for path in (request_path, result_path):
            path.unlink(missing_ok=True)
        try:
            temp_root.rmdir()
        except OSError:
            pass


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")