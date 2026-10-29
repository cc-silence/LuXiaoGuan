"""OpenWrt 自动抓包开机自启服务与离线成果管理。

遵循原则：
1. 采用标准 OpenWrt 服务脚本 /etc/init.d/luxiaoguan-autocap，不修改 /etc/rc.local；
2. 远端与前台产物一律落盘至 /root/cap/（Flash 持久化 overlay），严禁使用 /tmp；
3. 本地与远端脚本版本比对，支持一键更新；
4. SFTP 拉取离线成果与会话日志，提供状态与异常取消保护。
"""

from dataclasses import dataclass
from pathlib import Path
import base64
import posixpath
import re
import sys
import time

# 兼容直接从命令行独立运行该文件
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from modules.capture_services.capture_logging import LOGGER
from modules.config_manager import BASE_DIR

REMOTE_SCRIPT_PATH = "/root/hcx_autocap.sh"
REMOTE_SERVICE_PATH = "/etc/init.d/luxiaoguan-autocap"
REMOTE_CAP_DIR = "/root/cap"
SERVICE_NAME = "luxiaoguan-autocap"

SERVICE_INIT_SCRIPT = """#!/bin/sh /etc/rc.common

START=99
STOP=10

# 注意：切勿在此定义 USE_PROCD=0，因为 /etc/rc.common 使用 [ -n "$USE_PROCD" ] 判断，
# 只要变量非空就会强制作为 procd 托管并覆盖重写 start() 为 start_service()，导致启动空转。

boot() {
    # 开机自启入口：后台延迟 15 秒拉起，确保系统网络环境、无线驱动与系统服务完全初始化稳定
    (
        logger -t luxiaoguan-autocap "Boot detected: waiting 15s startup delay before launching capture..."
        sleep 15
        start
    ) >/dev/null 2>&1 &
}

start() {
    if [ ! -f /root/hcx_autocap.sh ]; then
        logger -t luxiaoguan-autocap "Error: /root/hcx_autocap.sh not found"
        return 1
    fi
    chmod +x /root/hcx_autocap.sh
    export PATH="/root/.winbox-tools/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

    LOCK="/tmp/.hcx_autocap.lock"
    if [ -f "$LOCK" ]; then
        PID=$(cat "$LOCK" 2>/dev/null)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            logger -t luxiaoguan-autocap "Already running with PID $PID"
            return 0
        fi
    fi

    # 以后台静默方式启动，脱离控制终端防止 SIGHUP 导致进程随启动脚本退出
    # VIEW=off 避免输出 ANSI 帧灌满重定向或会话文件
    if [ -x /usr/bin/nohup ]; then
        VIEW=off nohup /bin/sh /root/hcx_autocap.sh >/dev/null 2>&1 &
    else
        ( trap '' HUP; VIEW=off /bin/sh /root/hcx_autocap.sh >/dev/null 2>&1 ) &
    fi
    logger -t luxiaoguan-autocap "Started /root/hcx_autocap.sh in background"
}

stop() {
    LOCK="/tmp/.hcx_autocap.lock"
    if [ -f "$LOCK" ]; then
        PID=$(cat "$LOCK" 2>/dev/null)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill -TERM "$PID" 2>/dev/null
            WAIT=0
            while [ "$WAIT" -lt 15 ]; do
                kill -0 "$PID" 2>/dev/null || break
                sleep 1
                WAIT=$((WAIT + 1))
            done
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null
            fi
        fi
        rm -f "$LOCK"
    fi
    # 兜底清理残留 monitor 接口
    for d in $(ls /sys/class/net 2>/dev/null | grep -E '^hcxmon[0-9]+'); do
        iw dev "$d" del >/dev/null 2>&1
    done
}
"""


@dataclass(frozen=True)
class AutoCaptureBootStatus:
    """开机自启与脚本版本状态快照。"""

    installed: bool = False
    service_installed: bool = False
    autostart_enabled: bool = False
    is_running: bool = False
    remote_script_exists: bool = False
    remote_script_path: str = "/root/hcx_autocap.sh"
    remote_version: str = ""
    local_version: str = ""
    has_update: bool = False
    error_message: str = ""


