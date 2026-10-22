"""更新任务的临时诊断日志服务。

主要作用：为每次软件更新创建独立日志，并记录更新策略、安装包、关键步骤和最终结果。
日志放系统临时目录，保留必要安装诊断，用户可统一清理。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4

UPDATE_LOG_DIR_NAME = "update-logs"
UPDATE_LOG_MAX_AGE_DAYS = 90
UPDATE_LOG_MAX_TOTAL_BYTES = 5 * 1024 * 1024
_LOG_PATH_KEY = "_update_log_path"


def create_update_log(config: dict, software_name: str, strategy: str, package_path: Path) -> Path | None:
    """创建本次更新的持久日志并写入任务基础信息。

    参数：config 是本次运行时更新配置，会写入私有日志路径；software_name 是软件名称；
    strategy 是更新策略；package_path 是待处理的安装包路径。
    返回值：成功时返回日志文件 Path，目录不可写时返回 None。
    副作用：在系统 TEMP/WinBox/update-logs 下创建独立 UTF-8 诊断日志。
    不覆盖或删除已有用户日志，不修改用户保存的软件配置。
    """
    base_dir = Path(tempfile.gettempdir()) / "WinBox" / UPDATE_LOG_DIR_NAME
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_log_file_stem(software_name)
        log_path = base_dir / f"{safe_name}-{uuid4().hex[:12]}.log"
        with log_path.open("x", encoding="utf-8"):
            pass
        config[_LOG_PATH_KEY] = str(log_path)
        write_update_log(config, f"开始更新：{software_name}")
        write_update_log(config, f"更新策略：{strategy}")
        write_update_log(config, f"更新包：{package_path}")
        return log_path
    except OSError:
        config.pop(_LOG_PATH_KEY, None)
        return None


def write_update_log(config: dict, message: str) -> None:
    """向本次更新日志追加一条带时间的文本。

    参数：config 是含私有日志路径的运行时更新配置；message 是用户可读记录内容。
    返回值：无。
    副作用：追加 UTF-8 文件；写入失败时静默跳过，不能让日志问题阻断更新主流程。
    """
    log_path = get_update_log_path(config)
    if log_path is None:
        return
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


@contextmanager
def timed_update_step(config: dict, step_name: str):
    """记录一个安装步骤的单调时钟耗时。

    参数：config 是本次运行配置；step_name 是日志中的步骤名称。返回值：上下文
    管理器。副作用：步骤结束（包括异常或提前返回）时追加一条耗时日志；日志写入
    失败不影响更新。调用方应只包住真正执行的工作，避免把用户确认等待混入 I/O
    性能数据。
    """

    started_at = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - started_at
        write_update_log(config, f"阶段耗时：{step_name}={elapsed:.3f} 秒")


def get_update_log_path(config: dict) -> Path | None:
    """取得本次更新的持久日志路径。

    参数：config 是运行时更新配置。返回值：已创建日志时返回 Path，否则返回 None。
    副作用：无。
    """
    value = str(config.get(_LOG_PATH_KEY, "") or "").strip()
    return Path(value) if value else None


def _safe_log_file_stem(software_name: str) -> str:
    """将软件显示名称转换为 Windows 可用的日志文件名主体。

    参数：software_name 是用户配置的软件名称。返回值：保留中文和普通字符、替换 Windows
    非法路径字符后的文件名主体。副作用：无。
    """
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(software_name or "")).strip(". ")
    return value[:80] or "update"


def _cleanup_old_logs(log_dir: Path) -> None:
    """清理过期日志，并将日志目录总容量控制在 5 MB 内。"""
    try:
        log_files = sorted(
            (path for path in log_dir.glob("*.log") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
    except OSError:
        return

    cutoff = datetime.now().timestamp() - timedelta(days=UPDATE_LOG_MAX_AGE_DAYS).total_seconds()
    retained = []
    for old_path in log_files:
        try:
            if old_path.stat().st_mtime < cutoff:
                old_path.unlink()
                continue
            retained.append(old_path)
        except OSError:
            continue

    total_size = 0
    for old_path in retained:
        try:
            total_size += old_path.stat().st_size
        except OSError:
            continue
    for old_path in retained:
        if total_size <= UPDATE_LOG_MAX_TOTAL_BYTES:
            break
        try:
            total_size -= old_path.stat().st_size
            old_path.unlink()
        except OSError:
            continue
