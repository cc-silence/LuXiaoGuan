"""OpenWrt 自动抓包多路前台直连控制器与全局资源守卫服务。

职责：
1. 射频智能接管与多路并发：
   - 识别「空闲」与「正在使用」射频；
   - 对「正在使用」射频经用户授权后执行原子级无线接管与回滚；
   - 通过独立 SSH PTY/Exec 通道流式接收 hcxdumptool 输出；
2. 零 Flash 磨损日志落盘：
   - 前台模式下流式输出由 PC 客户端追加写入本地日志文件，远端不写日志；
3. 全局资源双重守卫 (SpaceGuard & MemGuard)：
   - 探针监控远端内存与磁盘余量；
   - 内存 < 12MB 或磁盘 < 5MB 黄色预警；
   - 内存 < 6MB 或磁盘 < 2MB 触发紧急停机并拉包保护；
4. 开机自启服务管理集成。
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
import secrets
import socket
import threading
import time
from typing import Dict, List, Optional
from uuid import uuid4

from PySide6.QtCore import QObject, Signal, QTimer

from modules.capture_services.capture_logging import LOGGER
from modules.capture_services.openwrt.models import OpenWrtRadioInfo
from .auto_capture_boot_manager import AutoCaptureBootManager, AutoCaptureBootStatus
from .auto_capture_log_analyzer import AutoCaptureLogAnalyzer, AutoCaptureDiagnosticReport
from .radio_takeover import OpenWrtRadioTakeover, RadioTakeoverRecord


@dataclass
class RadioCaptureStats:
    """单路射频实时指标数据。"""

    duration_seconds: int = 0
    pmkid_count: int = 0
    eapol_count: int = 0
    file_size_bytes: int = 0
    current_channel: str = ""
    pid: Optional[int] = None
    state: str = "idle"  # idle | starting | capturing | stopping | error
    error_message: str = ""


def frequency_to_channel(freq: int) -> str:
    """根据频率(MHz)换算 Wi-Fi 信道编号。"""
    try:
        freq = int(freq)
    except (ValueError, TypeError):
        return str(freq)

    # 2.4 GHz
    if freq == 2484:
        return "14"
    if 2412 <= freq <= 2472 and (freq - 2407) % 5 == 0:
        return str((freq - 2407) // 5)

    # 5 GHz
    if 5000 <= freq <= 5900 and (freq - 5000) % 5 == 0:
        return str((freq - 5000) // 5)

    # 6 GHz (Wi-Fi 6E)
    if 5955 <= freq <= 7115 and (freq - 5950) % 5 == 0:
        return str((freq - 5950) // 5)

    return str(freq)


def format_mac(raw: str) -> str:
    """归一化 MAC 地址为标准大写冒号格式 (如 AA:BB:CC:DD:EE:FF)。"""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(cleaned) == 12:
        return ":".join(cleaned[i : i + 2].upper() for i in range(0, 12, 2))
    return raw.upper()


class AutoCaptureRadioSession:
    """管理单路射频前台实时抓包的 SSH 通道、流式读取与本地日志落盘。"""

    def __init__(self, radio_info: OpenWrtRadioInfo, runner, ssh_client, output_dir: Path, service):
        self.radio_info = radio_info
        self.runner = runner
        self.ssh_client = ssh_client
        self.output_dir = output_dir
        self.service = service
        self.radio_name = radio_info.name

        # 智能判定底层物理 phy 与监听口名称
        idx_match = re.search(r"\d+", self.radio_name)
        self.idx = idx_match.group(0) if idx_match else "0"
        self.mon_iface = f"hcxmon{self.idx}"
        self.phy = radio_info.interface or f"phy{self.idx}"
        self._hcx_bin = "hcxdumptool"
        self.remote_cap_file = ""
        self.local_log_file: Optional[Path] = None

        self.stats = RadioCaptureStats()
        self.takeover_record: Optional[RadioTakeoverRecord] = None
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._start_time: float = 0
        self._channel = None
        self._pmkid_set = set()
        self._eapol_set = set()
        self._created_stubs: list[str] = []

    def start(self, channels: str, allow_takeover: bool):
        """启动该路抓包会话。"""
        if self.stats.state in {"starting", "capturing"}:
            return

        self.stats.state = "starting"
        self.stats.error_message = ""
        self.service.radioStateChanged.emit(self.radio_name, "starting")

        self._stop_event.clear()
        self._pmkid_set.clear()
        self._eapol_set.clear()

        # 启动后台工作线程
        self._worker_thread = threading.Thread(
            target=self._run_capture_pipeline,
            args=(channels, allow_takeover),
            name=f"AutoCapture_{self.radio_name}",
            daemon=True,
        )
        self._worker_thread.start()

    def stop(self):
        """请求停止该路会话。"""
        if self.stats.state in {"idle", "error"}:
            return
        self.stats.state = "stopping"
        self.service.radioStateChanged.emit(self.radio_name, "stopping")
        self._stop_event.set()

    def _run_capture_pipeline(self, channels: str, allow_takeover: bool):
        """抓包完整流程：接管 -> 建口 -> 起 hcxdumptool -> 读流 -> 收尾回滚 -> 拉成果。"""
        takeover = OpenWrtRadioTakeover(self.runner)
        try:
            # 0. 前置环境与连接检查
            client = getattr(self.ssh_client, "_client", None)
            if client is None or not getattr(self.ssh_client, "connected", False):
                raise RuntimeError("路由器尚未建立 SSH 连接，无法启动抓包。请确认连接状态。")

            # 确保远端 /root/cap 目录存在，防止 hcxdumptool 报 No such file or directory
            mkdir_res = self.runner.run("mkdir_cap", "mkdir -p /root/cap", timeout=8, cancel_event=self._stop_event)
            if mkdir_res.exit_code != 0:
                raise RuntimeError(f"创建远端抓包目录 /root/cap 失败: {mkdir_res.stderr or mkdir_res.stdout}")

            # 探测远端是否存在 hcxdumptool 抓包二进制，并记录绝对/可用路径
            bin_res = self.runner.run(
                "find_hcx_bin",
                "if command -v hcxdumptool >/dev/null 2>&1; then command -v hcxdumptool; elif [ -x /root/.winbox-tools/bin/hcxdumptool ]; then printf '/root/.winbox-tools/bin/hcxdumptool'; fi",
                timeout=8,
                cancel_event=self._stop_event,
            )
            found_bin = (bin_res.stdout or "").strip()
            if not found_bin:
                raise RuntimeError("路由器尚未安装抓包工具 hcxdumptool！\n请先在页面部署自启脚本，或在【工具管理】页面安装兼容的 hcxdumptool。")
            self._hcx_bin = found_bin

            # 动态核准该射频对应的真实底层物理 phy (对齐 hcx_autocap.sh 三级动态探测)
            default_phy = self.phy if (self.phy and re.fullmatch(r"phy\d+", self.phy)) else f"phy{self.idx}"
            resolve_phy_script = (
                f"_real_phy=$(uci get wireless.{self.radio_name}.phy 2>/dev/null); "
                f"if [ -z \"$_real_phy\" ]; then "
                f"  _dev_path=$(uci get wireless.{self.radio_name}.path 2>/dev/null); "
                f"  if [ -n \"$_dev_path\" ]; then "
                f"    for _p in /sys/class/ieee80211/phy*; do "
                f"      [ -d \"$_p\" ] || continue; "
                f"      _target=$(readlink -f \"$_p/device\" 2>/dev/null || ls -l \"$_p/device\" 2>/dev/null); "
                f"      case \"$_target\" in *\"$_dev_path\"*) _real_phy=$(basename \"$_p\"); break ;; esac; "
                f"    done; "
                f"  fi; "
                f"fi; "
                f"[ -z \"$_real_phy\" ] && _real_phy=\"{default_phy}\"; "
                f"printf '%s' \"$_real_phy\""
            )
            phy_res = self.runner.run("resolve_real_phy", resolve_phy_script, timeout=6, cancel_event=self._stop_event)
            resolved_phy = (phy_res.stdout or "").strip()
            if re.fullmatch(r"phy\d+", resolved_phy):
                self.phy = resolved_phy

            # 1. 如果射频非空闲，且用户允许接管，执行无线安全接管事务
            is_free = bool(self.radio_info.free)
            if not is_free:
                if not allow_takeover:
                    raise RuntimeError(f"射频 {self.radio_name} 当前正在使用，且未勾选允许接管，已拒绝启动。")
                self.service.messageGenerated.emit(f"正在安全接管无线射频 {self.radio_name}...", "info")
                session_id = uuid4().hex
                record = takeover.prepare_config_takeover(self.phy, self.radio_name, session_id, self._stop_event)
                takeover.takeover_prepared(record, self._stop_event)
                self.takeover_record = record

            # 2. 清理旧残留监听口与残留外部抓包进程 (严格对齐 hcx_autocap.sh，绝不误伤或关闭同 PHY 其他接口，不误杀其它路抓包)
            cleanup_script = (
                f"stale_pids=$(ps w 2>/dev/null | grep 'hcxdumptool' | grep -w '{self.mon_iface}' | grep -v grep | awk '{{print $1}}'); "
                f"if [ -n \"$stale_pids\" ]; then "
                f"  for p in $stale_pids; do kill -TERM \"$p\" 2>/dev/null; done; "
                f"  sleep 1; "
                f"  for p in $stale_pids; do kill -KILL \"$p\" 2>/dev/null; done; "
                f"fi; "
                f"iw dev {self.mon_iface} del >/dev/null 2>&1"
            )
            self.runner.run("sweep_stale", cleanup_script, timeout=8, cancel_event=self._stop_event)

            # 清理本 PHY 上冲突的旧业务接口与旧占位接口(防止多接口或孤儿 AP 接口导致 hcxdumptool 报 failed to arm interface)
            cleanup_stale_ap = (
                f"for _stale in $(iw dev 2>/dev/null | grep -E \"phy#{self.idx}\\b\" -A 10 | grep 'Interface ' | awk '{{print $2}}'); do "
                f"  case \"$_stale\" in "
                f"    {self.mon_iface}) : ;; "
                f"    *) iw dev \"$_stale\" del 2>/dev/null ;; "
                f"  esac; "
                f"done"
            )
            self.runner.run("cleanup_stale_ap", cleanup_stale_ap, timeout=6, cancel_event=self._stop_event)

            # 3. 下发标准内核纯净白名单 BPF 过滤器规则 (仅放行信标与四次握手，过滤大流量视频/下载数据帧)
            bpf_payload = (
                "48 0 0 0\n21 1 0 0\n6 0 0 0\n48 0 0 3\n100 0 0 8\n7 0 0 0\n48 0 0 2\n"
                "76 0 0 0\n53 1 0 8\n6 0 0 0\n2 0 0 0\n7 0 0 0\n80 0 0 0\n21 4 0 128\n"
                "21 3 0 80\n21 8 0 8\n21 7 0 136\n6 0 0 0\n80 0 0 1\n84 0 0 199\n"
                "21 1 0 0\n6 0 0 0\n80 0 0 35\n5 0 0 35\n80 0 0 1\n84 0 0 196\n"
                "21 1 0 0\n6 0 0 0\n80 0 0 1\n84 0 0 3\n21 0 1 3\n6 0 0 0\n"
                "80 0 0 22\n84 0 0 15\n21 1 0 0\n6 0 0 0\n80 0 0 0\n21 1 0 136\n"
                "5 0 0 7\n80 0 0 24\n84 0 0 128\n21 1 0 0\n6 0 0 0\n135 0 0 0\n"
                "4 0 0 2\n7 0 0 0\n64 0 0 24\n21 1 0 2863268608\n6 0 0 0\n"
                "64 0 0 28\n21 1 0 34958\n6 0 0 0\n80 0 0 33\n21 1 0 3\n"
                "6 0 0 0\n72 0 0 34\n53 1 0 95\n6 0 0 0\n80 0 0 130\n97 0 0 0\n"
                "80 0 0 22\n84 0 0 15\n21 1 0 0\n6 0 0 0\n6 0 0 4294967295\n"
            )
            bpf_file = f"/tmp/.hcx_{self.mon_iface}.bpf"
            self.runner.run("init_bpf", f"cat << 'EOF' > {bpf_file}\n{bpf_payload}EOF", timeout=6)

            # 4. 生成有效单播 MAC 并创建监控接口 (严格对齐 hcx_autocap.sh 原生原子命令与 1 秒稳定延时)
            rand_bytes = secrets.token_bytes(5)
            wlan_mac = f"02:{rand_bytes[0]:02x}:{rand_bytes[1]:02x}:{rand_bytes[2]:02x}:{rand_bytes[3]:02x}:{rand_bytes[4]:02x}"

            create_cmd = (
                f"iw phy {self.phy} interface add {self.mon_iface} type monitor addr {wlan_mac} && "
                f"ip link set {self.mon_iface} up && sleep 1"
            )
            create_res = self.runner.run(
                "add_mon_iface",
                create_cmd,
                timeout=12,
                cancel_event=self._stop_event,
            )
            if create_res.exit_code != 0:
                fallback_cmd = (
                    f"iw phy {self.phy} interface add {self.mon_iface} type monitor && "
                    f"ip link set {self.mon_iface} up && sleep 1"
                )
                create_res = self.runner.run(
                    "add_mon_iface_fallback",
                    fallback_cmd,
                    timeout=12,
                    cancel_event=self._stop_event,
                )
                if create_res.exit_code != 0:
                    raise RuntimeError(
                        f"在射频 {self.phy} 上创建监听接口 {self.mon_iface} 失败: "
                        f"{create_res.stderr or create_res.stdout or '内核或网卡驱动拒绝创建 monitor 模式'}"
                    )

            # 检查接口在内核网络协议栈中是否已就绪
            chk_link = self.runner.run(
                "chk_mon_link",
                f"ip link show dev {self.mon_iface} 2>/dev/null",
                timeout=6,
                cancel_event=self._stop_event,
            )
            if chk_link.exit_code != 0:
                raise RuntimeError(
                    f"监听接口 {self.mon_iface} 创建后在网络接口列表中未呈现，"
                    f"请检查无线硬件驱动是否支持 monitor 模式。"
                )

            # 防御 hcxdumptool 6.3.x 内部 Multi-PHY 枚举 Bug：
            # 若系统中其他空闲物理卡处于零接口状态，hcxdumptool 会在 get_interfacelist 时
            # 误判首个 PHY index 为 0 并直接报 failed to get interface list 崩溃退出。
            # 为零接口物理卡挂载极轻量占位 stub 接口，保证工具正确枚举系统无线卡列表。
            stub_script = (
                "for _p in /sys/class/ieee80211/*; do "
                "  [ -d \"$_p\" ] || continue; "
                "  _phy=$(basename \"$_p\"); "
                "  _num=\"${_phy#phy}\"; "
                "  if ! iw dev 2>/dev/null | grep -q \"phy#${_num}\"; then "
                "    _sname=\"hcxstub${_num}\"; "
                "    _rand_mac=\"02:$(hexdump -n 5 -e '/1 \":%02x\"' /dev/urandom 2>/dev/null | cut -c 2-)\"; "
                "    [ -z \"$_rand_mac\" ] && _rand_mac=\"02:77:88:99:aa:bb\"; "
                "    if iw phy \"$_phy\" interface add \"$_sname\" type monitor addr \"$_rand_mac\" 2>/dev/null && "
                "       ip link set \"$_sname\" up 2>/dev/null; then "
                "      echo \"STUB_CREATED:$_sname\"; "
                "    fi; "
                "  fi; "
                "done"
            )
            stub_res = self.runner.run("ensure_phy_stubs", stub_script, timeout=8, cancel_event=self._stop_event)
            if stub_res.stdout:
                for line in stub_res.stdout.splitlines():
                    if line.startswith("STUB_CREATED:"):
                        stub_name = line.split(":", 1)[1].strip()
                        if stub_name and stub_name not in self._created_stubs:
                            self._created_stubs.append(stub_name)

            # 5. 准备输出文件与本地日志文件
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.remote_cap_file = f"/root/cap/{self.mon_iface}_{stamp}.pcapng"

            log_dir = self.output_dir / "autocap_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self.local_log_file = log_dir / f"run_{self.mon_iface}_{stamp}.log"

            # 6. 对齐 hcx_autocap.sh 多档尝试阶梯 (next_attempt) 自愈驱动兼容问题
            attempts = self._build_attempt_commands(channels, bpf_file)

            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise RuntimeError("SSH 传输通道失效，请重新连接路由器")

            started = False
            diagnostics: list[str] = []
            for desc, cmd_str in attempts:
                if self._stop_event.is_set():
                    return

                self.service.radioStreamOutput.emit(
                    self.radio_name,
                    f"▶ 正在尝试启动抓包引擎 (档位: {desc})...\n",
                )
                wrapped_cmd = f"sh -c 'echo $$ > /tmp/.hcx_{self.mon_iface}.pid; exec {cmd_str}'"

                self._channel = transport.open_session(timeout=10)
                self._channel.get_pty(term="vt100", width=120, height=40)
                self._channel.settimeout(1.0)
                self._channel.exec_command(wrapped_cmd)

                # 耐心等待 3.5 秒检验是否闪退 (对齐 hcx_autocap.sh verify_captures 与 netlink 物理能力探测耗时)
                crashed = False
                err_text = ""
                exit_code = 0
                for _ in range(7):
                    time.sleep(0.5)
                    if self._channel.exit_status_ready():
                        crashed = True
                        exit_code = self._channel.recv_exit_status()
                        break

                if crashed:
                    chunks = []
                    try:
                        while self._channel.recv_ready():
                            chunk = self._channel.recv(4096).decode("utf-8", errors="replace")
                            if not chunk:
                                break
                            chunks.append(chunk)
                    except Exception:
                        pass
                    err_text = "".join(chunks).strip()
                    core_lines = [
                        l.strip() for l in err_text.splitlines()
                        if l.strip()
                        and not l.startswith("This is")
                        and not l.startswith("usage")
                        and not l.startswith("Please be")
                        and not l.startswith("Requesting")
                        and not l.startswith("$ hcxdumptool")
                    ]
                    core_err = " | ".join(core_lines[:3]) if core_lines else (err_text[:200] or "进程异常退出")
                    diag_entry = f"• [{desc}] (代码 {exit_code}): {core_err}"
                    diagnostics.append(diag_entry)
                    LOGGER.warning("Auto capture attempt failed on %s: %s", self.radio_name, diag_entry)

                    self.service.radioStreamOutput.emit(
                        self.radio_name,
                        f"⚠️ {desc} 未能稳定运行 (代码 {exit_code}):\n{err_text or '驱动异常退出'}\n正在自动切换下一档自愈...\n",
                    )
                    try:
                        self._channel.close()
                    except Exception:
                        pass
                    # 清理空文件与残留 PID，并幂等确保接口处于 UP 状态 (对齐 hcx_autocap.sh，绝不粗暴 down 网卡导致驱动状态机重置)
                    self.runner.run(
                        "reset_iface",
                        f"[ ! -s {self.remote_cap_file} ] && rm -f {self.remote_cap_file}; "
                        f"kill -9 $(cat /tmp/.hcx_{self.mon_iface}.pid 2>/dev/null) 2>/dev/null; "
                        f"rm -f /tmp/.hcx_{self.mon_iface}.pid; "
                        f"ip link set {self.mon_iface} up 2>/dev/null; "
                        f"sleep 0.5",
                        timeout=5,
                    )
                    continue

                # 持续平稳运行超过 3.5 秒，代表该档位完全适配该网卡硬件与驱动！
                started = True
                break

            if not started:
                diag_summary = "\n".join(diagnostics) if diagnostics else "网卡驱动拒绝 monitor 模式或接口正忙"
                raise RuntimeError(
                    f"已试遍所有抓包档位，hcxdumptool 仍未能稳定运行。\n"
                    f"各档位运行诊断详情:\n{diag_summary}"
                )

            first_ch = channels.split(",")[0].strip() if channels else ""
            if first_ch:
                self.stats.current_channel = first_ch
            else:
                self.stats.current_channel = "正在探测..."

            self._start_time = time.monotonic()
            self.stats.state = "capturing"
            self.service.radioStateChanged.emit(self.radio_name, "capturing")
            self.service.messageGenerated.emit(f"射频 {self.radio_name} 抓包已启动 ({self.mon_iface})", "success")
            self.service.radioStatsUpdated.emit(self.radio_name, self._build_stats_dict())

            # 7. 实时流式读取输出并写入本地日志
            self._stream_loop()

        except Exception as exc:
            LOGGER.exception("Auto capture session failed on %s", self.radio_name)
            self.stats.state = "error"
            self.stats.error_message = str(exc)
            # 在对应卡片的控制台醒目打印错误日志
            self.service.radioStreamOutput.emit(
                self.radio_name,
                f"\n❌ [抓包启动失败] {exc}\n",
            )
            self.service.radioStateChanged.emit(self.radio_name, "error")
            self.service.messageGenerated.emit(f"射频 {self.radio_name} 抓包失败: {exc}", "error")
        finally:
            self._cleanup_session(takeover)

    def _probe_dialect(self) -> str:
        """探测远端 hcxdumptool 方言版本：'modern' (>= 6.3.x) 或 'legacy' (6.2.x)。"""
        hcx_bin = getattr(self, "_hcx_bin", "hcxdumptool")
        try:
            res = self.runner.run("probe_hcx_dialect", f"{hcx_bin} -h 2>&1 | head -n 40", timeout=8)
            out = res.stdout or ""
            if "-w <file>" in out or "--attemptapmax" in out:
                return "modern"
            if "-o <file>" in out or "--enable_status" in out:
                return "legacy"
        except Exception:
            pass
        return "modern"

    def _detect_phy_channels(self, target_band: str) -> str:
        """从远端 iw phy <phy> info 探测驱动实际支持且未 disabled 的物理信道。"""
        try:
            res = self.runner.run("detect_phy_channels", f"iw phy {self.phy} info 2>/dev/null", timeout=6)
            stdout = res.stdout or ""
            current_band = ""
            channels = []
            for line in stdout.splitlines():
                if "Band 1:" in line:
                    current_band = "2g"
                elif "Band 2:" in line:
                    current_band = "5g"
                elif "Band " in line:
                    current_band = "other"

                if "MHz [" in line and "disabled" not in line.lower():
                    m = re.search(r"\[(\d+)\]", line)
                    if m:
                        ch = int(m.group(1))
                        if target_band == "5g" and (current_band == "5g" or ch > 14):
                            channels.append(f"{ch}b")
                        elif target_band == "2g" and (current_band == "2g" or ch <= 11):
                            channels.append(f"{ch}a")

            if channels:
                return ",".join(channels[:14])
        except Exception:
            pass
        return ""

    def _build_attempt_commands(self, user_channels: str, bpf_file: str) -> List[tuple[str, str]]:
        """与 hcx_autocap.sh next_attempt 严格对齐，构建多档自愈阶梯命令列表 (全程启用受控 Deauth 踢线重连)。"""
        dialect = self._probe_dialect()
        hcx_bin = getattr(self, "_hcx_bin", "hcxdumptool")

        # 检查是否支持 --bpf 标志
        bpf_opt = ""
        try:
            h_res = self.runner.run("probe_bpf_flag", f"{hcx_bin} -h 2>&1 | grep -q -- '--bpf' && echo YES", timeout=5)
            if "YES" in (h_res.stdout or ""):
                bpf_opt = f"--bpf={bpf_file}"
        except Exception:
            pass

        band_str = ""
        if self.radio_info.bands:
            band_str = str(self.radio_info.bands[0])
        else:
            phy_info = self.runner.run(
                "detect_phy_band",
                f"iw phy {self.phy} info 2>/dev/null",
                timeout=6,
            )
            stdout = phy_info.stdout or ""
            if "Band 2:" in stdout:
                band_str = "5G"
            elif "Band 1:" in stdout:
                band_str = "2.4G"
            elif "Band 4:" in stdout or "6000" in stdout:
                band_str = "6G"

        is_5g = "5" in band_str
        is_6g = "6" in band_str

        channels = user_channels.strip()
        if not channels:
            if is_5g:
                # 优先探测当前 5G 网卡驱动实际支持且未禁用的信道
                detected_5g = self._detect_phy_channels("5g")
                channels = detected_5g or "36b,40b,44b,48b"  # 纯基础低频 DFS-free 信道，避免 149~165 报 EINVAL
            elif is_6g:
                channels = "5c,21c,37c,53c,69c,85c,101c,117c"
            else:
                detected_2g = self._detect_phy_channels("2g")
                channels = detected_2g or "1a,2a,3a,4a,5a,6a,7a,8a,9a,10a,11a,12a,13a"

        # 信道规范化：若 modern 方言下传入了纯数字信道，智能补齐对应频段后缀 (a/b/c)，杜绝 EINVAL
        if dialect == "modern" and channels:
            ch_items = [ch.strip() for ch in channels.split(",") if ch.strip()]
            norm_items = []
            suffix = "b" if is_5g else ("c" if is_6g else "a")
            for item in ch_items:
                if re.fullmatch(r"\d+", item):
                    norm_items.append(f"{item}{suffix}")
                else:
                    norm_items.append(item)
            channels = ",".join(norm_items)

        cmd_list = []
        if dialect == "legacy":
            pure_ch = re.sub(r"[a-zA-Z]", "", channels)
            cmd_list.append((
                "legacy-chan (旧版方言指定信道)",
                f"{hcx_bin} -i {self.mon_iface} -o {self.remote_cap_file} -c {pure_ch} --enable_status=15",
            ))
            cmd_list.append((
                "legacy-nochan (旧版方言自适应全频)",
                f"{hcx_bin} -i {self.mon_iface} -o {self.remote_cap_file} --enable_status=15",
            ))
            cmd_list.append((
                "legacy-min (旧版极简模式)",
                f"{hcx_bin} -i {self.mon_iface} -o {self.remote_cap_file}",
            ))
        else:
            # modern 方言 (>= 6.3.x，严禁混入旧版 -o 参数；全程开启受控 Deauth 踢线，坚决禁用假信标)
            if not is_5g and not is_6g:
                # 2.4G 专属阶梯：强化容错与驱动兼容自愈 (严格对齐 hcx_autocap.sh)
                # 档位 1: 双轨主动加速与重连 + 信道表 + BPF
                cmd_list.append((
                    "modern-chan (双轨主动加速+信道表)",
                    f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} -c {channels} {bpf_opt} "
                    f"--attemptapmax=3 --attemptclientmax=3 --disable_beacon --disable_proberequest",
                ))
                # 档位 2: 无 BPF 模式 (解决个别 2.4G 网卡驱动挂载 BPF 时报错 PACKET_STATISTICS failed)
                cmd_list.append((
                    "modern-nobpf (无BPF安全模式)",
                    f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} -c {channels} "
                    f"--attemptapmax=3 --attemptclientmax=3 --disable_beacon --disable_proberequest",
                ))
                # 档位 3: 2.4G 基础通用信道 1~11 (某些老旧或美规固件不支持 12/13 信道)
                if channels != "1a,2a,3a,4a,5a,6a,7a,8a,9a,10a,11a":
                    cmd_list.append((
                        "modern-safe2g (2.4G通用信道1~11)",
                        f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} -c 1a,2a,3a,4a,5a,6a,7a,8a,9a,10a,11a "
                        f"--attemptapmax=3 --attemptclientmax=3 --disable_beacon --disable_proberequest",
                    ))
                # 档位 4: 全频自适应跳频 (-F) 模式 (让驱动与 hcxdumptool 自适应跳频)
                cmd_list.append((
                    "modern-full (双轨主动加速+全频跳频)",
                    f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} -F {bpf_opt} "
                    f"--attemptapmax=3 --attemptclientmax=3 --disable_beacon --disable_proberequest",
                ))
                # 档位 5: 无 BPF 全频自适应跳频 (-F) 模式
                cmd_list.append((
                    "modern-full-nobpf (无BPF全频跳频)",
                    f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} -F "
                    f"--attemptapmax=3 --attemptclientmax=3 --disable_beacon --disable_proberequest",
                ))
                # 档位 6: 极简防扰模式 (去除跳频与主动参数，最基础纯净抓包)
                cmd_list.append((
                    "modern-min (极简防扰模式)",
                    f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} "
                    f"--disable_beacon --disable_proberequest",
                ))
            else:
                # 5G / 6G 阶梯 (保持现有经实测验证成功的 5G 完美表现)
                cmd_list.append((
                    "modern-chan (双轨主动加速+信道表)",
                    f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} -c {channels} {bpf_opt} "
                    f"--attemptapmax=3 --attemptclientmax=3 --disable_beacon --disable_proberequest",
                ))
                cmd_list.append((
                    "modern-full (双轨主动加速+全频跳频)",
                    f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} -F {bpf_opt} "
                    f"--attemptapmax=3 --attemptclientmax=3 --disable_beacon --disable_proberequest",
                ))
                if is_5g and channels != "36b,40b,44b,48b":
                    cmd_list.append((
                        "modern-base5g (5G低频安全信道)",
                        f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} -c 36b,40b,44b,48b {bpf_opt} "
                        f"--attemptapmax=3 --attemptclientmax=3 --disable_beacon --disable_proberequest",
                    ))
                cmd_list.append((
                    "modern-nobpf (无BPF安全模式)",
                    f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} -c {channels} "
                    f"--attemptapmax=3 --attemptclientmax=3 --disable_beacon --disable_proberequest",
                ))
                cmd_list.append((
                    "modern-min (极简防扰模式)",
                    f"{hcx_bin} -i {self.mon_iface} -w {self.remote_cap_file} "
                    f"--disable_beacon --disable_proberequest",
                ))

        return cmd_list

    def _stream_loop(self):
        """循环读取 PTY 输出，流向 UI 并追加落盘。"""
        log_f = None
        if self.local_log_file:
            log_f = open(self.local_log_file, "a", encoding="utf-8", errors="replace")

        try:
            while not self._stop_event.is_set():
                if self._channel is None or self._channel.exit_status_ready():
                    break
                try:
                    data = self._channel.recv(2048)
                    if not data:
                        break
                    text = data.decode("utf-8", errors="replace")
                    if log_f:
                        log_f.write(text)
                        log_f.flush()

                    self.service.radioStreamOutput.emit(self.radio_name, text)
                    self._parse_stream_stats(text)

                except socket.timeout:
                    pass
                except Exception:
                    break

                # 定期刷新时长与统计
                self.stats.duration_seconds = int(time.monotonic() - self._start_time)
                self.service.radioStatsUpdated.emit(self.radio_name, self._build_stats_dict())

        finally:
            if log_f:
                try:
                    log_f.close()
                except Exception:
                    pass

    def _parse_stream_stats(self, text: str):
        """从流式文本解析信道、PMKID 和 EAPOL。"""
        hcx_ap_tbl_pat = re.compile(
            r"^\s*(\d+[a-zA-Z]?)\s+(\d{2}:\d{2}:\d{2})\s+([+ ]{9})\s+([0-9a-fA-F]{12})\s*(.*)$"
        )
        hcx_sta_tbl_pat = re.compile(
            r"^\s*(\d{2}:\d{2}:\d{2})\s+([+ ]{3,5})\s+([0-9a-fA-F]{12})\s+([0-9a-fA-F]{12})\s*(.*)$"
        )

        for raw_line in text.splitlines():
            line = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", raw_line).strip("\r")

            # 1. 匹配 hcxdumptool 6.3.x AP 表格行
            m_ap = hcx_ap_tbl_pat.match(line)
            if m_ap:
                ch, _time, flags, hex_mac, _essid = m_ap.groups()
                b_norm = format_mac(hex_mac)
                if ch:
                    self.stats.current_channel = ch
                # flags: 1(2)=M1, 3(4)=M3, P(6)=PMKID
                has_m1 = len(flags) > 2 and flags[2] == "+"
                has_m3 = len(flags) > 4 and flags[4] == "+"
                has_p = len(flags) > 6 and flags[6] == "+"
                if has_p:
                    self._pmkid_set.add(b_norm)
                if has_m1 or has_m3:
                    self._eapol_set.add(b_norm)
                continue

            # 2. 匹配 hcxdumptool 6.3.x 客户端握手行
            m_sta = hcx_sta_tbl_pat.match(line)
            if m_sta:
                _time, flags, ap_hex, _sta_hex, _essid = m_sta.groups()
                if "+" in flags:
                    self._eapol_set.add(format_mac(ap_hex))
                continue

            # 3. 匹配现代 hcxdumptool 的 SCAN-FREQUENCY: 2412 / 5180
            sf_m = re.search(r"SCAN-FREQUENCY:\s*(\d+)", line, re.IGNORECASE)
            if sf_m:
                freq = int(sf_m.group(1))
                ch = frequency_to_channel(freq)
                self.stats.current_channel = f"{ch} ({freq}MHz)"
            else:
                # 4. 匹配 [CH 36b] 或 [CH 1] 格式
                ch_m = re.search(r"\[CH\s*([0-9a-zA-Z]+)\]", line, re.IGNORECASE)
                if ch_m:
                    self.stats.current_channel = ch_m.group(1)
                else:
                    # 5. 匹配频率表格式: 2412 [  1]
                    ch_tbl = re.search(r"\b(\d{4})\s*\[\s*(\d+)\s*\]", line)
                    if ch_tbl:
                        freq = int(ch_tbl.group(1))
                        ch = ch_tbl.group(2)
                        self.stats.current_channel = f"{ch} ({freq}MHz)"
                    else:
                        ch_alt = re.search(r"\b(?:channel|cha)\s*[:=]?\s*([0-9a-zA-Z]+)\b", line, re.IGNORECASE)
                        if ch_alt and ch_alt.group(1).lower() not in {"hopping", "info", "set", "width", "of", "the"}:
                            self.stats.current_channel = ch_alt.group(1)

            # 6. 传统 PMKID 行解析 (支持 17 位或 12 位 MAC)
            if "PMKID" in line:
                m = re.search(r"([0-9a-fA-F:]{17}|[0-9a-fA-F]{12})", line)
                if m:
                    self._pmkid_set.add(format_mac(m.group(1)))
                else:
                    self._pmkid_set.add(f"unknown_{len(self._pmkid_set)}")

            # 7. 传统握手包行解析
            if "EAPOL" in line or "handshake" in line.lower():
                m = re.search(r"([0-9a-fA-F:]{17}|[0-9a-fA-F]{12})", line)
                if m:
                    self._eapol_set.add(format_mac(m.group(1)))
                else:
                    self._eapol_set.add(f"unknown_{len(self._eapol_set)}")

        self.stats.pmkid_count = len(self._pmkid_set)
        self.stats.eapol_count = len(self._eapol_set)

    def _cleanup_session(self, takeover: OpenWrtRadioTakeover):
        """清理远端通道、精准终止 hcx 进程、删除监控接口、原子恢复被接管无线并拉回成果。"""
        # 1. 关闭 SSH 管道并优雅中断 hcxdumptool
        if self._channel is not None:
            try:
                self._channel.send("\x03")  # 发送 Ctrl+C
                time.sleep(0.5)
                self._channel.close()
            except Exception:
                pass
            self._channel = None

        # 2. 精准停止本射频对应的远端 PID，确保进程彻底退出后再删网卡接口（杜绝 Device or resource busy -16）
        kill_cmd = (
            f"if [ -f /tmp/.hcx_{self.mon_iface}.pid ]; then "
            f"PID=$(cat /tmp/.hcx_{self.mon_iface}.pid 2>/dev/null); "
            f"if [ -n \"$PID\" ] && kill -0 \"$PID\" 2>/dev/null; then "
            f"kill -TERM \"$PID\" 2>/dev/null; sleep 1; "
            f"if kill -0 \"$PID\" 2>/dev/null; then kill -9 \"$PID\" 2>/dev/null; fi; "
            f"fi; "
            f"rm -f /tmp/.hcx_{self.mon_iface}.pid; "
            f"fi"
        )
        try:
            self.runner.run("kill_hcx_pid", kill_cmd, timeout=8)
        except Exception as exc:
            LOGGER.debug("Kill hcx pid failed: %s", exc)

        # 3. 删除监控接口与本会话创建的 Multi-PHY 占位 stub 接口
        try:
            self.runner.run("del_mon_iface", f"iw dev {self.mon_iface} del 2>/dev/null", timeout=8)
        except Exception as exc:
            LOGGER.warning("Delete monitor iface failed: %s", exc)

        if self._created_stubs:
            for stub in list(self._created_stubs):
                try:
                    self.runner.run("del_stub_iface", f"iw dev {stub} del 2>/dev/null", timeout=6)
                except Exception as exc:
                    LOGGER.debug("Delete stub iface %s failed: %s", stub, exc)
            self._created_stubs.clear()

        # 4. 若有无线接管记录，执行原子级回滚恢复！
        if self.takeover_record is not None:
            try:
                self.service.messageGenerated.emit(f"正在原子恢复射频 {self.radio_name} 业务配置...", "info")
                takeover.restore_config_takeover(self.takeover_record)
                takeover.audit_restore(self.takeover_record)
                self.service.messageGenerated.emit(f"射频 {self.radio_name} 业务已成功恢复上线！", "success")
            except Exception as exc:
                LOGGER.error("Takeover restore failed: %s", exc)
                self.service.messageGenerated.emit(f"射频 {self.radio_name} 恢复异常: {exc}", "warning")
            finally:
                self.takeover_record = None

        # 4. 尝试拉回本次抓包产物 .pcapng
        if self.remote_cap_file:
            self._pull_capture_result()

        # 5. 更新状态 (若有未解决的异常，保留 error 状态供用户知晓)
        final_state = "error" if self.stats.error_message else "idle"
        self.stats.state = final_state
        self.service.radioStateChanged.emit(self.radio_name, final_state)
        self.service.radioStatsUpdated.emit(self.radio_name, self._build_stats_dict())

    def _pull_capture_result(self):
        """将生成的 .pcapng 拉回本地。"""
        try:
            pcap_name = Path(self.remote_cap_file).name
            dest_file = self.output_dir / pcap_name
            with self.ssh_client.transfer(None, timeout=30):
                sftp = self.ssh_client.open_sftp(timeout=10)
                try:
                    stat_info = sftp.stat(self.remote_cap_file)
                    self.stats.file_size_bytes = stat_info.st_size
                    if stat_info.st_size > 0:
                        temp_dest = self.output_dir / f"{pcap_name}.part"
                        sftp.get(self.remote_cap_file, str(temp_dest))
                        if temp_dest.exists():
                            if dest_file.exists():
                                dest_file.unlink()
                            temp_dest.rename(dest_file)
                            self.service.messageGenerated.emit(f"抓包产物已拉回: {dest_file.name}", "success")
                except FileNotFoundError:
                    pass
        except Exception as exc:
            LOGGER.warning("Pull capture result failed: %s", exc)

    def _build_stats_dict(self) -> dict:
        return {
            "duration_seconds": self.stats.duration_seconds,
            "pmkid_count": self.stats.pmkid_count,
            "eapol_count": self.stats.eapol_count,
            "file_size_bytes": self.stats.file_size_bytes,
            "current_channel": self.stats.current_channel,
            "state": self.stats.state,
            "error_message": self.stats.error_message,
        }


class AutoCaptureService(QObject):
    """自动抓包后台常驻服务。"""

    resourceStatsUpdated = Signal(int, int, int, int, str)  # mem_avail, mem_total, disk_avail, disk_total, level
    radioStatsUpdated = Signal(str, dict)                   # radio_name, stats_dict
    radioStreamOutput = Signal(str, str)                    # radio_name, text_chunk
    radioStateChanged = Signal(str, str)                    # radio_name, state
    bootStatusChecking = Signal()                           # 开始探测自启与抓包环境
    bootStatusChanged = Signal(object)                      # AutoCaptureBootStatus
    offlineArtifactsPulled = Signal(list, str)              # pulled_files, local_dir
    offlinePullFailed = Signal(str)                         # error_message
    messageGenerated = Signal(str, str)                     # message, level

    def __init__(self, owner):
        super().__init__(owner if isinstance(owner, QObject) else None)
        self.owner = owner
        backend = owner.openwrt_backend
        self.runner = backend.command_runner
        self.ssh = backend.ssh_client

        self.boot_manager = AutoCaptureBootManager(self.runner, self.ssh)
        self.sessions: Dict[str, AutoCaptureRadioSession] = {}

        # 资源守卫轮询定时器
        self._resource_timer = QTimer(self)
        self._resource_timer.setInterval(2500)
        self._resource_timer.timeout.connect(self._poll_resources)

        self._boot_status: Optional[AutoCaptureBootStatus] = None
        self._is_probing_boot = False
        self._disposed = False

        owner.openwrtSnapshotChanged.connect(self._on_snapshot_changed)

    @property
    def boot_status(self) -> Optional[AutoCaptureBootStatus]:
        """获取最近一次探测到的远端自启与服务状态快照。"""
        return self._boot_status

    @property
    def is_probing_boot(self) -> bool:
        """是否正在探测远端自启与服务状态。"""
        return self._is_probing_boot

    @property
    def is_busy(self) -> bool:
        """检查是否有正在抓包的会话。"""
        return any(s.stats.state in {"starting", "capturing", "stopping"} for s in self.sessions.values())

    def get_output_dir(self) -> Path:
        """获取本地抓包成果存储目录。"""
        settings = getattr(self.owner, "settings", {}) or {}
        custom = settings.get("output_dir", "")
        if custom and Path(custom).is_dir():
            out = Path(custom)
        else:
            repo_root = Path(__file__).resolve().parents[3]
            out = repo_root / "dist" / "captures"
        out.mkdir(parents=True, exist_ok=True)
        return out

    def get_or_create_session(self, radio_info: OpenWrtRadioInfo) -> AutoCaptureRadioSession:
        """获取或创建单路射频会话。"""
        if radio_info.name not in self.sessions:
            session = AutoCaptureRadioSession(
                radio_info=radio_info,
                runner=self.runner,
                ssh_client=self.ssh,
                output_dir=self.get_output_dir(),
                service=self,
            )
            self.sessions[radio_info.name] = session
        return self.sessions[radio_info.name]

    def start_radio_capture(self, radio_info: OpenWrtRadioInfo, channels: str = "", allow_takeover: bool = False):
        """启动单路射频抓包。"""
        session = self.get_or_create_session(radio_info)
        session.start(channels, allow_takeover)
        if not self._resource_timer.isActive():
            self._resource_timer.start()

    def stop_radio_capture(self, radio_name: str):
        """停止单路射频抓包。"""
        if radio_name in self.sessions:
            self.sessions[radio_name].stop()
        if not self.is_busy:
            self._resource_timer.stop()

    def start_all(self, radio_configs: List[dict]):
        """批量启动各路射频抓包。"""
        for item in radio_configs:
            radio_info = item.get("radio_info")
            if radio_info:
                channels = item.get("channels", "")
                allow_takeover = item.get("allow_takeover", False)
                self.start_radio_capture(radio_info, channels, allow_takeover)

    def stop_all(self):
        """停止所有正在抓包的射频。"""
        for session in list(self.sessions.values()):
            session.stop()
        self._resource_timer.stop()

    def refresh_boot_status(self):
        """在后台异步刷新开机自启状态。"""
        self._is_probing_boot = True
        self.bootStatusChecking.emit()

        def _task():
            try:
                status = self.boot_manager.probe_boot_status()
                self._boot_status = status
                self._is_probing_boot = False
                self.bootStatusChanged.emit(status)
            except Exception as exc:
                self._is_probing_boot = False
                LOGGER.warning("Probe boot status failed: %s", exc)

        t = threading.Thread(target=_task, name="ProbeBootStatus", daemon=True)
        t.start()

    def start_boot_script(self):
        """异步手动启动后台抓包脚本。"""
        def _task():
            try:
                self.messageGenerated.emit("正在启动后台抓包脚本（仅启用空闲射频）...", "info")
                self.boot_manager.start_script()
                self.messageGenerated.emit("后台抓包脚本已成功启动！仅接管空闲射频，不影响正常业务 Wi-Fi。", "success")
                self.refresh_boot_status()
            except Exception as exc:
                self.messageGenerated.emit(f"启动后台抓包脚本失败: {exc}", "error")
                self.refresh_boot_status()

        t = threading.Thread(target=_task, name="StartBootScript", daemon=True)
        t.start()

    def install_and_start_boot_script(self):
        """异步安装部署自启脚本与服务，并在部署完成后立即启动抓包脚本。"""
        def _task():
            try:
                self.messageGenerated.emit("正在部署自启脚本与系统服务...", "info")
                self.boot_manager.install_or_update()
                self.messageGenerated.emit("脚本部署完成，正在启动后台抓包脚本（仅启用空闲射频）...", "info")
                self.boot_manager.start_script()
                self.messageGenerated.emit("后台抓包脚本已成功启动！仅接管空闲射频，不影响正常业务 Wi-Fi。", "success")
                self.refresh_boot_status()
            except Exception as exc:
                self.messageGenerated.emit(f"部署或启动脚本失败: {exc}", "error")
                self.refresh_boot_status()

        t = threading.Thread(target=_task, name="InstallAndStartBootScript", daemon=True)
        t.start()

    def install_or_update_boot_service(self, enable_after: bool = False):
        """异步安装或更新自启脚本。可选在部署成功后直接开启自启。"""
        def _task():
            try:
                self.messageGenerated.emit("正在部署自启脚本与系统服务...", "info")
                self.boot_manager.install_or_update()
                if enable_after:
                    self.messageGenerated.emit("脚本与服务部署完成，正在开启开机自启...", "info")
                    self.boot_manager.set_autostart_enabled(True)
                    self.messageGenerated.emit("自启脚本部署完成并已开启开机自启！", "success")
                else:
                    self.messageGenerated.emit("自启脚本部署完成！", "success")
                self.refresh_boot_status()
            except Exception as exc:
                self.messageGenerated.emit(f"部署自启脚本失败: {exc}", "error")
                self.refresh_boot_status()

        t = threading.Thread(target=_task, name="InstallBootService", daemon=True)
        t.start()

    def toggle_autostart(self, enabled: bool):
        """切换开机自启开关。"""
        def _task():
            try:
                action_str = "开启" if enabled else "关闭"
                self.messageGenerated.emit(f"正在{action_str}开机自启...", "info")
                self.boot_manager.set_autostart_enabled(enabled)
                self.messageGenerated.emit(f"已{action_str}开机自启", "success")
                self.refresh_boot_status()
            except Exception as exc:
                self.messageGenerated.emit(f"切换开机自启失败: {exc}", "error")
                self.refresh_boot_status()

        t = threading.Thread(target=_task, name="ToggleAutostart", daemon=True)
        t.start()

    def uninstall_boot_service(self):
        """异步卸载自启服务。"""
        def _task():
            try:
                self.messageGenerated.emit("正在卸载开机自启服务...", "info")
                self.boot_manager.uninstall()
                self.messageGenerated.emit("已完全卸载自启服务与脚本", "success")
                self.refresh_boot_status()
            except Exception as exc:
                self.messageGenerated.emit(f"卸载失败: {exc}", "error")
                self.refresh_boot_status()

        t = threading.Thread(target=_task, name="UninstallBootService", daemon=True)
        t.start()

    def stop_and_cleanup_capture(self):
        """异步结束并收尾后台抓包进程（安全封包、清理残留进程与监听接口）。"""
        def _task():
            try:
                self.messageGenerated.emit("正在结束并收尾后台抓包操作...", "info")
                self.boot_manager.stop_and_cleanup_capture()
                self.messageGenerated.emit("后台抓包已成功结束！抓包数据已安全保存至 /root/cap/ 目录。", "success")
                self.refresh_boot_status()
            except Exception as exc:
                self.messageGenerated.emit(f"收尾抓包失败: {exc}", "error")
                self.refresh_boot_status()

        t = threading.Thread(target=_task, name="StopAndCleanupCapture", daemon=True)
        t.start()

    def pull_offline_artifacts(self, dest_dir: Optional[str] = None) -> list[str]:
        """从远端 /root/cap/ 拉取所有离线成果与日志到本地，返回成功拉取的文件绝对路径列表。"""
        target_dir = dest_dir or str(self.get_output_dir())
        return self.boot_manager.pull_offline_artifacts(target_dir)

    def pull_offline_artifacts_async(self, dest_dir: Optional[str] = None):
        """异步从远端 /root/cap/ 拉取所有离线成果与日志到本地，并通过 Qt 信号在主线程通知完成。"""
        target_dir = dest_dir or str(self.get_output_dir())

        def _task():
            try:
                self.messageGenerated.emit("正在从路由器拉取离线抓包文件与日志...", "info")
                pulled = self.boot_manager.pull_offline_artifacts(target_dir)
                self.offlineArtifactsPulled.emit(pulled, target_dir)
            except Exception as exc:
                LOGGER.warning("Pull offline artifacts failed: %s", exc)
                self.offlinePullFailed.emit(str(exc))

        t = threading.Thread(target=_task, name="PullOfflineArtifacts", daemon=True)
        t.start()

    def analyze_offline_artifacts(self, dest_dir: Optional[str] = None) -> AutoCaptureDiagnosticReport:
        """调用分析引擎对本地成果目录中的离线抓包与日志进行智能健康诊断。"""
        target_dir = dest_dir or str(self.get_output_dir())
        return AutoCaptureLogAnalyzer.analyze_directory(target_dir)

    def _poll_resources(self):
        """轻量探针读取内存与 Flash 剩余空间。"""
        if not self.is_busy:
            return

        def _query():
            try:
                cmd = (
                    "awk '/MemAvailable/ {avail=$2} /MemTotal/ {total=$2} END {printf \"%d:%d:\", avail/1024, total/1024}' /proc/meminfo; "
                    "df -k /root | tail -n 1 | awk '{printf \"%d:%d\\n\", $(NF-2)/1024, $(NF-4)/1024}'"
                )
                res = self.runner.run("guard_probe", cmd, timeout=5)
                if res.exit_code == 0 and res.stdout:
                    parts = res.stdout.strip().split(":")
                    if len(parts) == 4:
                        mem_avail = int(parts[0])
                        mem_total = int(parts[1])
                        disk_avail = int(parts[2])
                        disk_total = int(parts[3])

                        level = "normal"
                        if mem_avail < 6 or disk_avail < 2:
                            level = "danger"
                        elif mem_avail < 12 or disk_avail < 5:
                            level = "warning"

                        self.resourceStatsUpdated.emit(mem_avail, mem_total, disk_avail, disk_total, level)

                        # 触发危险红线紧急停机保护！
                        if level == "danger":
                            self.messageGenerated.emit(
                                f"【资源危险预警】远端内存({mem_avail}MB)或磁盘({disk_avail}MB)已逼近极限！触发紧急优雅停机保护...",
                                "error",
                            )
                            self.stop_all()
            except Exception as exc:
                LOGGER.debug("Guard probe failed: %s", exc)

        t = threading.Thread(target=_query, name="GuardProbe", daemon=True)
        t.start()

    def _on_snapshot_changed(self, snapshot):
        """连接状态变化时同步自启状态。"""
        if snapshot is None or not hasattr(snapshot, "connection_state"):
            self.stop_all()
            self._boot_status = None
            return

        state_val = getattr(snapshot.connection_state, "value", "")
        if state_val == "connected":
            self.refresh_boot_status()
        elif state_val in {"disconnected", "failed"}:
            self.stop_all()
            self._boot_status = None
