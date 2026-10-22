"""本地会话的正向安全凭据；未确认远端收尾的现场始终保留。"""

import json
import os
import re
import tempfile
import time
from pathlib import Path
from threading import RLock

from modules.platform_services.owned_cache import checked_path, inventory, delete_inventory

_lock = RLock()
_active = set()
_MARKER = ".winbox-session.json"


def register_session(workspace):
    """创建会话时登记活动所有权；安全标记默认关闭。"""
    path = Path(workspace.local_dir)
    with _lock:
        checked_path(path.parent, path)
        _active.add(path)
        (path / _MARKER).write_text(json.dumps({"owner": "WinBox-capture-v1",
            "session_id": workspace.session_id, "safe": False}), encoding="utf-8")


def acknowledge_session(workspace, safe):
    """仅全部进程及接口恢复确认后允许 safe；失败现场保留，不改变远端顺序。"""
    path = Path(workspace.local_dir)
    with _lock:
        _active.discard(path)
        if not path.exists():
            return
        checked_path(path.parent, path)
        marker = path / _MARKER
        if marker.exists():
            checked_path(path.parent, marker)
        marker.write_text(json.dumps({"owner": "WinBox-capture-v1",
            "session_id": workspace.session_id, "safe": bool(safe)}), encoding="utf-8")


def capture_cache_roots():
    """返回主目录与兼容目录，不创建目录。"""
    from modules.config_manager import CAPTURE_WORK_DIR
    return tuple(dict.fromkeys((CAPTURE_WORK_DIR,
                                Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "WinBox" / "capture-work",
                                Path(tempfile.gettempdir()) / "winbox-capture-work")))


def cleanup_capture_caches(roots, cancel, warn):
    """应用所有服务退出后调用；仅清理有安全凭据且无恢复记录的会话。"""
    deadline = time.monotonic() + 30
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        checked_path(root, root)
        for folder in root.iterdir():
            try:
                if cancel.is_set():
                    raise InterruptedError("Capture cache maintenance cancelled")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Capture cache maintenance budget exceeded")
                with _lock:
                    if folder in _active:
                        raise ValueError("会话仍有活动登记")
                    if not re.fullmatch(r"[a-f0-9]{32}", folder.name):
                        raise ValueError("会话目录身份未知")
                    entries = inventory(root, folder, cancel, deadline)
                    if any(p.name.startswith("takeover-journal") or p.name == "owned-processes.json" for p, _s in entries):
                        raise ValueError("存在待核实的恢复或进程记录，请连接设备完成恢复")
                    marker = folder / _MARKER
                    if checked_path(root, marker).st_size > 4096:
                        raise ValueError("安全凭据格式未知")
                    data = json.loads(marker.read_text(encoding="utf-8"))
                    if data != {"owner": "WinBox-capture-v1", "session_id": folder.name, "safe": True}:
                        raise ValueError("远端资源收尾未确认")
                    delete_inventory(root, entries, cancel, deadline)
            except (InterruptedError, TimeoutError):
                raise
            except (OSError, ValueError, TypeError) as exc:
                warn(f"保留抓包恢复现场：{folder}；{exc}。请核对会话后处理。", "warning")
