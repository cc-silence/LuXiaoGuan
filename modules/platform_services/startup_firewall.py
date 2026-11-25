"""WinBox 启动时的联网组件防火墙审计。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from modules.config_manager import BASE_DIR
from modules.platform_services.runtime import is_linux, is_windows
from modules.platform_services.windows_firewall import (
    ensure_persistent_allow_rules,
    outbound_is_blocked,
    persistent_allow_rules_ready,
    remove_persistent_allow_rules,
)


_AUDIT_LOCK = threading.Lock()
_AUDIT_COMPLETE = False
_AUDIT_ROUTE = None


class AuditMessages(list):
    """Bind cached audit completion to the actual service route."""
    def __init__(self,messages,route):
        super().__init__(messages)
        self.route=route


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


def _linux_firewall_route():
    from .linux_firewall import _ufw_path
    if not is_linux():
        return "not_applicable"
    return "linux_ufw" if _ufw_path() else "not_applicable"


def ensure_startup_firewall_rules(progress=None, password_provider=None):
    """默认阻止出站时为 WinBox 全部联网组件创建持久放行规则。

    返回值是用户可读的结果消息列表。副作用：必要时触发 UAC/sudo 并创建规则；已有
    WinBox 规则时跳过。后台常驻和完整主窗口可共用此函数。
    """

    global _AUDIT_COMPLETE, _AUDIT_ROUTE
    if progress is not None:
        progress("checking")
    from .windows_network_services import firewall_route
    route = firewall_route()
    if route == "not_applicable" and is_linux():
        route = _linux_firewall_route()
    with _AUDIT_LOCK:
        if _AUDIT_ROUTE != route:
            _AUDIT_COMPLETE = False
            _AUDIT_ROUTE = route
        if route == 'ipsec':
            return AuditMessages(['防火墙服务已禁用，跳过防火墙规则添加；登录优先直接连接，仅在连接被本机拒绝时尝试 IP 安全策略放行。'], route)
        if route == 'not_applicable':
            if is_linux():
                return AuditMessages(['未检测到受支持的 Linux 防火墙（UFW）。如系统防火墙阻止出站，请手动允许路小管联网。'], route)
            return AuditMessages(['Windows network policy audit is not applicable on this platform.'], route)
        if route == 'linux_ufw':
            return _ensure_linux_firewall_rules_locked(progress, password_provider)
        if _AUDIT_COMPLETE:
            return AuditMessages(["本进程已完成联网组件防火墙审计"], route)
        programs = startup_network_programs()
        if persistent_allow_rules_ready(programs):
            _AUDIT_COMPLETE = True
            return AuditMessages(["全部防火墙规则已验证且未变化，已批量通过，无需重复检查或添加。"], route)
        if not outbound_is_blocked():
            _AUDIT_COMPLETE = True
            return AuditMessages(["默认出站未阻止，无需创建放行规则"], route)
        if not programs:
            return AuditMessages(["未找到需要检测的联网组件，跳过规则检查"], route)
        if progress is not None:
            progress("adding")
        messages = ensure_persistent_allow_rules(programs)
        # 用户取消 UAC 时允许后续操作再次尝试；已处理成功或发现已有规则才缓存结果。
        if not any("失败" in message for message in messages):
            _AUDIT_COMPLETE = True
        return AuditMessages(messages, route)


def _ensure_linux_firewall_rules_locked(progress, password_provider):
    global _AUDIT_COMPLETE
    from .linux_firewall import (
        LinuxFirewallPermission,
        ensure_persistent_allow_rules as linux_ensure,
        outbound_is_blocked as linux_outbound_blocked,
        persistent_allow_rules_ready as linux_ready,
    )
    from .linux_privilege import is_elevated
    from .linux_sudo_credential import clear_saved, load_saved_password, save_password

    route = "linux_ufw"
    if _AUDIT_COMPLETE:
        return AuditMessages(["本进程已完成联网组件防火墙审计"], route)
    programs = startup_network_programs()
    password = load_saved_password() or None
    if not is_elevated() and not password:
        # try without password only for status checks that may work; ufw status usually needs root
        pass
    try:
        ready = linux_ready(programs, password=password)
    except LinuxFirewallPermission:
        ready = False
        if not is_elevated() and not password:
            # 没有管理员权限时无法读取 UFW 状态；避免启动时强行弹密码，只提示后续可重试。
            return AuditMessages(["暂时无法检查 Linux 防火墙状态（需要管理员权限）。如果联网或登录路由器被防火墙阻止，请稍后授权检查。"], route)
    if ready:
        _AUDIT_COMPLETE = True
        return AuditMessages(["全部防火墙规则已验证且未变化，已批量通过，无需重复检查或添加。"], route)
    try:
        blocked = linux_outbound_blocked(password=password)
    except LinuxFirewallPermission:
        if not is_elevated() and not password:
            return AuditMessages(["暂时无法检查 Linux 防火墙状态（需要管理员权限）。如果联网或登录路由器被防火墙阻止，请稍后授权检查。"], route)
        blocked = True
    if not blocked:
        _AUDIT_COMPLETE = True
        return AuditMessages(["默认出站未阻止，无需创建放行规则"], route)

    if not is_elevated() and not password:
        if password_provider is None:
            return AuditMessages(["需要管理员密码才能检查或添加 Linux 防火墙规则。请在设置中处理防火墙权限。"], route)
        provided = password_provider()
        if not provided:
            return AuditMessages(["已取消管理员授权，未添加 Linux 防火墙规则。"], route)
        if isinstance(provided, tuple):
            password, remember, allow_fallback = provided
        else:
            password, remember, allow_fallback = provided, False, False
        if not password:
            return AuditMessages(["已取消管理员授权，未添加 Linux 防火墙规则。"], route)
        from .linux_privilege import validate_sudo_password, SudoPasswordError
        try:
            validate_sudo_password(password)
        except SudoPasswordError as exc:
            clear_saved()
            return AuditMessages([f"防火墙规则添加失败：{exc}"], route)
        save_password(password, bool(remember), allow_fallback_file=bool(allow_fallback))

    if progress is not None:
        progress("adding")
    try:
        messages = linux_ensure(programs, password=password)
    except LinuxFirewallPermission as exc:
        clear_saved()
        return AuditMessages([f"防火墙规则添加失败：{exc}"], route)
    if not any("失败" in message for message in messages):
        _AUDIT_COMPLETE = True
    else:
        # if auth failed, clear saved
        if any("密码不正确" in m or "验证失败" in m for m in messages):
            clear_saved()
    return AuditMessages(messages, route)



def remove_startup_firewall_rules(password_provider=None):
    """删除当前 WinBox 启动审计创建的持久防火墙规则。

    返回值：用户可读的删除结果消息列表。
    副作用：可能请求 UAC/sudo 并删除规则。
    """

    from .windows_network_services import firewall_route
    route = firewall_route()
    if route == "not_applicable" and is_linux():
        route = _linux_firewall_route()
    if route == "linux_ufw":
        from .linux_firewall import remove_persistent_allow_rules as linux_remove
        from .linux_sudo_credential import load_saved_password
        from .linux_privilege import is_elevated
        password = load_saved_password() or None
        if not is_elevated() and not password and password_provider is not None:
            provided = password_provider()
            if isinstance(provided, tuple):
                password = provided[0]
            else:
                password = provided
        return linux_remove(startup_network_programs(), password=password)
    return remove_persistent_allow_rules(startup_network_programs())
