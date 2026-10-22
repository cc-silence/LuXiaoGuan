"""WinBox 启动时的联网组件防火墙审计。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from modules.config_manager import BASE_DIR
from modules.platform_services.windows_firewall import (
    ensure_persistent_allow_rules,
    outbound_is_blocked,
    persistent_allow_rules_ready,
    remove_persistent_allow_rules,
)


_AUDIT_LOCK = threading.Lock()
_AUDIT_COMPLETE = False


def startup_network_programs():
    """返回当前发布形态实际存在的 WinBox 联网可执行文件。

    返回值包括主程序、脚本宿主、aria2 和内置 curl 的去重绝对路径列表。
    副作用：只检查文件存在性，不创建规则。
    """

    candidates = [Path(sys.executable)]
    if not getattr(sys, 'frozen', False):
        executable = Path(sys.executable)
        if executable.name.casefold() in {'python.exe', 'pythonw.exe'}:
            candidates.extend(executable.with_name(name) for name in ('python.exe', 'pythonw.exe'))
    for resource_dir in (BASE_DIR / "resources", BASE_DIR / "_internal", BASE_DIR):
        host = resource_dir / "WinBoxScriptHost.exe"
        if host.is_file():
            candidates.append(host)
            break
    for relative in (Path("bin") / "aria2" / "aria2c.exe", Path("bin") / "curl" / "curl.exe"):
        for base in (BASE_DIR, BASE_DIR / "_internal"):
            path = base / relative
            if path.is_file():
                candidates.append(path)
                break
    unique = []
    seen = set()
    for path in candidates:
        if path.is_file() and str(path.resolve()).casefold() not in seen:
            unique.append(path.resolve())
            seen.add(str(path.resolve()).casefold())
    return unique


def ensure_startup_firewall_rules(progress=None):
    """默认阻止出站时为 WinBox 全部联网组件创建持久放行规则。

    返回值是用户可读的结果消息列表。副作用：必要时触发 UAC 并创建规则；已有
    WinBox 规则时跳过。后台常驻和完整主窗口可共用此函数。
    """

    global _AUDIT_COMPLETE
    if progress is not None:
        progress("checking")
    with _AUDIT_LOCK:
        if _AUDIT_COMPLETE:
            return ["本进程已完成联网组件防火墙审计"]
        programs = startup_network_programs()
        if persistent_allow_rules_ready(programs):
            _AUDIT_COMPLETE = True
            return ["全部防火墙规则已验证且未变化，已批量通过，无需重复检查或添加。"]
        if not outbound_is_blocked():
            _AUDIT_COMPLETE = True
            return ["默认出站未阻止，无需创建放行规则"]
        if not programs:
            return ["未找到需要检测的联网组件，跳过规则检查"]
        if progress is not None:
            progress("adding")
        messages = ensure_persistent_allow_rules(programs)
        # 用户取消 UAC 时允许后续操作再次尝试；已处理成功或发现已有规则才缓存结果。
        if not any("失败" in message for message in messages):
            _AUDIT_COMPLETE = True
        return messages


def remove_startup_firewall_rules():
    """删除当前 WinBox 启动审计创建的持久防火墙规则。

    返回值：用户可读的删除结果消息列表。
    副作用：可能请求 UAC 并删除当前 WinBox、脚本宿主、aria2 和内置 curl 的规则；
    不删除更新软件或用户手动添加的防火墙规则。

    调用限制：由设置页面在用户确认后调用。删除后本次进程不会自动重建规则，重启
    WinBox 才会再次执行启动审计。
    """

    return remove_persistent_allow_rules(startup_network_programs())