def get_local_script_path() -> Path:
    """定位本地内置的 hcx_autocap.sh 脚本路径。"""
    candidate = BASE_DIR / "tools" / "scripts" / "hcx_autocap.sh"
    if candidate.is_file():
        return candidate
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "tools" / "scripts" / "hcx_autocap.sh"
    if candidate.is_file():
        return candidate
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidate = exe_dir / "tools" / "scripts" / "hcx_autocap.sh"
        if candidate.is_file():
            return candidate
    return candidate


def read_local_script_content() -> str:
    """读取本地内置脚本内容。"""
    path = get_local_script_path()
    if not path.is_file():
        raise FileNotFoundError(f"本地抓包脚本不存在: {path}")
    return path.read_text(encoding="utf-8")


def read_local_script_version() -> str:
    """解析本地内置脚本头部 SCRIPT_VER 常量。"""
    try:
        content = read_local_script_content()
        match = re.search(r'(?m)^SCRIPT_VER=["\']?([^"\']+)["\']?', content)
        if match:
            return match.group(1).strip()
    except Exception as exc:
        LOGGER.warning("Failed to read local script version: %s", exc)
    return "v2610"


class AutoCaptureBootManager:
    """管理 OpenWrt 端 /root/ 抓包脚本及 /etc/init.d/ 自启服务。"""

    def __init__(self, command_runner, ssh_client):
        self.runner = command_runner
        self.ssh = ssh_client

    def probe_boot_status(self, cancel_event=None) -> AutoCaptureBootStatus:
        """轻量探针检测远端自启与脚本状态。"""
        local_ver = read_local_script_version()
        cmd = (
            "R_PATH=\"\"; "
            "if [ -f /root/hcx_autocap.sh ]; then R_PATH=\"/root/hcx_autocap.sh\"; "
            "elif [ -f /root/autocap.sh ]; then R_PATH=\"/root/autocap.sh\"; fi; "
            "if [ -n \"$R_PATH\" ]; then "
            "R_VER=$(awk -F= '$1==\"SCRIPT_VER\"{gsub(/[ \"\\x27]/, \"\", $2); print $2; exit}' \"$R_PATH\" 2>/dev/null); "
            "[ -z \"$R_VER\" ] && R_VER=$(head -n 60 \"$R_PATH\" 2>/dev/null | grep -m 1 '^SCRIPT_VER=' | tr -cd '0-9a-zA-Z._' | sed 's/^SCRIPT_VER//'); "
            "R_EXIST=1; "
            "else R_VER=\"\"; R_EXIST=0; fi; "
            "[ -f /etc/init.d/luxiaoguan-autocap ] && S_INSTALLED=1 || S_INSTALLED=0; "
            "ls /etc/rc.d/S*luxiaoguan-autocap >/dev/null 2>&1 && S_ENABLED=1 || S_ENABLED=0; "
            "R_RUNNING=0; "
            "if [ -f /tmp/.hcx_autocap.lock ]; then "
            "PID=$(cat /tmp/.hcx_autocap.lock 2>/dev/null); "
            "if [ -n \"$PID\" ] && kill -0 \"$PID\" 2>/dev/null; then R_RUNNING=1; fi; "
            "fi; "
            "if [ \"$R_RUNNING\" = \"0\" ]; then "
            "if ps w 2>/dev/null | grep 'hcxdumptool' | grep -v grep >/dev/null 2>&1; then R_RUNNING=1; fi; "
            "fi; "
            "printf '__AUTOCAP_STATUS__:%s:%s:%s:%s:%s:%s\\n' \"$R_EXIST\" \"$R_VER\" \"$S_INSTALLED\" \"$S_ENABLED\" \"$R_RUNNING\" \"$R_PATH\""
        )
        res = self.runner.run("probe_autocap_status", cmd, timeout=12, cancel_event=cancel_event)
        if res.cancelled or res.timed_out or res.exit_code != 0:
            err = "探测超时" if res.timed_out else ("已取消" if res.cancelled else res.stderr or "执行失败")
            return AutoCaptureBootStatus(local_version=local_ver, error_message=err)

        match = re.search(r"__AUTOCAP_STATUS__:(\d):([^:]*):(\d):(\d):(\d):?([^\r\n]*)", res.stdout or "")
        if not match:
            return AutoCaptureBootStatus(local_version=local_ver, error_message="探针输出解析失败")

        r_exist = match.group(1) == "1"
        r_ver = match.group(2).strip()
        s_installed = match.group(3) == "1"
        s_enabled = match.group(4) == "1"
        r_running = match.group(5) == "1"
        r_path = (match.group(6) or "").strip() or "/root/hcx_autocap.sh"

        has_update = False
        if r_exist and r_ver and local_ver:
            has_update = r_ver != local_ver

        return AutoCaptureBootStatus(
            installed=s_installed and r_exist,
            service_installed=s_installed,
            autostart_enabled=s_enabled,
            is_running=r_running,
            remote_script_exists=r_exist,
            remote_script_path=r_path,
            remote_version=r_ver,
            local_version=local_ver,
            has_update=has_update,
        )

    def _upload_file(self, remote_path: str, content: str | bytes, mode: int = 0o755, cancel_event=None) -> None:
        """安全上传文件到远端：优先 SFTP；若 SFTP 不可用，自动降级为 base64 分片写入。"""
        data = content.encode("utf-8") if isinstance(content, str) else content

        # 1. 尝试 SFTP
        try:
            with self.ssh.transfer(cancel_event, timeout=30):
                sftp = self.ssh.open_sftp(timeout=10, cancel_event=cancel_event)
                sftp.get_channel().settimeout(10)
                with sftp.open(remote_path, "wb") as f:
                    f.write(data)
                sftp.chmod(remote_path, mode)
                return
        except Exception as exc:
            LOGGER.info("SFTP upload to %s failed (%s), fallback to base64 chunked upload", remote_path, exc)

        # 2. 降级为 Base64 分片写入，彻底规避命令行溢出与 stdin 缺失
        rm_res = self.runner.run("rm_target", f"rm -f {remote_path}", timeout=10, cancel_event=cancel_event)
        if rm_res.cancelled:
            raise InterruptedError("上传已被用户取消。")

        b64_str = base64.b64encode(data).decode("ascii")
        chunk_size = 4096
        total_chunks = (len(b64_str) + chunk_size - 1) // chunk_size

        for i in range(total_chunks):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("上传已被用户取消。")
            chunk = b64_str[i * chunk_size : (i + 1) * chunk_size]
            append_cmd = f"printf '%s' '{chunk}' | base64 -d >> {remote_path}"
            res = self.runner.run("upload_chunk", append_cmd, timeout=15, cancel_event=cancel_event)
            if res.cancelled:
                raise InterruptedError("上传已被用户取消。")
            if res.exit_code != 0:
                raise RuntimeError(f"写入分片 {i + 1}/{total_chunks} 失败: {res.stderr or res.stdout}")

        chmod_res = self.runner.run("chmod_target", f"chmod {oct(mode)[2:]} {remote_path}", timeout=10, cancel_event=cancel_event)
        if chmod_res.exit_code != 0:
            raise RuntimeError(f"设置文件权限失败: {chmod_res.stderr or chmod_res.stdout}")

    def install_or_update(self, cancel_event=None) -> None:
        """上传/更新 /root/hcx_autocap.sh 并部署 /etc/init.d/luxiaoguan-autocap。"""
        script_content = read_local_script_content()
        # 确保目录存在
        mkdir_res = self.runner.run(
            "mkdir_root_cap",
            "mkdir -p /root/cap /etc/init.d",
            timeout=10,
            cancel_event=cancel_event,
        )
        if mkdir_res.exit_code != 0:
            raise RuntimeError(f"创建目录失败: {mkdir_res.stderr or mkdir_res.stdout}")

        # 上传 /root/hcx_autocap.sh
        self._upload_file(REMOTE_SCRIPT_PATH, script_content, mode=0o755, cancel_event=cancel_event)

        # 写入 /etc/init.d/luxiaoguan-autocap
        self._upload_file(REMOTE_SERVICE_PATH, SERVICE_INIT_SCRIPT, mode=0o755, cancel_event=cancel_event)

    def set_autostart_enabled(self, enable: bool, cancel_event=None) -> None:
        """设置开机自启开关状态。"""
        action = "enable" if enable else "disable"
        link_cmd = "[ ! -f /root/hcx_autocap.sh ] && [ -f /root/autocap.sh ] && ln -sf /root/autocap.sh /root/hcx_autocap.sh 2>/dev/null; "
        cmd = f"{link_cmd}chmod +x /etc/init.d/{SERVICE_NAME} 2>/dev/null; /etc/init.d/{SERVICE_NAME} {action}"
        res = self.runner.run(
            f"autocap_set_{action}",
            cmd,
            timeout=15,
            cancel_event=cancel_event,
        )
        if res.cancelled:
            raise InterruptedError("设置开机自启已取消。")
        if res.timed_out or res.exit_code != 0:
            raise RuntimeError(f"设置开机自启失败: {res.stderr or res.stdout}")

    def uninstall(self, cancel_event=None) -> None:
        """完全卸载自启服务与抓包脚本。保留 /root/cap/ 成果文件。"""
        cmd = (
            f"/etc/init.d/{SERVICE_NAME} stop 2>/dev/null; "
            f"/etc/init.d/{SERVICE_NAME} disable 2>/dev/null; "
            f"rm -f /etc/init.d/{SERVICE_NAME} /etc/rc.d/S*{SERVICE_NAME} {REMOTE_SCRIPT_PATH} /tmp/.hcx_autocap.*"
        )
        res = self.runner.run("uninstall_autocap", cmd, timeout=20, cancel_event=cancel_event)
        if res.cancelled:
            raise InterruptedError("卸载已取消。")
        if res.timed_out or res.exit_code != 0:
            raise RuntimeError(f"卸载失败: {res.stderr or res.stdout}")

    def start_script(self, cancel_event=None) -> None:
        """手动启动后台抓包脚本。优先使用系统服务启动；若服务尚未注册但脚本存在，直接脱离控制终端拉起。"""
        cmd = (
            f"if [ -x /etc/init.d/{SERVICE_NAME} ]; then "
            f"  /etc/init.d/{SERVICE_NAME} start; "
            "else "
            "  SCRIPT=\"\"; "
            "  if [ -f /root/hcx_autocap.sh ]; then SCRIPT=\"/root/hcx_autocap.sh\"; "
            "  elif [ -f /root/autocap.sh ]; then SCRIPT=\"/root/autocap.sh\"; fi; "
            "  if [ -z \"$SCRIPT\" ]; then "
            "    echo 'Error: 抓包脚本不存在' >&2; exit 1; "
            "  fi; "
            "  chmod +x \"$SCRIPT\"; "
            "  export PATH=\"/root/.winbox-tools/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH\"; "
            "  LOCK='/tmp/.hcx_autocap.lock'; "
            "  if [ -f \"$LOCK\" ]; then "
            "    PID=$(cat \"$LOCK\" 2>/dev/null); "
            "    if [ -n \"$PID\" ] && kill -0 \"$PID\" 2>/dev/null; then exit 0; fi; "
            "  fi; "
            "  if [ -x /usr/bin/nohup ]; then "
            "    VIEW=off nohup /bin/sh \"$SCRIPT\" >/dev/null 2>&1 & "
            "  else "
            "    ( trap '' HUP; VIEW=off /bin/sh \"$SCRIPT\" >/dev/null 2>&1 ) & "
            "  fi; "
            "fi"
        )
        res = self.runner.run("start_autocap_script", cmd, timeout=15, cancel_event=cancel_event)
        if res.cancelled:
            raise InterruptedError("启动抓包脚本已取消。")
        if res.timed_out or res.exit_code != 0:
            raise RuntimeError(f"启动抓包脚本失败: {res.stderr or res.stdout}")

    def start_service(self, cancel_event=None) -> None:
        """手动启动后台自启抓包服务。"""
        self.start_script(cancel_event=cancel_event)

    def stop_service(self, cancel_event=None) -> None:
        """手动停止后台自启抓包服务。"""
        cmd = f"/etc/init.d/{SERVICE_NAME} stop"
        res = self.runner.run("stop_autocap_service", cmd, timeout=25, cancel_event=cancel_event)
        if res.cancelled:
            raise InterruptedError("停止服务已取消。")
        if res.timed_out or res.exit_code != 0:
            raise RuntimeError(f"停止服务失败: {res.stderr or res.stdout}")

    def stop_and_cleanup_capture(self, cancel_event=None) -> None:
        """收尾并结束后台抓包：优雅收尾、安全封包、强力清理残留进程与监听接口（与启动脚本时的收尾规范完全一致）。"""
        cmd = (
            "LOCK='/tmp/.hcx_autocap.lock'; "
            "if [ -f \"$LOCK\" ]; then "
            "  PID=$(cat \"$LOCK\" 2>/dev/null); "
            "  if [ -n \"$PID\" ] && kill -0 \"$PID\" 2>/dev/null; then "
            "    kill -TERM \"$PID\" 2>/dev/null; "
            "    WAIT=0; "
            "    while [ \"$WAIT\" -lt 10 ]; do "
            "      kill -0 \"$PID\" 2>/dev/null || break; "
            "      sleep 1; "
            "      WAIT=$((WAIT + 1)); "
            "    done; "
            "    if kill -0 \"$PID\" 2>/dev/null; then kill -9 \"$PID\" 2>/dev/null; fi; "
            "  fi; "
            "fi; "
            "stale_pids=$(ps w 2>/dev/null | grep 'hcxdumptool' | grep -v grep | awk '{print $1}'); "
            "if [ -n \"$stale_pids\" ]; then "
            "  for p in $stale_pids; do kill -TERM \"$p\" 2>/dev/null; done; "
            "  sleep 1; "
            "  for p in $stale_pids; do kill -KILL \"$p\" 2>/dev/null; done; "
            "fi; "
            "for d in $(ls /sys/class/net 2>/dev/null | grep -E '^hcxmon[0-9]+'); do "
            "  iw dev \"$d\" del >/dev/null 2>&1; "
            "done; "
            "rm -f /tmp/.hcx_autocap.* /tmp/.hcx_*.pid"
        )
        res = self.runner.run("stop_and_cleanup_capture", cmd, timeout=25, cancel_event=cancel_event)
        if res.cancelled:
            raise InterruptedError("结束并收尾抓包已取消。")
        if res.timed_out or res.exit_code != 0:
            raise RuntimeError(f"收尾抓包失败: {res.stderr or res.stdout}")

    def pull_offline_artifacts(self, local_dest_dir: str, cancel_event=None) -> list[str]:
        """通过 SFTP 从 /root/cap/ 拉取所有 .pcapng 原始包与日志到本地 PC。"""
        local_path = Path(local_dest_dir)
        local_path.mkdir(parents=True, exist_ok=True)

        # 检查 /root/cap 目录
        check_res = self.runner.run(
            "list_cap_files",
            "ls -1 /root/cap 2>/dev/null",
            timeout=15,
            cancel_event=cancel_event,
        )
        if check_res.cancelled:
            raise InterruptedError("拉取已取消。")
        if check_res.exit_code != 0:
            return []

        filenames = [line.strip() for line in (check_res.stdout or "").splitlines() if line.strip()]
        target_files = [f for f in filenames if f.endswith(".pcapng") or f.endswith(".log") or f.endswith(".txt")]
        if not target_files:
            return []

        pulled_files = []
        with self.ssh.transfer(cancel_event, timeout=300):
            sftp = self.ssh.open_sftp(timeout=15, cancel_event=cancel_event)
            sftp.get_channel().settimeout(15)
            for fname in target_files:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("拉取文件已取消。")
                remote_file = posixpath.join(REMOTE_CAP_DIR, fname)
                dest_file = local_path / fname
                temp_file = local_path / f"{fname}.part"
                try:
                    sftp.get(remote_file, str(temp_file))
                    if temp_file.exists():
                        if dest_file.exists():
                            dest_file.unlink()
                        temp_file.rename(dest_file)
                        pulled_files.append(str(dest_file))
                except Exception as exc:
                    LOGGER.warning("Pull file %s failed: %s", fname, exc)
                    if temp_file.exists():
                        try:
                            temp_file.unlink()
                        except Exception:
                            pass
        return pulled_files


    def read_recent_session_log(self, max_lines: int = 35, cancel_event=None) -> str:
        """读取远端最新的抓包会话日志末尾文本。"""
        cmd = (
            "LATEST=$(ls -1t /root/cap/script_*.log 2>/dev/null | head -n 1); "
            f"if [ -n \"$LATEST\" ] && [ -f \"$LATEST\" ]; then tail -n {max_lines} \"$LATEST\" 2>/dev/null; fi"
        )
        res = self.runner.run("read_autocap_log", cmd, timeout=6, cancel_event=cancel_event)
        return (res.stdout or "").strip()


if __name__ == "__main__":
    print(f"本地内置抓包脚本: {get_local_script_path()}")
    print(f"本地脚本版本: {read_local_script_version()}")
