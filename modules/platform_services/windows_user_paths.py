"""Windows 当前用户已知目录服务。

本模块集中处理 Windows 用户目录读取，避免提权后的 HKCU 指向管理员账户时，
业务脚本把用户文件保存到错误位置。
"""

from __future__ import annotations

import os
import platform
from pathlib import Path


def current_user_desktop() -> Path:
    """返回调用软件 的当前用户桌面绝对路径。

    返回值：优先读取 Windows User Shell Folders 的 Desktop；注册表缺失或路径无效时
    回退到 ``%USERPROFILE%\\Desktop``。返回路径不保证已创建。
    副作用：只读取注册表和环境变量，不创建目录、不修改配置或界面。

    调用限制：必须在请求 UAC 前调用。提权后 HKCU 可能属于批准 UAC 的管理员账户，
    不能再用于确定原始调用者的桌面位置。
    """

    fallback = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    if os.name != "nt":
        return fallback
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, "Desktop")
        resolved = Path(os.path.expandvars(str(value)))
        if resolved.is_absolute():
            return resolved
    except OSError:
        pass
    return fallback
