"""Bounded worker-only reboot of the same authenticated router."""
import json
from threading import Event
from .command_runner import require_command_completion

REBOOT_PROBE = 'test "$(id -u)" = 0 || exit 41; test -x /sbin/reboot && command -v sync >/dev/null && command -v ubus >/dev/null || exit 43; ubus call system board'


def request_reboot(run, ssh, connection):
    """Do not wait for boot, retry, or close a later replacement connection."""
    try:
        run("reboot", "sync && /sbin/reboot", Event(), 12)
        accepted = True
    except Exception:
        accepted = False
    finally:
        invalidate = getattr(ssh, "_invalidate", None)
        if ssh._client is connection and callable(invalidate):
            invalidate(connection)
    return accepted


def reboot_router(manager, cancel):
    ssh = manager.ssh
    connection, fingerprint = ssh._client, ssh.host_fingerprint

    def run(name, command, event, timeout=10):
        if not ssh.connected or ssh._client is not connection or ssh.host_fingerprint != fingerprint:
            raise RuntimeError("路由器连接已变化，已停止旧设备的重启操作。")
        result = manager.runner.run("maintenance_" + name, command, timeout=timeout, cancel_event=event)
        require_command_completion(result)
        if result.exit_code:
            raise RuntimeError("需要管理员权限、标准重启命令和设备信息接口；未确认执行成功。")
        return result.stdout

    board = json.loads(run("reboot_probe", REBOOT_PROBE, cancel))
    if not isinstance(board, dict) or not isinstance(board.get("release"), dict) or board["release"].get("distribution") != "OpenWrt":
        raise RuntimeError("未能确认官方 OpenWrt 系统，未执行重启。")
    if cancel.is_set():
        raise InterruptedError("重启已取消。")
    if not ssh.connected or ssh._client is not connection or ssh.host_fingerprint != fingerprint:
        raise RuntimeError("连接已变化，未执行重启。")
    accepted = request_reboot(run, ssh, connection)
    return dict(outcome="reconnect" if accepted else "uncertain",
        note=("重启命令已返回成功，请等待路由器启动后重新登录。"
              if accepted else "重启请求的结果未确认，请等待并核对设备状态，勿连续重复重启。")
             + "重启期间网络和本软件登录会断开；如之前保存过 LAN 设置，请使用生效后的地址重新连接。")
