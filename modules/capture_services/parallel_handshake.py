"""自动被动握手的多射频调度；资源按 phy 隔离，整轮仍由一个任务槽持有。"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Lock
from uuid import uuid4
import re
import time
import json

from .backends.openwrt_backend import (
    HandshakeBatchResult, HandshakeTargetResult, OpenWrtCaptureBackend,
)
from .openwrt.command_builder import normalize_bssid, quote_argument
from .openwrt.command_runner import OpenWrtCommandRunner
from .openwrt.device_probe import _parse_supported_modes
from .openwrt.handshake_capture import OpenWrtHandshakeCapture
from .openwrt.models import CaptureCleanupError
from .openwrt.radio_policy import IdleRadioUnavailable, require_idle_phy
from .openwrt.radio_session import OpenWrtRadioSession
from .openwrt.phy_channels import iter_phy_channels
from .openwrt.remote_process import RemoteProcessManager
from .openwrt.workspace import CaptureWorkspaceManager


@dataclass(frozen=True)
class RadioCapability:
    """实际读取的可用频段/信道集合，不使用业务信道推测硬件能力。"""

    phy: str
    channels: frozenset


@dataclass(frozen=True)
class ParallelProgress:
    """整轮并行状态；每路阶段独立展示，完成数只来自结构化目标结果。"""

    workers: int
    active: int
    completed: int
    total: int
    lanes: tuple


def discover_idle_radios(runner, event):
    """只读核实所有硬件及空闲能力；返回可用资源与逐路排除原因。"""
    if event.is_set():
        return (), ()
    inventory = runner.run("parallel_list_phys", "ls /sys/class/ieee80211/", timeout=8, cancel_event=event)
    names = inventory.stdout.split()
    if (inventory.exit_code != 0 or inventory.timed_out or inventory.cancelled
            or not names or any(not re.fullmatch(r"phy\d+", name) for name in names)):
        raise IdleRadioUnavailable("无法核实无线硬件清单，未启动并行采集；请检查连接与设备无线状态。")
    radios, notes = [], []
    for phy in sorted(set(names)):
        if event.is_set():
            break
        try:
            require_idle_phy(runner, phy, event)
            result = runner.run("parallel_phy_capability", f"iw phy {quote_argument(phy)} info",
                                timeout=10, cancel_event=event)
            if event.is_set():
                break
            if result.exit_code != 0 or result.timed_out or result.cancelled:
                raise IdleRadioUnavailable(f"{phy} 能力读取失败或超时，已排除。")
            if "monitor" not in (_parse_supported_modes(result.stdout) or ()):
                raise IdleRadioUnavailable(f"{phy} 未明确声明 monitor 能力，已排除。")
            channels = set()
            for frequency, channel, flags in iter_phy_channels(result.stdout):
                if "disabled" in flags.lower():
                    continue
                if 2400 <= frequency < 2500 and 1 <= channel <= 14:
                    channels.add(("2.4G", channel))
                elif 5000 <= frequency < 5925 and 15 <= channel <= 196:
                    channels.add(("5G", channel))
            if not channels:
                raise IdleRadioUnavailable(f"{phy} 没有明确可用的 2.4G/5G 信道，已排除。")
            radios.append(RadioCapability(phy, frozenset(channels)))
        except IdleRadioUnavailable as exc:
            notes.append(str(exc))
        except Exception as exc:
            if event.is_set():
                break
            notes.append(f"{phy} 无法完成能力核验（{type(exc).__name__}），已排除；请检查连接。")
    return tuple(radios), tuple(notes)


def assign_targets(radios, targets):
    """按能力稀缺程度及当前负载分配，每个 BSSID 和 phy 均只有一个所有者。"""
    unique = {}
    for target in targets:
        key = normalize_bssid(target.bssid)
        target = replace(target, bssid=key)
        if key in unique and unique[key] != target:
            raise ValueError("授权目标记录冲突，请重新扫描确认。")
        unique[key] = target
    if len({r.phy for r in radios}) != len(radios):
        raise ValueError("射频清单重复，拒绝创建重复工作单元。")
    assignments = {r.phy: [] for r in radios}
    candidates = {key: [r.phy for r in radios if (p.band, p.channel) in r.channels]
                  for key, p in unique.items()}
    rejected = []
    for key in sorted(unique, key=lambda value: len(candidates[value])):
        if not candidates[key]:
            rejected.append(HandshakeTargetResult(unique[key], error="没有支持目标频段和信道的已核实空闲射频。",
                                                   error_code="idle_radio_unavailable"))
            continue
        phy = min(candidates[key], key=lambda name: (len(assignments[name]), name))
        assignments[phy].append(unique[key])
    return {phy: tuple(points) for phy, points in assignments.items() if points}, tuple(rejected)


class _RadioBackend(OpenWrtCaptureBackend):
    """只装配握手批次所需协作者；共享连接，不共享任何可变无线/进程记录。"""

    def __init__(self, ssh_client, capability):
        self.ssh_client = ssh_client
        self.capability = capability
        self.command_runner = OpenWrtCommandRunner(ssh_client)
        self.process_manager = RemoteProcessManager(self.command_runner)
        self.radio_session = OpenWrtRadioSession(self.command_runner)
        self.workspace_manager = CaptureWorkspaceManager(ssh_client, self.command_runner)
        self.handshake_capture = OpenWrtHandshakeCapture(self.command_runner, self.process_manager)
        from modules.capture_services.openwrt.reconnect_cleanup import ReconnectCleanup
        self.reconnect_cleanup = ReconnectCleanup(self)
        fingerprint = getattr(ssh_client, "host_fingerprint", "")
        if isinstance(fingerprint, str):
            self.reconnect_cleanup.recover(fingerprint)
        self.signal_refresh_callback = None
        self._running = False

    def _find_scan_radio(self, band, cancel_event, target_channel=0, idle_radio_only=False, message_callback=None):
        """复核已分配的实际 phy，禁止回退或重新自动选卡。"""
        if not idle_radio_only or (band, target_channel) not in self.capability.channels:
            raise IdleRadioUnavailable("并行单元的频段、信道或严格空闲策略不匹配。")
        require_idle_phy(self.command_runner, self.capability.phy, cancel_event)
        return self.capability.phy

    def _finish_capture_task(self, workspace, primary_error=None, takeover_record=None,
                               message_callback=None):
        """停止失败保留精确进程身份和现场，禁止删掉仍被进程使用的目录。"""
        handles = self.process_manager.handles()
        if handles:
            errors = ["本路进程停止未确认，远端目录已保留，请检查设备连接后按身份恢复。"]
            try:
                (workspace.local_dir / "owned-processes.json").write_text(
                    json.dumps([asdict(handle) for handle in handles], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                errors.append(f"进程身份记录保存失败：{exc}")
            self._running = False
            raise CaptureCleanupError("；".join(errors), errors)
        return super()._finish_capture_task(workspace, primary_error, takeover_record, message_callback)


class _DeadlineEvent:
    """总取消和单目标期限共享事件接口；超时仅停止本路，不取消其他已授权目标。"""

    def __init__(self, parent, seconds):
        self.parent = parent
        self.deadline = time.monotonic() + seconds

    def is_set(self):
        return self.parent.is_set() or time.monotonic() >= self.deadline


def capture_parallel(backend, settings, targets, event, progress, message):
    """后台调度有限个资源单元，全部清理/线程退出后归并结果，不释放外层任务槽。"""
    targets = tuple(targets)
    if not targets:
        raise ValueError("并行采集范围不能为空。")
    if event.is_set():
        return HandshakeBatchResult(True, (), status="cancelled")
    try:
        radios, notes = discover_idle_radios(backend.command_runner, event)
    except IdleRadioUnavailable as exc:
        return HandshakeBatchResult(event.is_set(), (), str(exc),
                                    "cancelled" if event.is_set() else "idle_radio_unavailable")
    assignments, rejected = assign_targets(radios, targets)
    if not assignments:
        return HandshakeBatchResult(event.is_set(), rejected, "；".join(notes) or "没有符合目标的空闲射频。",
                                    "cancelled" if event.is_set() else "idle_radio_unavailable")
    options = dict(settings, active_deauth=False, idle_radio_only=True, allow_radio_takeover=False,
                   handshake_rounds=1, handshake_rounds_infinite=False)
    if not options.get("resolved_output_dir"):
        raise ValueError("并行采集结果目录无效，请重新选择保存位置。")
    run_id = uuid4().hex
    lock = Lock()
    entries = {entry.target.bssid: entry for entry in rejected}
    states = {phy: "等待" for phy in assignments}
    running = set()
    batches = []

    def publish():
        progress(ParallelProgress(len(assignments), len(running), len(entries),
                                  len({normalize_bssid(p.bssid) for p in targets}), tuple(states.items())))

    def record(entry):
        with lock:
            entries[entry.target.bssid] = entry
            progress(entry)
            publish()

    def run_lane(capability):
        phy = capability.phy
        unit = _RadioBackend(backend.ssh_client, capability)
        lane_settings = dict(options, resolved_output_dir=str(Path(options["resolved_output_dir"]) / run_id / phy))
        outcomes = []
        with lock:
            running.add(phy)
            states[phy] = "准备"
            publish()
        try:
            for target in assignments[phy]:
                if event.is_set():
                    break
                # 采集之外预留原有准备、三次有界传输、转换和清理时间。
                deadline = _DeadlineEvent(event, int(options.get("idle_timeout_seconds", 180) or 180) + 600)

                def updated(update):
                    with lock:
                        states[phy] = update.stage
                        publish()

                batch = unit.capture_handshakes_passive(
                    uuid4().hex, lane_settings, (target,), deadline, updated, message,
                    target_callback=record, stop_on_idle_unavailable=True,
                )
                if deadline.is_set() and not event.is_set():
                    batch = replace(batch, cancelled=False, status="failed", note="本路任务超过有界执行期限，已清理停止。")
                outcomes.append(batch)
                if batch.status != "completed" or batch.cleanup_errors or any(e.error for e in batch.results):
                    break
            return outcomes
        except Exception as exc:
            outcomes.append(HandshakeBatchResult(event.is_set(), (), f"{phy} 执行失败：{exc}", "failed"))
            return outcomes
        finally:
            with lock:
                running.discard(phy)
                states[phy] = "结束"
                publish()

    message(f"自动并行采集：使用 {len(assignments)} 块已核实空闲射频；"
            + ("仅一块可用，回落单路。" if len(assignments) == 1 else "各路同时处理不同授权目标。"), "info")
    for note in notes:
        message(note, "warning")
    for entry in rejected:
        progress(entry)
    # 线程数量严格受可用且有匹配目标的 phy 数约束；不启动逐目标无界线程。
    with ThreadPoolExecutor(max_workers=len(assignments), thread_name_prefix="WinBoxRadio") as pool:
        futures = [pool.submit(run_lane, radio) for radio in radios if radio.phy in assignments]
        for future in as_completed(futures):
            try:
                batches.extend(future.result())
            except Exception as exc:
                batches.append(HandshakeBatchResult(event.is_set(), (), f"并行工作单元异常：{exc}", "failed"))
    for batch in batches:
        for entry in batch.results:
            entries[entry.target.bssid] = entry
    errors = tuple(error for batch in batches for error in batch.cleanup_errors)
    status = "cancelled" if event.is_set() else "failed" if errors or any(
        batch.status != "completed" for batch in batches) else "completed"
    order = dict.fromkeys(normalize_bssid(p.bssid) for p in targets)
    return HandshakeBatchResult(event.is_set(), tuple(entries[key] for key in order if key in entries),
                                "；".join(batch.note for batch in batches if batch.note), status, errors,
                                tuple(entry for batch in batches for entry in batch.retained_results), len(order))
