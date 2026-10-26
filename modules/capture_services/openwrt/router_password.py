"""Bounded system password change over encrypted stdin, never command arguments."""
from dataclasses import dataclass, field
import time
from .command_runner import require_command_completion
from .ssh_client import OpenWrtSshClient


@dataclass
class PasswordChange:
    settings: dict
    secret: bytearray = field(repr=False)

    @classmethod
    def create(cls, password, settings):
        if not isinstance(password, str) or not 8 <= len(password.encode("utf-8")) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in password):
            raise ValueError("新密码需要 8～128 字节，不能包含换行或控制字符。")
        return cls(dict(settings), bytearray(password.encode("utf-8")))

    def clear(self):
        self.secret[:] = b"\0" * len(self.secret)
        self.secret.clear()


def change_password(manager, request, cancel):
    ssh = manager.ssh
    connection, fingerprint = ssh._client, ssh.host_fingerprint
    if not connection or not fingerprint:
        raise RuntimeError("请先登录路由器。")
    check = manager.runner.run("password_change_check",
        'uid=$(id -u) || exit 40; [ "$uid" = 0 ] || exit 41; '
        'command -v passwd >/dev/null 2>&1 || exit 42',
        timeout=10, cancel_event=cancel)
    require_command_completion(check)
    if check.exit_code == 41:
        raise RuntimeError("当前 SSH 账号没有管理员权限，请使用 root 登录后修改路由器管理密码。未修改密码。")
    if check.exit_code == 42:
        raise RuntimeError("路由器未提供 passwd 改密工具，请检查固件组件。未修改密码。")
    if check.exit_code != 0:
        raise RuntimeError("未能确认路由器改密权限和工具，请检查连接后重试。未修改密码。")
    if cancel.is_set():
        raise InterruptedError("修改已取消，未提交新密码。")
    if ssh._client is not connection or ssh.host_fingerprint != fingerprint:
        raise RuntimeError("路由器连接已变化，未修改密码。")
    streams = ()
    submitted = False
    changed = False
    try:
        stdin, stdout, stderr, channel = ssh.exec_command(
            "LC_ALL=C passwd", timeout=10, cancel_event=cancel)
        streams = (stdin, stdout, stderr, channel)
        deadline = time.monotonic() + 25
        pending = b""
        step = 0
        while time.monotonic() < deadline:
            # Once the second line is sent, finish observing the write even if cancelled.
            if cancel.is_set() and not submitted:
                raise InterruptedError("修改已取消，未提交新密码。")
            if channel.recv_ready():
                pending += channel.recv(4096)
            if channel.recv_stderr_ready():
                pending += channel.recv_stderr(4096)
            if len(pending) > 8192:
                raise RuntimeError("密码工具返回异常，已停止。")
            prompts = ((b"new password:", b"new unix password:") if step == 0 else
                       (b"retype password:", b"retype new password:", b"retype new unix password:") if step == 1 else ())
            if any(prompt in pending.lower() for prompt in prompts):
                if step == 1:
                    submitted = True
                stdin.write(bytes(request.secret) + b"\n")
                stdin.flush()
                step += 1
                submitted = step == 2
                pending = b""
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                changed = submitted and channel.recv_exit_status() == 0
                break
            cancel.wait(0.02) if not submitted else time.sleep(0.02)
    except InterruptedError:
        raise
    except Exception:
        if not submitted:
            raise RuntimeError("密码修改未完成，未确认提交；请检查连接后重试。") from None
    finally:
        for resource in reversed(streams):
            try:
                resource.close()
            except Exception:
                pass
    if not submitted:
        raise RuntimeError("未收到完整的密码输入提示，未修改密码。")
    # A separate password-only login verifies the new credential without replacing the session.
    verifier = OpenWrtSshClient()
    verified = False
    try:
        verifier.configure(request.settings["host"], request.settings["port"], request.settings["user"])
        verifier.connect(password=request.secret.decode("utf-8"), expected_fingerprint=fingerprint, timeout=8)
        verified = True
    except Exception:
        pass  # Report only a fixed message; auth errors can contain implementation details.
    finally:
        verifier.close()
    if verified:
        note = "路由器登录密码已修改，并已通过新密码登录验证。"
    elif changed:
        note = "系统已确认密码修改，但新密码登录验证未完成。请保留新密码，并检查连接或密码登录权限。"
    else:
        note = "新密码已提交，但修改结果未确认。请保留新密码，重新登录核对，勿连续重试。"
    return dict(changed=True if changed or verified else None, verified=verified, note=note)
