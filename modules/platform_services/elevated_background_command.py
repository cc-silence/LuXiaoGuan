"""后台提权命令先启动无窗口宿主，再建立可由普通子进程继承的隐藏控制台。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from modules.platform_services.runtime import (
    windowless_worker_executable, background_script_command, background_script_options,
)

WORKER_ARG = "--hidden-command-worker"
_CONSOLE_NAMES = {"cmd.exe", "powershell.exe", "pwsh.exe", "python.exe",
                  "pythonw.exe", "winboxscripthost.exe"}


def needs_hidden_host(filepath):
    """只包装已知控制台宿主；正常安装器界面和可见交互脚本不受影响。"""
    return Path(filepath).name.casefold() in _CONSOLE_NAMES


def run_hidden_elevated(filepath, args, timeout_ms, working_directory, launch):
    """复用原 UAC 启动器提权无窗口宿主，真实命令和退出码通过独立请求传递。"""
    with tempfile.TemporaryDirectory(prefix="winbox-command-") as directory:
        request = Path(directory) / "request.json"
        result = Path(directory) / "result.json"
        request.write_text(json.dumps({
            "file": str(filepath), "args": str(args),
            "directory": str(working_directory) if working_directory else None,
            "timeout_ms": timeout_ms,
        }), encoding="utf-8")
        executable = windowless_worker_executable()
        command = [WORKER_ARG, str(request), str(result)]
        workdir = executable.parent
        if not getattr(sys, "frozen", False):
            main = Path(__file__).resolve().parents[2] / "main.py"
            command.insert(0, str(main))
            workdir = main.parent
        started, code = launch(str(executable), subprocess.list2cmdline(command),
                               timeout_ms=timeout_ms, show_window=False,
                               working_directory=workdir)
        if not started:
            return False, code
        try:
            data = json.loads(result.read_text(encoding="utf-8"))
            return bool(data["started"]), int(data["code"])
        except (OSError, ValueError, KeyError):
            return False, code or 1


def run_worker(argv):
    """隐藏入口只运行已授权的命令，不初始化 Qt；超时回收自己创建的进程树。"""
    index = argv.index(WORKER_ARG)
    result = Path(argv[index + 2])
    process = None
    try:
        data = json.loads(Path(argv[index + 1]).read_text(encoding="utf-8"))
        executable = background_script_command([data["file"]])[0]
        command = subprocess.list2cmdline([executable]) + " " + data["args"]
        process = subprocess.Popen(
            command, cwd=data["directory"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **background_script_options(),
        )
        try:
            code = process.wait(timeout=max(0.001, data["timeout_ms"] / 1000))
        except subprocess.TimeoutExpired:
            from modules.platform_services.windows_uac import _terminate_process_tree
            _terminate_process_tree(process._handle)
            process.wait(timeout=5)
            code = 1460
        payload = {"started": True, "code": code}
    except Exception as exc:
        payload = {"started": False, "code": 1, "error": str(exc)}
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    result.write_text(json.dumps(payload), encoding="utf-8")
    return 0
