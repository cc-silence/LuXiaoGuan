"""Windows UAC 提权工具

主要作用：通过 ShellExecuteExW + runas verb 提权运行外部程序，
弹标准 UAC 盾牌对话框，比 runas /user:Administrator 更符合用户习惯。

此模块是纯平台服务，不依赖 Qt、界面代码或其他业务模块。
"""

import ctypes
import ctypes.wintypes
import os
import subprocess


def _terminate_process_tree(process_handle):
    """超时结束提权宿主及其子进程，避免 DISM/脚本残留在后台。"""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetProcessId.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.GetProcessId.restype = ctypes.wintypes.DWORD
        pid = kernel32.GetProcessId(process_handle)
        if not pid:
            return
        taskkill = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe"
        )
        subprocess.run(
            [taskkill, "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        # 超时清理不能覆盖原始 UAC/更新错误。
        pass


def run_elevated(filepath, args="", timeout_ms=600000, show_window=True, working_directory=None):
    """交互任务按原方式显示；后台控制台通过无窗口宿主提权，避免黑窗和子进程闪窗。"""
    if not show_window:
        from modules.platform_services.elevated_background_command import needs_hidden_host, run_hidden_elevated
        if needs_hidden_host(filepath):
            return run_hidden_elevated(filepath, args, timeout_ms, working_directory, _run_elevated_direct)
    return _run_elevated_direct(filepath, args, timeout_ms, show_window, working_directory)


def _run_elevated_direct(filepath, args="", timeout_ms=600000, show_window=True, working_directory=None):
    """使用 UAC 提权运行外部程序，等待完成后返回退出码。

    通过 ShellExecuteExW + runas verb 实现真正的 UAC 弹窗提权，
    不会像旧代码的 runas /user:Administrator 那样弹密码框。

    参数：
        filepath: 可执行文件路径
        args: 命令行参数
        timeout_ms: 等待超时毫秒数，默认 10 分钟
        show_window: 是否显示被提权进程窗口。后台任务（如防火墙规则）应隐藏窗口。
        working_directory: 子进程工作目录。更新脚本传入用户可写工作区，避免
            相对路径落到 Program Files 下的 WinBox 安装目录。
    返回：
        (True, exit_code) 成功；失败时 (False, -1)
    """
    try:
        class SHELLEXECUTEINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.DWORD),
                ("fMask", ctypes.wintypes.ULONG),
                ("hwnd", ctypes.wintypes.HWND),
                ("lpVerb", ctypes.c_wchar_p),
                ("lpFile", ctypes.c_wchar_p),
                ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p),
                ("nShow", ctypes.c_int),
                ("hInstApp", ctypes.wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.c_wchar_p),
                ("hkeyClass", ctypes.wintypes.HKEY),
                ("dwHotKey", ctypes.wintypes.DWORD),
                ("hMonitor", ctypes.wintypes.HANDLE),
                ("hProcess", ctypes.wintypes.HANDLE),
            ]

        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "runas"
        sei.lpFile = str(filepath)
        sei.lpParameters = str(args)
        sei.lpDirectory = str(working_directory) if working_directory else None
        sei.nShow = 1 if show_window else 0  # SW_SHOWNORMAL / SW_HIDE

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        if not shell32.ShellExecuteExW(ctypes.byref(sei)):
            # 返回真实 Win32 错误码，调用方才能区分 UAC 取消、文件不存在和权限阻止。
            return False, ctypes.get_last_error() or -1

        # 等待进程结束
        wait_result = ctypes.windll.kernel32.WaitForSingleObject(
            sei.hProcess, timeout_ms
        )
        if wait_result == 0x00000102:  # WAIT_TIMEOUT
            _terminate_process_tree(sei.hProcess)
            ctypes.windll.kernel32.TerminateProcess(sei.hProcess, 1)
            ctypes.windll.kernel32.CloseHandle(sei.hProcess)
            return False, 1460  # ERROR_TIMEOUT

        # 获取退出码
        exit_code = ctypes.wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(
            sei.hProcess, ctypes.byref(exit_code)
        )
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)
        return True, exit_code.value
    except Exception:
        return False, ctypes.get_last_error() or -1
