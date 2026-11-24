"""System WPS client path (wpa_supplicant + wpa_cli) for 秒 Pin.

This mirrors how phone apps send a PIN: a real station interface joins the
target and wpa_supplicant performs the WPS registration. It never replaces
firmware packages; when the firmware lacks WPS support the caller receives a
clear reason and can switch back to the reaver path.
"""

import re
import time
from secrets import token_hex

from .command_builder import normalize_bssid, quote_argument
from .command_runner import require_command_completion

# Linux interface names are limited to 15 characters (IFNAMSIZ - 1).
_MAX_NAME = 15
_INTERFACE_PREFIX = "wbsta"
_WPS_UNSUPPORTED = "unknown command"
_FAILURE_MARKERS = (
    "received wsc_nack",
    "wsc nack",
    "wps-fail",
    "wps: registration failed",
    "wps: registrar ",
    "wps: disassociate",
    "wps: timeout",
)
_ASSOC_MARKERS = (
    "trying to associate",
    "associated with",
)
_ASSOC_WATCHDOG_SECONDS = 25
_WIPHY = re.compile(r"^\s*wiphy\s+(\d+)\s*$", re.MULTILINE)
# wpa_supplicant -K 会把 WPS 凭据里的网络密钥以 hexdump 打在调试日志里。
# 例：wlan0: WPS: Network Key - hexdump(len=10): 31 32 33 34 35 36 37 38 39 30
_NETWORK_KEY = re.compile(
    r"WPS: Network Key - hexdump\(len=(\d+)\):\s*((?:[0-9a-fA-F]{2}\s*)+)")
_KEY_LINE = "WPS: Network Key"


class WpsSystemUnavailable(RuntimeError):
    """Firmware or driver cannot provide the system WPS path."""

    def __init__(self, message, kind="generic"):
        super().__init__(message)
        self.kind = str(kind or "generic")


