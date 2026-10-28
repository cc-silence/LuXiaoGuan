"""Windows 更新归档工具路径解析。

主要作用：集中解析 WinBox 随包 7-Zip、系统安装位置和 PATH 中的归档工具，
避免更新服务与自定义脚本把某一台开发机的 Program Files 路径当成唯一依赖。
"""

from __future__ import annotations

from modules.release_identity import DISPLAY_NAME
import shutil
from pathlib import Path

from modules.config_manager import BASE_DIR


def resolve_7zip_executable():
    """返回当前可执行的 7z.exe 路径，找不到时返回 None。

    查找顺序：WinBox 随包 bin\\7zip、常见系统安装目录、PATH。返回值是 Path
    或 None。副作用：只检查文件存在性，不创建、修改或执行任何外部工具。
    """

    candidates = (BASE_DIR / "bin" / "7zip" / "7z.exe",)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    from_path = shutil.which("7z.exe") or shutil.which("7z")
    return Path(from_path) if from_path else None


def required_7zip_message(context):
    """生成缺少 7-Zip 时的可定位错误提示。

    参数 context 是当前更新策略或脚本名称。返回值是面向用户的错误文本。
    副作用：无。调用方应在实际解压前检查 resolve_7zip_executable()。
    """

    return (
        f"{context} 需要 7-Zip，但未找到 {DISPLAY_NAME} 内置工具或系统 7z.exe。"
        f"请重新安装完整 {DISPLAY_NAME}，或安装 7-Zip 并加入 PATH 后重试。"
    )
