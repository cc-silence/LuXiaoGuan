"""Linux privilege helpers for sudo validation and privileged command execution.

Passwords are kept only in memory by callers/sudo credential storage; this module never logs
passwords or writes them to disk.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Sequence

from .runtime import is_linux


class SudoPasswordError(PermissionError):
    def __init__(self, message: str, reason: str = "sudo-authentication-failed"):
        super().__init__(message)
        self.reason = reason


def is_elevated() -> bool:
    if not is_linux():
        return False
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def sudo_executable() -> str | None:
    if not is_linux():
        return None
    return shutil.which("sudo")


def sudo_available() -> bool:
    return bool(sudo_executable())


def _hidden_options() -> dict:
    # Keep behavior consistent with other subprocess helpers; Linux has no creationflags window hide.
    return {}


def validate_sudo_password(password: str, timeout: float = 15.0) -> None:
    if not is_linux():
        raise SudoPasswordError("当前系统不支持 sudo 验证。", "unsupported")
    if is_elevated():
        return
    exe = sudo_executable()
    if not exe:
        raise SudoPasswordError("未找到 sudo 命令，无法执行需要管理员权限的操作。", "sudo-missing")
    if not isinstance(password, str) or not password:
        raise SudoPasswordError("请输入 sudo 密码。", "password-required")
    # -k forces fresh validation; -S reads password from stdin; -p '' suppresses prompt text.
    cmd = [exe, "-k", "-S", "-p", "", "-v"]
    try:
        completed = subprocess.run(
            cmd,
            input=(password + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(1.0, float(timeout)),
            check=False,
            **_hidden_options(),
        )
    except subprocess.TimeoutExpired as exc:
        raise SudoPasswordError("sudo 密码验证超时，请重试。", "timeout") from exc
    except Exception as exc:
        raise SudoPasswordError("无法验证 sudo 密码：" + str(exc), "exec-failed") from exc
    if completed.returncode == 0:
        return
    output = (completed.stdout or b"").decode("utf-8", errors="replace").lower()
    if "sorry, try again" in output or "incorrect password" in output or "authentication failure" in output:
        raise SudoPasswordError("sudo 密码不正确，请重新输入。", "password-incorrect")
    if "no new privileges" in output:
        raise SudoPasswordError("当前环境不允许提权执行 sudo。", "sudo-denied")
    raise SudoPasswordError("sudo 密码验证失败，请确认密码和 sudo 权限。", "sudo-authentication-failed")


def run_privileged(command: Sequence[str], password: str | None = None, timeout: float = 30.0) -> subprocess.CompletedProcess[bytes]:
    if not is_linux():
        raise SudoPasswordError("当前系统不支持 Linux 提权。", "unsupported")
    argv = [str(x) for x in command]
    if not argv:
        raise ValueError("privileged command cannot be empty")
    if is_elevated():
        return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False, **_hidden_options())
    exe = sudo_executable()
    if not exe:
        raise SudoPasswordError("未找到 sudo 命令，无法执行需要管理员权限的操作。", "sudo-missing")
    if password:
        # Use -S so we can provide password; keep -p '' to avoid prompt text.
        full = [exe, "-S", "-p", "", "--", *argv]
        input_data = (password + "\n").encode("utf-8")
    else:
        full = [exe, "-n", "-p", "", "--", *argv]
        input_data = None
    try:
        return subprocess.run(
            full,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(1.0, float(timeout)),
            check=False,
            **_hidden_options(),
        )
    except subprocess.TimeoutExpired as exc:
        raise SudoPasswordError("执行管理员命令超时。", "timeout") from exc