class WpsSystemClient:
    """One temporary station interface + wpa_supplicant instance per target.

    副作用：在远端创建并销毁临时受管接口、临时配置目录和自有 wpa_supplicant
    进程；只按本会话登记的 PID 停止，不触碰其他会话的接口和进程。
    """

    def __init__(self, command_runner, process_manager):
        self.command_runner = command_runner
        self.process_manager = process_manager
        self.interface = ""
        self.config_path = ""
        self.control_dir = ""
        self.log_path = ""
        self.handle = None

    # ---------------------------------------------------------------- helpers

    def _run(self, name, command, timeout, cancel_event, *, allow_failure=False):
        result = self.command_runner.run(name, command, timeout=timeout, cancel_event=cancel_event)
        require_command_completion(result)
        if result.exit_code != 0 and not allow_failure:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise WpsSystemUnavailable(
                f"系统 WPS 准备失败：{detail[-1] if detail else result.exit_code}",
                kind="setup")
        return result

    @staticmethod
    def _reply(result):
        """wpa_cli 单次调用的最后一行有效回应。"""

        for line in reversed(str(result.stdout or "").splitlines()):
            text = line.strip()
            if text:
                return text
        return str(result.stderr or "").strip()

    def _missing_binary(self, cancel_event):
        result = self.command_runner.run(
            "wps_system_probe",
            "command -v wpa_supplicant || echo MISSING-supplicant; "
            "command -v wpa_cli || echo MISSING-cli; "
            "command -v iw || echo MISSING-iw",
            timeout=10, cancel_event=cancel_event)
        require_command_completion(result)
        text = str(result.stdout or "")
        if "MISSING-supplicant" in text or "MISSING-cli" in text:
            return ("这台路由器缺少系统 WPS 组件（wpa_supplicant / wpa_cli）。"
                    "请先在依赖页安装常用依赖，或把发送方式改回 reaver。")
        if "MISSING-iw" in text:
            return "这台路由器缺少 iw 命令，无法建立临时客户端接口。"
        return ""

    def _create_interface(self, monitor_interface, cancel_event):
        """优先按 phy 添加受管接口；旧固件不支持时退回按设备添加。"""

        name = quote_argument(self.interface)
        info = self.command_runner.run(
            "wps_system_phy",
            f"iw dev {quote_argument(str(monitor_interface))} info 2>/dev/null || true",
            timeout=10, cancel_event=cancel_event)
        require_command_completion(info)
        match = _WIPHY.search(str(info.stdout or ""))
        commands = []
        if match:
            commands.append(f"iw phy phy{match.group(1)} interface add {name} type managed")
        commands.append(
            f"iw dev {quote_argument(str(monitor_interface))} interface add {name} type managed")
        last = ""
        for command in commands:
            result = self.command_runner.run(
                "wps_system_iface", f"{command} 2>&1 && ip link set {name} up",
                timeout=15, cancel_event=cancel_event)
            require_command_completion(result)
            if result.exit_code == 0:
                return
            last = (str(result.stderr or "") + str(result.stdout or "")).strip()
        raise WpsSystemUnavailable(
            "无法建立临时客户端接口：当前无线芯片或驱动不支持同时做热点和客户端连接。"
            + (f"（{last}）" if last else ""), kind="interface")

    def _write_config(self, cancel_event):
        # 不写 update_config=1：精简版 wpad 把这个选项编译掉了，读到会直接退出。
        # 密码改从 wpa_supplicant -K 的调试日志读取，不需要回写配置文件。
        self._run(
            "wps_system_config",
            "mkdir -p " + quote_argument(self.control_dir) + " && "
            "printf '%s\\n' "
            + quote_argument(f"ctrl_interface={self.control_dir}") + " "
            + quote_argument("ctrl_interface_group=0") + " "
            + quote_argument("ap_scan=1") + " > " + quote_argument(self.config_path),
            10, cancel_event)

    # ------------------------------------------------------------ lifecycle

    def open(self, workspace, monitor_interface, session_id="", cancel_event=None):
        """建立临时客户端接口并启动自有 wpa_supplicant；失败抛 WpsSystemUnavailable。"""

        reason = self._missing_binary(cancel_event)
        if reason:
            raise WpsSystemUnavailable(reason, kind="missing_tool")
        remote_dir = str(workspace.remote_dir).rstrip("/")
        self.interface = (_INTERFACE_PREFIX + token_hex(3))[:_MAX_NAME]
        self.control_dir = f"{remote_dir}/wpa-ctrl"
        self.config_path = f"{remote_dir}/wpa-system.conf"
        self._create_interface(monitor_interface, cancel_event)
        self._write_config(cancel_event)
        command = (
            f"exec wpa_supplicant -Dnl80211 -i{quote_argument(self.interface)} "
            f"-c{quote_argument(self.config_path)} -d -K"
        )
        self.handle = self.process_manager.start(
            "wpa_supplicant", command, f"{session_id or 'wps'}-sys{token_hex(2)}",
            remote_dir, cancel_event, max_runtime=3600)
        self.log_path = getattr(self.handle, "log_path", "") or f"{remote_dir}/wpa_supplicant.log"
        if not self._wait_control(cancel_event):
            tail = self._sanitize_log_tail(self._log_tail(cancel_event))
            self.close()
            raise WpsSystemUnavailable(
                "系统 WPS 客户端没有启动成功。" + (f"（{tail}）" if tail else ""),
                kind="startup")
        self._require_wps_support(cancel_event)

    @staticmethod
    def _sanitize_log_tail(text):
        """失败提示只保留少量非敏感行，避免把 -K 日志里的密钥带进消息。"""

        lines = [
            line for line in str(text or "").splitlines()
            if _KEY_LINE not in line and "psk=" not in line.lower()
        ]
        return "\n".join(lines[-12:])

    def _wait_control(self, cancel_event, seconds=12):
        deadline = time.monotonic() + seconds
        target = quote_argument(f"{self.control_dir}/{self.interface}")
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("WPS 任务已取消。")
            result = self.command_runner.run(
                "wps_system_control", f"test -S {target}", timeout=8, cancel_event=cancel_event)
            require_command_completion(result)
            if result.exit_code == 0:
                return True
            if self.handle is not None and not self.process_manager.is_running(self.handle, cancel_event):
                return False
            time.sleep(0.5)
        return False

    def _require_wps_support(self, cancel_event):
        # 不带合法 PIN 的调用不会启动 WPS 会话：支持 WPS 时返回 FAIL（参数错误），
        # 未编译 WPS 时落入通用分支返回 UNKNOWN COMMAND。
        reply = self._wpa_cli("wps_reg 0 0", cancel_event, timeout=20)
        lowered = reply.lower()
        if _WPS_UNSUPPORTED in lowered:
            raise WpsSystemUnavailable(
                "这台路由器的无线组件是精简版（wpad-basic），不含 WPS 功能，系统 WPS 用不了。"
                "请在路由器上安装完整版 wpad，或把发送方式改回 reaver。",
                kind="basic_wpad")
        if not lowered:
            raise WpsSystemUnavailable(
                "系统 WPS 没有回应控制命令，请重试或改回 reaver。",
                kind="control")
        if "failed to connect" in lowered or "not connected to wpa_supplicant" in lowered:
            raise WpsSystemUnavailable(
                "系统 WPS 控制通道没有建立起来，请重试或改回 reaver。",
                kind="control")

    def close(self):
        """停止自有进程、删除临时接口和临时文件；每步独立，异常不阻断后续清理。"""

        errors = []
        if self.handle is not None:
            try:
                self.process_manager.stop(self.handle, None, graceful_signal="INT")
            except Exception as exc:
                errors.append(f"停止系统 WPS 进程失败：{exc}")
            finally:
                self.handle = None
        if self.interface:
            name = quote_argument(self.interface)
            for command in (f"ip link set {name} down", f"iw dev {name} del"):
                try:
                    self.command_runner.run(
                        "wps_system_cleanup", command, timeout=10, cancel_event=None)
                except Exception as exc:
                    errors.append(f"清理临时接口失败：{exc}")
        if self.control_dir:
            try:
                self.command_runner.run(
                    "wps_system_cleanup",
                    f"rm -rf {quote_argument(self.control_dir)} {quote_argument(self.config_path)}",
                    timeout=10, cancel_event=None)
            except Exception as exc:
                errors.append(f"清理临时配置失败：{exc}")
        if self.log_path:
            try:
                self.command_runner.run(
                    "wps_system_cleanup",
                    f"rm -f {quote_argument(self.log_path)}",
                    timeout=10, cancel_event=None)
            except Exception as exc:
                errors.append(f"清理临时日志失败：{exc}")
        self.interface = ""
        self.log_path = ""
        return errors

    # ---------------------------------------------------------------- traffic

    def _wpa_cli(self, arguments, cancel_event, timeout=15):
        command = (f"wpa_cli -p {quote_argument(self.control_dir)} "
                   f"-i {quote_argument(self.interface)} {arguments}")
        result = self.command_runner.run(
            "wps_system_cli", command, timeout=timeout, cancel_event=cancel_event)
        require_command_completion(result)
        return self._reply(result)

    def _log_tail(self, cancel_event, lines=40):
        if not self.log_path:
            return ""
        result = self.command_runner.run(
            "wps_system_log",
            f"tail -n {int(lines)} {quote_argument(self.log_path)} 2>/dev/null || true",
            timeout=10, cancel_event=cancel_event)
        return str(result.stdout or "")

    def _log_size(self, cancel_event):
        """当前日志字节数；拿不到按 0 处理，不影响后续判断。"""

        if not self.log_path:
            return 0
        result = self.command_runner.run(
            "wps_system_log_size",
            f"wc -c < {quote_argument(self.log_path)} 2>/dev/null || echo 0",
            timeout=10, cancel_event=cancel_event)
        text = str(result.stdout or "").strip().split()
        try:
            return max(0, int(text[0]))
        except (IndexError, ValueError):
            return 0

    def _log_since(self, offset, cancel_event):
        """只读取本次尝试开始之后的日志，避免上一候选的失败标记误判。"""

        if not self.log_path:
            return ""
        start = max(0, int(offset)) + 1
        result = self.command_runner.run(
            "wps_system_log_since",
            f"tail -c +{start} {quote_argument(self.log_path)} 2>/dev/null | tail -c 32768 || true",
            timeout=10, cancel_event=cancel_event)
        return str(result.stdout or "")

    @staticmethod
    def _parse_network_key(line):
        """把 wpa_supplicant -K 的 hexdump 行还原成密码。"""

        match = _NETWORK_KEY.search(str(line or ""))
        if match is None:
            return ""
        try:
            raw = bytes.fromhex(match.group(2).replace(" ", ""))
        except ValueError:
            return ""
        try:
            value = raw.decode("ascii")
        except UnicodeDecodeError:
            return ""
        value = value.strip("\r\n")
        if 8 <= len(value) <= 63:
            return value
        if len(value) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in value):
            return value
        return ""

    def _credentials(self, offset, cancel_event):
        """从 wpa_supplicant -K 的调试日志里读 WPS 下发的网络密钥。

        精简版 wpad 不编译 update_config，写不回配置文件，所以不能读 psk=。
        这里只读本次尝试新增的日志，避免上一候选的密钥被误当成新结果。
        """

        if not self.log_path:
            return ""
        start = max(1, int(offset) - 512)
        result = self.command_runner.run(
            "wps_system_credentials",
            f"tail -c +{start} {quote_argument(self.log_path)} 2>/dev/null "
            f"| grep -F '{_KEY_LINE}' | tail -n 1 || true",
            timeout=10, cancel_event=cancel_event)
        require_command_completion(result)
        return self._parse_network_key(result.stdout)

    def attempt(self, bssid, pin, seconds, cancel_event=None):
        """发一个 PIN；返回 ('confirmed'|'rejected'|'timeout', pin, psk)。"""

        address = normalize_bssid(bssid)
        offset = self._log_size(cancel_event)
        reply = self._wpa_cli(f"wps_reg {address} {pin}", cancel_event)
        lowered = reply.lower()
        if _WPS_UNSUPPORTED in lowered:
            raise WpsSystemUnavailable(
                "这台路由器的无线组件是精简版（wpad-basic），不含 WPS 功能，系统 WPS 用不了。"
                "请在路由器上安装完整版 wpad，或把发送方式改回 reaver。",
                kind="basic_wpad")
        if "failed to connect" in lowered or "not connected to wpa_supplicant" in lowered:
            raise WpsSystemUnavailable(
                "系统 WPS 控制通道已断开，请重试或改回 reaver。",
                kind="control")
        if lowered.startswith("fail"):
            return "rejected", "", ""
        # 秒 Pin 的“0 = 不限制”在系统 WPS 下没有意义：一次 WPS 注册本身是短交互，
        # 这里给每个候选最多 120 秒，避免一次失败把整轮卡死。
        budget = int(seconds or 0)
        started = time.monotonic()
        deadline = started + (budget if budget > 0 else 120)
        associated = False
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("WPS 任务已取消。")
            psk = self._credentials(offset, cancel_event)
            if psk:
                return "confirmed", pin, psk
            tail = self._log_since(offset, cancel_event).lower()
            if any(marker in tail for marker in _ASSOC_MARKERS):
                associated = True
            if any(marker in tail for marker in _FAILURE_MARKERS):
                return "rejected", "", ""
            if not associated and time.monotonic() - started > _ASSOC_WATCHDOG_SECONDS:
                # 连都连不上就别耗满整个候选预算：多半是同一芯片上已有监听接口、
                # 信道受限或目标不在范围内。
                raise WpsSystemUnavailable(
                    "系统 WPS 无法关联到目标 Wi-Fi（可能受临时监听接口、信道或信号限制）。"
                    "请把发送方式改回 reaver 后重试。",
                    kind="association")
            if cancel_event is not None:
                cancel_event.wait(1)
            else:
                time.sleep(1)
        try:
            self._wpa_cli("wps_cancel", cancel_event, timeout=10)
        except Exception:
            pass
        return "timeout", "", ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False
