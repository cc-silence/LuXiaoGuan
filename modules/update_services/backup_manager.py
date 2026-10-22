"""备份恢复管理器。

主要作用：更新前备份用户保留的配置文件和目录，更新失败时自动恢复，
更新成功后清理临时备份。从 StrategyExecutor 中提取。

所有函数都是模块级静态函数，不依赖类实例，便于测试。
"""

import shutil
import tempfile
import time
from pathlib import Path
from shutil import copy2, rmtree, copytree

from modules.config_manager import resolve_portable_path


def backup_keep_paths(config, target_dir=None):
    """按 config_keep_paths 备份用户要保留的配置文件或目录。

    参数：config 是更新配置；target_dir 用于兼容旧的相对路径写法。
    返回值：备份上下文字典，包含临时备份目录和每条备份的原始目标路径。
    副作用：会在系统临时目录创建备份目录，并复制真实文件。
    约定：绝对路径按原位置备份/恢复；相对路径仍相对 target_dir 处理。
    """
    keep_paths = config.get("config_keep_paths", [])
    if not config.get("backup_enabled", False) or not keep_paths:
        return None

    base_dir = Path(target_dir) if target_dir else None
    backup_root = None
    entries = []
    for raw_path in keep_paths:
        source = _resolve_keep_source(raw_path, base_dir)
        if source is None or not source.exists():
            continue
        if backup_root is None:
            # 备份目录不能放在目标软件目录内；delete_old_dir 会删除整个目标目录，
            # 如果备份也在里面，会导致后续恢复找不到备份文件。
            backup_root = Path(tempfile.mkdtemp(prefix=f"winbox_backup_{int(time.time())}_"))
        backup_name = f"keep_{len(entries)}{source.suffix if source.is_file() else ''}"
        backup_path = backup_root / backup_name
        try:
            if source.is_dir():
                copytree(source, backup_path)
            else:
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                copy2(source, backup_path)
            entries.append({"source": source, "backup": backup_path, "is_dir": source.is_dir()})
        except Exception:
            continue

    if not entries:
        if backup_root:
            rmtree(backup_root, ignore_errors=True)
        return None
    return {"root": backup_root, "entries": entries}


def restore_keep_paths(backup_context):
    """把 backup_keep_paths 保存的文件恢复回原位置。

    返回值：(success, message)。恢复失败会返回 False，避免误报更新成功。
    副作用：会覆盖恢复目标位置已有的同名文件或目录。
    """
    if not backup_context:
        return True, ""
    errors = []
    for entry in backup_context.get("entries", []):
        source = entry.get("backup")
        dest = entry.get("source")
        if not source or not dest or not Path(source).exists():
            continue
        source = Path(source)
        dest = Path(dest)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if entry.get("is_dir"):
                if dest.exists():
                    rmtree(dest)
                copytree(source, dest)
            else:
                copy2(source, dest)
        except PermissionError:
            errors.append(f"{dest}: 权限不足")
        except (OSError, shutil.Error) as exc:
            errors.append(f"{dest}: {exc}")
    if errors:
        return False, f"恢复备份失败：{'；'.join(errors)}"
    return True, ""


def cleanup_backup_context(backup_context):
    """删除 backup_keep_paths 创建的临时备份目录。"""
    if not backup_context:
        return
    root = backup_context.get("root")
    if root:
        rmtree(root, ignore_errors=True)


def _resolve_keep_source(raw_path, base_dir=None):
    """解析保留配置路径。

    绝对路径和 {APP_DIR} 等可移植路径直接解析；相对路径在 base_dir 下查找。
    返回 Path 或 None，不修改文件。
    """
    text = str(raw_path or "").strip()
    if not text:
        return None
    resolved = resolve_portable_path(text)
    if resolved.is_absolute():
        return resolved
    # 相对路径：在 base_dir 下查找
    if base_dir:
        candidate = (base_dir / text).resolve()
        if candidate.exists():
            return candidate
        # 再试一次：如果目标目录不存在，直接用 resolve() 拿绝对路径
        resolved = (base_dir / text).resolve()
        # 走 resolve() 后如果文件存在则返回，不存在就保持 resolve 结果让外层判断
        return resolved
    return Path(text).resolve()


def normalize_keep_path(raw_path, target):
    """把用户填写的保留配置路径转成相对于 target 的相对路径。

    PathListEditor 的"添加"按钮会用文件选择器返回绝对路径，
    但备份/恢复需要相对路径。这里自动处理两种情况：
    - 相对路径（如 guiConfigs）→ 直接使用
    - 绝对路径（如 C:/software/v2rayN/guiConfigs）→ 尝试转为相对路径
    参数：
        raw_path: 用户填写的路径（相对或绝对）
        target: 目标目录的 Path 对象
    返回值：
        相对路径字符串，无法转换时返回空字符串
    """
    rel = str(raw_path).strip().replace("\\", "/")
    if not rel:
        return ""
    p = Path(rel)
    if p.is_absolute():
        try:
            return str(p.relative_to(target))
        except ValueError:
            return ""
    return rel
