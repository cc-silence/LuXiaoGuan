"""运行平台基础工具。

主要作用：集中判断 WinBox 当前运行在哪个操作系统，并提供跨模块复用的
子进程启动参数。输入参数通常为空；返回值是平台名称、布尔判断或
``subprocess`` 可直接使用的参数值。不会修改文件、界面状态或配置数据。

注意：当前项目实际只实现 Windows 功能，本模块只是先把平台判断入口固定下来，
后续扩展 Linux 时优先在这里补充公共规则，而不是在业务代码里散落判断。
"""

import sys
import subprocess
from pathlib import Path
from functools import lru_cache


WINDOWS = "windows"
LINUX = "linux"
MACOS = "macos"
UNKNOWN = "unknown"


@lru_cache(maxsize=1)
def current_os():
    """返回当前运行系统名称。

    输入参数：无。
    返回值：``windows``、``linux``、``macos`` 或 ``unknown``。
    副作用：无。

    这里统一使用小写稳定值，避免不同模块各自判断 ``sys.platform`` 或
    ``platform.system()`` 时写出不一致的字符串。
    """

    # sys.platform 是解释器自带常量，不会像 platform.system() 间接启动 ver/CMD。
    name = sys.platform
    if name == "win32":
        return WINDOWS
    if name.startswith("linux"):
        return LINUX
    if name == "darwin":
        return MACOS
    return UNKNOWN


def is_windows():
    """判断 WinBox 当前是否运行在 Windows 上。无副作用。"""

    return current_os() == WINDOWS


def is_linux():
    """判断 WinBox 当前是否运行在 Linux 上。无副作用。"""

    return current_os() == LINUX


def subprocess_creationflags():
    """返回启动子进程时应使用的 ``creationflags``。

    输入参数：无。
    返回值：Windows 下优先返回 ``subprocess.CREATE_NO_WINDOW``，其他系统
    或不支持该常量时返回 0。
    副作用：无。

    这个函数用于收口 Windows 隐藏控制台窗口的细节。业务代码只关心
    “按当前平台安全启动子进程”，不要直接散落 ``CREATE_NO_WINDOW`` 判断。
    """

    if is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return subprocess.CREATE_NO_WINDOW
    return 0


def windowless_worker_executable(*, direct_process=False):
    """返回后台管理员入口的无控制台宿主路径，不启动进程。

    源码选同环境 pythonw.exe，发布版保留 GUI 主程序。
    缺少宿主时报错，不回退到会弹黑窗的控制台解释器。
    """
    executable = Path(sys.executable)
    if not is_windows() or getattr(sys, "frozen", False):
        return executable
    if direct_process:
        # Windows venv executables are redirectors; PID-bound workers must start the real image.
        executable = Path(getattr(sys, '_base_executable', sys.executable))
    executable = executable.with_name("pythonw.exe")
    if not executable.is_file():
        raise FileNotFoundError(f"缺少后台解释器：{executable}。请修复 Python 安装环境后重试。")
    return executable


def background_script_command(command):
    """为后台脚本选择可继承控制台的宿主，返回命令副本。

    源码从 pythonw 启动时改用同环境 python.exe；冻结宿主和 BAT 不变。
    本函数不启动进程，必须与 background_script_options 配对使用。
    """
    result = [str(value) for value in command]
    if is_windows() and result and Path(result[0]).name.lower() == "pythonw.exe":
        console_python = Path(result[0]).with_name("python.exe")
        if not console_python.is_file():
            raise FileNotFoundError(f"缺少后台脚本解释器：{console_python}。请修复 Python 环境。")
        result[0] = str(console_python)
    return result


def background_script_options():
    """返回后台检测脚本的隐藏控制台启动参数。

    进程从创建时就隐藏，普通子进程继承该控制台，无需脚本逐个补参数。
    CREATE_NO_WINDOW 会去掉控制台，不能替代这个继承契约；不影响安装更新或 GUI 主进程。
    """
    if not is_windows():
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": startup}
