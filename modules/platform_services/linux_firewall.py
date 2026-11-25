"""Linux 防火墙审计与放行规则。

仅支持 UFW。Windows 防火墙按程序路径放行；UFW 不支持按程序路径放行，
因此这里在默认出站被阻止时，添加带 RouterManager 标识的出站允许规则，
覆盖局域网访问和常见联网端口，以满足软件联网、直连和登录路由器的需要。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .linux_privilege import SudoPasswordError, is_elevated, run_privileged, sudo_executable, validate_sudo_password

COMMENT_PREFIX = "RouterManager-managed-firewall-v1"
RULES = [
    # LAN / local router access
    ("allow", "out", "to", "10.0.0.0/8", "comment", COMMENT_PREFIX + ":lan10"),
    ("allow", "out", "to", "172.16.0.0/12", "comment", COMMENT_PREFIX + ":lan172"),
    ("allow", "out", "to", "192.168.0.0/16", "comment", COMMENT_PREFIX + ":lan192"),
    ("allow", "out", "to", "169.254.0.0/16", "comment", COMMENT_PREFIX + ":linklocal4"),
    ("allow", "out", "to", "fc00::/7", "comment", COMMENT_PREFIX + ":ula6"),
    ("allow", "out", "to", "fe80::/10", "comment", COMMENT_PREFIX + ":linklocal6"),
    # Internet/update/DNS basics
    ("allow", "out", "53/tcp", "comment", COMMENT_PREFIX + ":dns53tcp"),
    ("allow", "out", "53/udp", "comment", COMMENT_PREFIX + ":dns53udp"),
    ("allow", "out", "80/tcp", "comment", COMMENT_PREFIX + ":http80"),
    ("allow", "out", "443/tcp", "comment", COMMENT_PREFIX + ":https443"),
    ("allow", "out", "443/udp", "comment", COMMENT_PREFIX + ":https443udp"),
]


class LinuxFirewallError(RuntimeError):
    pass


class LinuxFirewallUnsupported(LinuxFirewallError):
    pass


class LinuxFirewallPermission(LinuxFirewallError):
    pass


def is_linux_firewall_applicable() -> bool:
    from .runtime import is_linux
    return is_linux()


def _ufw_path() -> str | None:
    return shutil.which("ufw")


def _run_ufw(args: list[str], password: str | None = None, timeout: float = 30.0) -> subprocess.CompletedProcess[bytes]:
    ufw = _ufw_path()
    if not ufw:
        raise LinuxFirewallUnsupported("未找到 ufw 命令。")
    env = shutil.which("env")
    cmd = [env, "LC_ALL=C", "LANG=C", ufw, *[str(a) for a in args]] if env else [ufw, *[str(a) for a in args]]
    return run_privileged(cmd, password=password, timeout=timeout)


def ufw_status_text(password: str | None = None) -> str:
    try:
        result = _run_ufw(["status", "verbose"], password=password, timeout=20)
    except SudoPasswordError as exc:
        raise LinuxFirewallPermission(str(exc)) from exc
    if result.returncode != 0:
        detail = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        lowered = detail.lower()
        if "command not found" in lowered or "no such file or directory" in lowered:
            raise LinuxFirewallUnsupported("未找到 ufw 命令。")
        raise LinuxFirewallError(detail or f"ufw status 失败（{result.returncode}）。")
    return (result.stdout or b"").decode("utf-8", errors="replace")


def ufw_is_active(status_text: str | None = None, password: str | None = None) -> bool:
    text = status_text if status_text is not None else ufw_status_text(password=password)
    return any(line.strip().lower() == "status: active" for line in text.splitlines())


def ufw_default_outgoing(status_text: str) -> str:
    # Examples: "Default: deny (incoming), allow (outgoing), deny (routed)"
    for line in status_text.splitlines():
        m = re.search(r"Default:\s*(.*)", line, flags=re.IGNORECASE)
        if not m:
            continue
        body = m.group(1).lower()
        out = re.search(r"(allow|deny|reject)\s*\(outgoing\)", body)
        if out:
            return out.group(1)
    return ""


def outbound_is_blocked(password: str | None = None) -> bool:
    if not is_linux_firewall_applicable():
        return False
    if not _ufw_path():
        return False
    try:
        status = ufw_status_text(password=password)
    except LinuxFirewallPermission:
        # Can't inspect without permission; treat as potentially blocked and let caller handle.
        raise
    except LinuxFirewallError:
        return False
    if not ufw_is_active(status):
        return False
    return ufw_default_outgoing(status) in {"deny", "reject"}


def _managed_rule_present(status_text: str) -> bool:
    text = status_text.lower()
    if COMMENT_PREFIX.lower() in text:
        return True
    # Some ufw versions do not show comments in status verbose; fall back to rule-shape check.
    return "10.0.0.0/8" in text and "allow out" in text and "routermanager" in text


def persistent_allow_rules_ready(program_paths, password: str | None = None) -> bool:
    if not is_linux_firewall_applicable():
        return False
    if not _ufw_path():
        return False
    try:
        status = ufw_status_text(password=password)
    except LinuxFirewallError:
        return False
    if not ufw_is_active(status):
        return True
    if ufw_default_outgoing(status) not in {"deny", "reject"}:
        return True
    return _managed_rule_present(status)


def ensure_persistent_allow_rules(program_paths, password: str | None = None) -> list[str]:
    if not is_linux_firewall_applicable():
        return []
    ufw = _ufw_path()
    if not ufw:
        return ["未找到 ufw，无法自动添加 Linux 防火墙规则。请确认系统已安装 UFW，或手动放行路小管的网络访问。"]
    try:
        status = ufw_status_text(password=password)
    except LinuxFirewallPermission as exc:
        return [f"防火墙规则添加失败：{exc}"]
    except LinuxFirewallError as exc:
        return [f"无法检查 Linux 防火墙状态：{exc}"]

    if not ufw_is_active(status):
        return ["UFW 未启用，无需添加放行规则。"]
    if ufw_default_outgoing(status) not in {"deny", "reject"}:
        return ["UFW 默认出站未阻止，无需创建放行规则。"]
    if _managed_rule_present(status):
        return ["路小管的 Linux 防火墙放行规则已存在，无需重复添加。"]

    if not is_elevated() and not password:
        raise LinuxFirewallPermission("需要管理员密码才能添加 Linux 防火墙规则。")

    messages = []
    for rule in RULES:
        try:
            result = _run_ufw(list(rule), password=password, timeout=30)
        except SudoPasswordError as exc:
            return messages + [f"防火墙规则添加失败：{exc}"]
        if result.returncode != 0:
            detail = (result.stdout or b"").decode("utf-8", errors="replace").strip()
            return messages + [f"防火墙规则添加失败：{detail or result.returncode}"]
    messages.append("已添加路小管 Linux 防火墙放行规则（局域网访问和常用联网端口）。")
    return messages


def _managed_rule_numbers(password: str | None = None) -> list[int]:
    try:
        result = _run_ufw(["status", "numbered"], password=password, timeout=20)
    except SudoPasswordError as exc:
        raise LinuxFirewallPermission(str(exc)) from exc
    if result.returncode != 0:
        detail = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        raise LinuxFirewallError(detail or f"ufw status numbered 失败（{result.returncode}）。")
    text = (result.stdout or b"").decode("utf-8", errors="replace")
    numbers = []
    for line in text.splitlines():
        if COMMENT_PREFIX.lower() not in line.lower():
            continue
        m = re.match(r"\[\s*(\d+)\]", line.strip())
        if m:
            numbers.append(int(m.group(1)))
    return sorted(set(numbers))


def remove_persistent_allow_rules(program_paths, password: str | None = None) -> list[str]:
    if not is_linux_firewall_applicable():
        return []
    if not _ufw_path():
        return ["未找到 ufw，未删除 Linux 防火墙规则。"]
    try:
        status = ufw_status_text(password=password)
    except LinuxFirewallPermission as exc:
        return [f"防火墙规则删除失败：{exc}"]
    except LinuxFirewallError as exc:
        return [f"无法检查 Linux 防火墙状态：{exc}"]
    if not ufw_is_active(status):
        return ["UFW 未启用，无需删除规则。"]

    if not is_elevated() and not password:
        raise LinuxFirewallPermission("需要管理员密码才能删除 Linux 防火墙规则。")

    # Prefer deleting by numbered rules that carry our comment, to avoid deleting user rules.
    try:
        numbers = _managed_rule_numbers(password=password)
    except LinuxFirewallPermission as exc:
        return [f"防火墙规则删除失败：{exc}"]
    except LinuxFirewallError:
        numbers = []

    removed = 0
    if numbers:
        for number in reversed(numbers):
            try:
                result = _run_ufw(["--force", "delete", str(number)], password=password, timeout=30)
            except SudoPasswordError as exc:
                return [f"防火墙规则删除失败：{exc}"]
            if result.returncode == 0:
                removed += 1
        if removed:
            return [f"已删除 {removed} 条路小管 Linux 防火墙规则。"]

    # Fallback: if comments are not visible, only delete exact specs when we can confirm they are ours
    # by checking managed presence in verbose output. Avoid deleting broad rules blindly.
    if COMMENT_PREFIX.lower() not in status.lower():
        if _managed_rule_present(status):
            return ["检测到路小管 Linux 防火墙规则，但无法确认规则注释，未自动删除。请在 UFW 中手动检查带 RouterManager 标识的出站规则。"]
        return ["未找到需要删除的路小管 Linux 防火墙规则。"]

    for rule in reversed(RULES):
        args = [a for a in rule if a != "comment" and not str(a).startswith(COMMENT_PREFIX)]
        try:
            result = _run_ufw(["--force", "delete", *args], password=password, timeout=30)
        except SudoPasswordError as exc:
            return [f"防火墙规则删除失败：{exc}"]
        if result.returncode == 0:
            removed += 1
    if removed:
        return [f"已删除 {removed} 条路小管 Linux 防火墙规则。"]
    return ["未找到需要删除的路小管 Linux 防火墙规则。"]
