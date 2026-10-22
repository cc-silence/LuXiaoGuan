"""WinBox 下载目录路径规则。

主要作用：统一软件显示名称到 Windows 下载子目录的映射，供下载、扫描和更新入口共用。
输入参数：name 是软件显示名称；download_manager 是下载目录配置管理器。
返回值：安全目录名或软件专属下载目录 Path。
副作用：只计算路径，不创建目录、不读取界面状态。
调用限制：创建目录的调用方需自行处理 OSError；不能用原始软件名称直接拼接路径。
"""

import re
from pathlib import Path


INVALID_WINDOWS_NAME_CHARS = r'<>:"/\\|?*'


def safe_software_dir_name(name):
    """将软件显示名称转换成 Windows 可用目录名。

    参数：name 是软件配置中的显示名称。
    返回值：``(safe_name, renamed)``，后者表示是否替换了非法或末尾无效字符。
    副作用：无，不访问文件系统。
    """
    original = str(name or "").strip()
    cleaned = re.sub(f"[{re.escape(INVALID_WINDOWS_NAME_CHARS)}]+", "_", original)
    cleaned = cleaned.strip(" ._")
    return cleaned or "未命名软件", cleaned != original


def software_download_directory(download_manager, name, configured_download_dir=""):
    """计算一个软件在当前下载配置下的专属目录。

    参数：download_manager 提供 ``normalized_download_dir``；name 是软件显示名称；
    configured_download_dir 是原始下载目录配置，空字符串表示由管理器使用默认目录。
    返回值：标准化下载根目录下的软件专属 Path。
    副作用：只读取下载目录配置解析结果，不创建目录。
    """
    safe_name, _renamed = safe_software_dir_name(name)
    root = Path(download_manager.normalized_download_dir(configured_download_dir))
    return root / safe_name
