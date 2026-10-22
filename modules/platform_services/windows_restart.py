"""Windows 重启状态检测与用户确认后的重启执行服务。"""

from __future__ import annotations

import os
import subprocess

try:
    import winreg
except ImportError:  # 非 Windows 环境保留空实现，避免业务页面散落平台判断。
    winreg = None


_PENDING_REBOOT_KEYS = (
    (winreg.HKEY_LOCAL_MACHINE if winreg else None,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
     "Windows 组件安装"),
    (winreg.HKEY_LOCAL_MACHINE if winreg else None,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
     "Windows 更新"),
)
_PENDING_FILE_RENAME_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager"


def pending_reboot_state() -> dict[str, object]:
    """读取当前 Windows 待重启注册表状态快照。

    返回值是可在更新前后比较的字典；非 Windows 或注册表无法访问时返回空状态。
    副作用：只读取系统注册表，不会清除待重启标记，也不会重启系统。
    """

    if os.name != "nt" or winreg is None:
        return {}
    state = {}
    for root, key_path, reason in _PENDING_REBOOT_KEYS:
        try:
            with winreg.OpenKey(root, key_path):
                state[reason] = True
        except FileNotFoundError:
            state[reason] = False
        except OSError:
            state[reason] = False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _PENDING_FILE_RENAME_KEY) as key:
            pending, _ = winreg.QueryValueEx(key, "PendingFileRenameOperations")
            state["待替换系统文件"] = tuple(str(item) for item in pending) if pending else ()
    except FileNotFoundError:
        state["待替换系统文件"] = ()
    except OSError:
        state["待替换系统文件"] = ()
    return state


def pending_reboot_reasons(previous_state: dict[str, object] | None = None) -> list[str]:
    """返回本次操作新产生或改变的 Windows 待重启原因。

    参数 previous_state 是操作开始前通过 ``pending_reboot_state()`` 取得的快照；传入后
    只返回前后发生变化且当前仍存在的标记。返回值是普通用户可读原因列表。
    副作用：只读取系统注册表，不会清除待重启标记，也不会重启系统。
    """

    current_state = pending_reboot_state()
    if previous_state is None:
        previous_state = {}
    reasons = []
    for reason, current_value in current_state.items():
        if not current_value or previous_state.get(reason) == current_value:
            continue
        reasons.append(reason)
    return reasons


def restart_windows() -> tuple[bool, str]:
    """在用户明确确认后请求 Windows 立即重启。

    返回值为 ``(是否已发起, 提示文本)``。副作用：成功时调用 shutdown.exe 请求系统立即重启；
    调用方必须先在主线程取得用户确认，不能由后台更新任务自行调用。
    """

    if os.name != "nt":
        return False, "当前系统不支持 Windows 重启操作。"
    shutdown = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "shutdown.exe")
    try:
        subprocess.Popen(
            [shutdown, "/r", "/t", "0"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True, "已请求 Windows 立即重启。"
    except OSError as exc:
        return False, f"无法请求 Windows 重启：{exc}"
