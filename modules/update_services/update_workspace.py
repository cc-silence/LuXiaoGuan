"""更新脚本临时工作目录服务。

主要作用：为每次更新在用户可写目录创建独立工作区，避免自定义脚本向
WinBox 安装目录写入临时下载包、解压文件或生成资源。
"""

from __future__ import annotations

import os
import re
import tempfile
import time
import logging
from threading import RLock
from pathlib import Path

from modules.platform_services.owned_cache import checked_path, inventory, delete_inventory


FAILED_WORKSPACE_RETENTION_DAYS = 15
_workspace_lock = RLock()
_active_workspaces = set()
_owned_workspaces = set()


def cleanup_stale_update_workspaces(base_dir: Path, cancel=None, now=None, warn=None) -> None:
    """启动时单次清理 15 天无写入且未登记使用的自有工作区；扫描可取消、有界。"""
    base_dir = Path(base_dir)
    tell = warn or (lambda text, _level: logging.getLogger(__name__).warning(text))
    if not base_dir.exists():
        return
    checked_path(base_dir, base_dir)
    cutoff = (time.time() if now is None else now) - FAILED_WORKSPACE_RETENTION_DAYS * 86400
    deadline = time.monotonic() + 30
    for candidate in base_dir.iterdir():
        try:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Update cache maintenance cancelled")
            if time.monotonic() >= deadline:
                raise TimeoutError("Update cache maintenance budget exceeded")
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}_[a-z0-9_]{8}", candidate.name):
                tell(f"保留未知更新工作区：{candidate}。请核对来源。", "warning")
                continue
            with _workspace_lock:
                if candidate in _active_workspaces:
                    continue
            entries = inventory(base_dir, candidate, cancel, deadline)
            if max(info.st_mtime for _path, info in entries) >= cutoff:
                continue
            with _workspace_lock:
                if candidate not in _active_workspaces:
                    delete_inventory(base_dir, entries, cancel, deadline)
        except (InterruptedError, TimeoutError):
            raise
        except (OSError, ValueError) as exc:
            tell(f"更新工作区清理失败：{candidate}；{exc}。请核对权限和目录链接。", "warning")


def ensure_update_workspace(config, software_name):
    """确保更新配置含有本次可写工作目录。

    参数 config 是本次更新的内存配置字典，software_name 用于生成可读目录前缀。
    返回值是工作目录 Path，并会把真实路径写入 config["workspace_dir"]。
    副作用：在 LOCALAPPDATA/WinBox/update-work 下创建工作目录，保留恢复备份。
    不迁移或清理已有目录；该字段只存在于运行时配置，不会写回用户 JSON。
    """

    if not _workspace_lock.acquire(blocking=False):
        raise RuntimeError("更新缓存正在维护，请稍后重新开始更新。")
    try:
        configured = str(config.get("workspace_dir", "") or "").strip()
        if configured:
            workspace = Path(configured)
            workspace.mkdir(parents=True, exist_ok=True)
        else:
            base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "WinBox" / "update-work"
            base.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(software_name or "update"))[:40] or "update"
            workspace = Path(tempfile.mkdtemp(prefix=f"{safe_name}_", dir=base))
            _owned_workspaces.add(workspace)
            config["workspace_dir"] = str(workspace)
        _active_workspaces.add(workspace)
        return workspace
    finally:
        _workspace_lock.release()


def release_update_workspace(config):
    """更新 worker 最终退出时注销使用登记；失败现场保留在磁盘。"""
    with _workspace_lock:
        _active_workspaces.discard(Path(str((config or {}).get("workspace_dir", ""))))


def cleanup_update_workspace(config):
    """删除本次更新成功后不再需要的工作目录。

    参数 config 是包含 workspace_dir 的运行时配置。返回值无。
    副作用：仅清理本进程创建的工作区；未知来源和删除失败会记录并保留。
    失败更新不会调用本函数，以保留现场供用户排查。
    """

    workspace = str(config.get("workspace_dir", "") or "").strip()
    if workspace:
        path = Path(workspace)
        with _workspace_lock:
            if path not in _owned_workspaces:
                logging.getLogger(__name__).warning("保留非本次创建的更新目录：%s。请核对来源。", path)
                return
            try:
                if path.exists():
                    delete_inventory(path.parent, inventory(path.parent, path))
                _owned_workspaces.discard(path)
            except (OSError, ValueError) as exc:
                logging.getLogger(__name__).warning("更新缓存清理失败：%s；%s。请检查权限。", path, exc)
