"""更新流程中的文件删除服务。

主要作用：按更新配置执行更新前或更新后的文件/目录删除。
输入参数：配置字典、当前目标目录和阶段名 pre/post。
返回值：(success, message)，目标不存在视为成功，失败时返回可读错误信息。
副作用：会真实删除用户配置指定的文件或目录；不创建 UI、不写配置。
调用限制：调用方必须先完成签名、进程和用户确认等前置检查。
"""

from pathlib import Path
from shutil import rmtree

from modules.config_manager import resolve_portable_path


def apply_delete_paths(config, target_dir=None, phase="pre"):
    """按配置删除更新前/更新后指定的文件或目录。"""
    key = "post_delete_paths" if phase == "post" else "pre_delete_paths"
    enabled_key = "post_delete_enabled" if phase == "post" else "pre_delete_enabled"
    if not config.get(enabled_key):
        return True, ""
    paths = config.get(key, [])
    if not paths:
        return True, ""

    base_dir = Path(target_dir) if target_dir else None
    errors = []
    for raw_path in paths:
        text = str(raw_path or "").strip()
        if not text:
            continue
        resolved = resolve_portable_path(text)
        delete_path = resolved if resolved.is_absolute() else (base_dir / text if base_dir else None)
        if delete_path is None:
            errors.append(f"{text}: 相对路径缺少目标目录，无法删除")
            continue
        try:
            if not delete_path.exists():
                continue
            if delete_path.is_dir():
                rmtree(delete_path)
            else:
                delete_path.unlink()
        except PermissionError:
            errors.append(f"{delete_path}: 权限不足")
        except OSError as exc:
            errors.append(f"{delete_path}: {exc}")
    if errors:
        label = "更新后删除" if phase == "post" else "更新前删除"
        return False, f"{label}失败：{'；'.join(errors)}"
    return True, ""
