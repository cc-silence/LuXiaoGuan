"""常驻自动握手协调器：整轮借一个任务槽和 worker，不反向依赖页面。"""

from dataclasses import dataclass, replace
from uuid import uuid4

from PySide6.QtCore import QObject, Slot

from modules.capture_services.auto_handshake import (
    AutoHandshakeRequest, AutoScanPlan, AutoScanProgress, prepare_auto_scan,
)
from modules.capture_services.backends.openwrt_backend import HandshakeBatchResult, HandshakeTargetResult
from modules.capture_services.models import CaptureSessionState
from modules.capture_services.openwrt.models import AccessPointCaptureState, CaptureProgress, CaptureTaskType
from modules.capture_services.parallel_handshake import ParallelProgress, capture_parallel
from modules.capture_services.verified_results import VerifiedResultCheck, check_verified_results
from modules.config_services.capture_settings_manager import CaptureSettingsManager


_SKIP_REASONS = {
    "already_completed": "已有可用包", "not_found": "本次扫描未发现",
    "no_clients": "本次未发现客户端", "unsupported_encryption": "不属于 WPA/WPA2 PSK",
    "different_band": "不在本次频段", "invalid_channel": "信道无效",
    "conflicting_scan_records": "扫描记录冲突",
}
_STAGES = {
    "scanning": "自动扫描", "filtering": "筛选授权目标", "capturing": "自动被动采集",
    "analyzing": "检查握手内容", "saving": "转换并保存结果", "cleaning": "清理临时资源",
}


@dataclass(frozen=True)
class AutoHandshakeResult:
    """整轮终局，扫描计划和批次结果并存，失败/取消仍携带已落地产物。"""

    plan: AutoScanPlan
    batch: HandshakeBatchResult | None = None


class AutoHandshakeService(QObject):
    """主线程冻结/启动/回放，后台扫描后执行单轮串行或多空闲射频捕获。"""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._prepared = None
        self._request = None
        self._event = None
        self._session_id = ""
        self._plan = None
        self._entries = ()
        self._audit_input = ({}, (), 0, "")

    def _require_ready(self, settings=None):
        """只读主线程准入；不探测设备，不等待外部程序。"""
        owner = self.owner
        snapshot = owner.openwrt_snapshot
        if owner._shutting_down or owner.task_controller.busy:
            raise ValueError("已有任务运行或正在退出，请等待结束后再开始自动抓包。")
        if snapshot.connection_state.value != "connected":
            raise ValueError("请先在路由器登录页连接设备，再开始自动抓包。")
        ready = {item.key for item in snapshot.dependencies if item.state.value == "ready"}
        if not {"scan", "handshake", "sftp"} <= ready:
            raise ValueError("自动抓包需要扫描、常规抓包和 SFTP 依赖，请到路由器登录页检查并安装。")
        if settings is not None:
            enabled = settings.get("enabled_features", {})
            if not all(enabled.get(key, True) for key in ("scan", "handshake")):
                raise ValueError("请先在设置中启用扫描和常规抓包功能。")

    def prepare(self, settings):
        """确认框出现前冻结当前选择，返回只能用于本次确认的请求，不启动任务。"""
        self._prepared = None
        self._require_ready(settings)
        snapshot = self.owner.openwrt_snapshot
        options = dict(settings)
        options["resolved_output_dir"] = str(CaptureSettingsManager.normalized_output_dir(settings.get("output_dir")))
        request = AutoHandshakeRequest.from_selection(
            snapshot.access_points, options, self.owner.verified_history_snapshot(),
        )
        self._prepared = (request, snapshot.host_fingerprint, tuple(p for p in snapshot.access_points if p.selected))
        return request

    def start(self, request):
        """主线程确认后启动；请求必须来自本服务且连接/选择仍与确认内容一致。"""
        prepared, self._prepared = self._prepared, None
        self._require_ready()
        snapshot = self.owner.openwrt_snapshot
        if (prepared is None or prepared[0] is not request
                or prepared[1] != snapshot.host_fingerprint
                or prepared[2] != tuple(p for p in snapshot.access_points if p.selected)):
            raise ValueError("连接或所选范围已变化，请重新确认授权范围后再开始。")
        owner = self.owner
        self._session_id = uuid4().hex
        self._request = request
        self._plan = None
        self._entries = ()
        event = owner.task_controller.begin(CaptureTaskType.AUTO_HANDSHAKE.value, self._session_id)
        if event is None:
            return False
        self._event = event
        self._audit_input = owner.verified_results.freeze()
        owner._stash_result_prefs(request.backend_settings())
        owner._stash_privacy_mask(request.backend_settings())
        owner.openwrt_backend.snapshot = replace(
            owner.openwrt_snapshot,
            auto_scope=tuple(p.bssid for p in request.authorized),
            auto_stage="stopping" if event.is_set() else "scanning", auto_outcome="running",
            auto_summary="", auto_skipped=(), cleanup_errors=(), last_error="",
            progress=CaptureProgress("准备自动抓包", 0, 0, "仅处理本次明确确认的授权范围。", True),
        )
        owner.openwrtSnapshotChanged.emit(owner.openwrt_snapshot)
        owner.messageGenerated.emit(
            f"开始单轮自动抓包：授权 {len(request.authorized)} 个，{request.band}，"
            f"扫描 {request.scan_seconds} 秒，每目标最多 {request.capture_seconds} 秒；被动、仅空闲射频。", "info",
        )
        try:
            owner._start_openwrt_worker(
                self._session_id,
                lambda cancel, progress, message: self._run(request, cancel, progress, message),
                event, self._finished, self._failed, self._progress, self._message,
            )
        except Exception as exc:
            self._failed(str(exc), repr(exc))
            return False
        return True

    def _run(self, request, event, progress, message):
        """只在 worker 中运行；两阶段目录分别创建，取消不衔接后续阶段。"""
        if event.is_set():
            return AutoHandshakeResult(AutoScanPlan("cancelled", detail="自动抓包已取消。"))
        report = check_verified_results(*self._audit_input, event)
        progress(report)
        request = replace(request, completed=request.completed - report.missing_bssids)
        if event.is_set():
            return AutoHandshakeResult(AutoScanPlan("cancelled", detail="自动抓包已取消。"))
        plan = prepare_auto_scan(self.owner.openwrt_backend, request, event, progress, message)
        progress(plan)
        if plan.status != "ready" or event.is_set():
            return AutoHandshakeResult(plan)
        if request.parallel_radios:
            batch = capture_parallel(self.owner.openwrt_backend, request.backend_settings(),
                                     plan.targets, event, progress, message)
        else:
            batch = self.owner.openwrt_backend.capture_handshakes_passive(
                uuid4().hex, request.backend_settings(), plan.targets, event, progress, message,
                target_callback=progress, stop_on_idle_unavailable=True,
            )
        return AutoHandshakeResult(plan, batch)

    def _active(self):
        return (self.owner.openwrt_snapshot.session_id == self._session_id
                and self.owner.openwrt_snapshot.task_type == CaptureTaskType.AUTO_HANDSHAKE)

    @Slot(str, str)
    def _message(self, text, level):
        """入窗前应用当前隐私开关；停止期间不显示排队的成功消息。"""
        if not self._active():
            return
        if self._event.is_set() and level == "success":
            level = "info"
        self.owner.messageGenerated.emit(self._safe_text(text), level)

    def _safe_text(self, text):
        """\u4fdd\u7559\u539f\u59cb\u64ad\u62a5\uff0c\u9875\u9762\u7edf\u4e00\u6295\u5f71\u3002"""
        return str(text)

    @Slot(object)
    def _progress(self, update):
        """主线程合并结构化进度/计划/产物，停止仍接收产物但不倒退阶段。"""
        if not self._active():
            return
        owner = self.owner
        if isinstance(update, VerifiedResultCheck):
            missing = owner.verified_results.apply(update)
            self._request = replace(self._request, completed=self._request.completed - missing)
            return
        if isinstance(update, HandshakeTargetResult):
            self._entries += (update,)
            owner._merge_openwrt_handshake_results(HandshakeBatchResult(self._event.is_set(), (update,)))
            return
        if isinstance(update, AutoScanPlan):
            self._plan = update
            fresh = {
                p.bssid: owner.verified_results.decorate(
                    replace(p, capture_state=AccessPointCaptureState.CAPTURED)
                    if p.bssid in self._request.completed else p)
                for p in update.access_points
            }
            scope = {p.bssid for p in self._request.authorized}
            owner.openwrt_backend.snapshot = replace(
                owner.openwrt_snapshot,
                access_points=tuple(
                    fresh.get(p.bssid.upper(), replace(p, clients=())) if p.bssid.upper() in scope else p
                    for p in owner.openwrt_snapshot.access_points
                ) if update.status in {"ready", "no_targets"} else owner.openwrt_snapshot.access_points,
                auto_skipped=tuple((s.bssid, s.reason) for s in update.skipped),
            )
            for skipped in update.skipped:
                point = next(p for p in self._request.authorized if p.bssid == skipped.bssid)
                self._message(f"{owner._masked_target(point.ssid, point.bssid)}：跳过，{_SKIP_REASONS[skipped.reason]}。", "info")
            owner.openwrtSnapshotChanged.emit(owner.openwrt_snapshot)
            return
        if self._event.is_set():
            return
        if isinstance(update, ParallelProgress):
            owner.task_controller.set_state(CaptureSessionState.CAPTURING)
            if self._event.is_set():
                return
            stages = {"等待": "等待", "准备": "准备", "结束": "结束", **_STAGES}
            detail = "；".join(f"{phy}：{stages.get(stage, stage)}" for phy, stage in update.lanes)
            owner.openwrt_backend.snapshot = replace(
                owner.openwrt_snapshot, auto_stage="capturing",
                progress=CaptureProgress(
                    f"并行 {update.active}/{update.workers} 路 · 已处理 {update.completed}/{update.total}",
                    update.completed, update.total, detail, False),
            )
            owner.openwrtSnapshotChanged.emit(owner.openwrt_snapshot)
            return
        if owner.openwrt_snapshot.task_state == "starting":
            owner.task_controller.set_state(CaptureSessionState.CAPTURING)
        if self._event.is_set():
            return
        stage = update.stage
        title = _STAGES.get(stage, "自动抓包")
        if not isinstance(update, AutoScanProgress) and update.target_total:
            title += f" {update.target_index + 1}/{update.target_total}"
        current_bssid = str(getattr(update, "target_bssid", "") or "").strip().upper()
        if current_bssid:
            capturing = (current_bssid,)
        else:
            capturing = owner.openwrt_snapshot.capturing_bssids
        owner.openwrt_backend.snapshot = replace(
            owner.openwrt_snapshot, auto_stage=stage,
            progress=CaptureProgress(title, update.elapsed, update.total,
                                     f"本次授权 {len(self._request.authorized)} 个；有限单轮。", not bool(update.total)),
            capturing_bssids=capturing,
        )
        owner.openwrtSnapshotChanged.emit(owner.openwrt_snapshot)

    @Slot(str, str)
    def _failed(self, summary, detail):
        """意外异常仍合并此前结构化回调收到的产物，不走会清空连接快照的通用失败槽。"""
        plan = self._plan or AutoScanPlan("failed", detail=str(summary))
        batch = HandshakeBatchResult(bool(self._event and self._event.is_set()), self._entries,
                                     str(summary), "failed")
        self._finished(AutoHandshakeResult(plan, batch))

    @Slot(object)
    def _finished(self, result):
        """最后清理返回后统一汇总；取消与清理错误并存，不发成功框或成功后置动作。"""
        if not self._active():
            return
        owner = self.owner
        plan, batch = result.plan, result.batch
        cancelled = self._event.is_set() or plan.status == "cancelled" or bool(batch and batch.cancelled)
        lines, good = [], 0
        if batch is not None:
            lines, good = owner._merge_openwrt_handshake_results(replace(batch, cancelled=cancelled))
        # 合并通知可能触发主线程取消，终局发送前再次读取事件。
        cancelled = cancelled or self._event.is_set()
        errors = tuple(plan.cleanup_errors) + (batch.cleanup_errors if batch else ())
        outcome = ("cancelled" if cancelled else batch.status if batch else plan.status)
        if errors and not cancelled:
            outcome = "failed"
        attempted = len(batch.results) if batch else 0
        failed_targets = sum(entry.result is None or bool(entry.error) for entry in batch.results) if batch else 0
        if outcome == "completed" and failed_targets:
            outcome = "partial_failure"
        terminal = {"cancelled": "已取消", "no_targets": "无符合目标，已结束", "completed": "单轮结束",
                    "idle_radio_unavailable": "无空闲射频，已结束整轮"}.get(outcome, "未正常完成")
        summary = (f"自动抓包{terminal}：授权 {len(self._request.authorized)} 个，"
                   f"符合 {len(plan.targets)} 个，跳过 {len(plan.skipped)} 个，"
                   f"已处理 {attempted} 个，可用结果 {good} 个，"
                   f"尚未处理 {max(0, len(plan.targets) - attempted)} 个。")
        if plan.status not in {"ready", "no_targets"} and not attempted:
            summary = (f"自动抓包未开始：本次授权 {len(self._request.authorized)} 个，"
                       f"尚未处理 {len(self._request.authorized)} 个。")
        detail = batch.note if batch else plan.detail
        if detail:
            summary += "\n" + detail
        if errors:
            summary += "\n清理错误：" + "；".join(errors)
        summary = self._safe_text(summary)
        # 汇总不含目标名称，详细列表通过有隐私处理的信息通道输出。
        failed = outcome in {"failed", "disconnected", "idle_radio_unavailable", "partial_failure"} or bool(errors)
        snapshot = owner._task_failure_snapshot(summary) if failed else owner.openwrt_snapshot
        owner.openwrt_backend.snapshot = replace(
            snapshot, auto_stage="finished", auto_outcome=outcome, auto_summary=summary,
            auto_skipped=tuple((s.bssid, s.reason) for s in plan.skipped), cleanup_errors=errors,
            capturing_bssids=(),
        )
        # 结果已写入快照；finish 的状态信号一次回放终态，避免回放途中又被取消。
        session_id = self._session_id
        owner.task_controller.finish(failed=failed)
        level = ("warning" if outcome == "idle_radio_unavailable" and not errors else
                 "error" if failed else "info" if cancelled else "success" if good else "warning")
        owner.messageGenerated.emit(self._safe_text(summary + ("\n" + "\n".join(lines) if lines else "")), level)
        if not cancelled and owner.openwrt_snapshot.session_id == session_id:
            if not failed:
                produced = bool(batch and any(e.result and any((e.result.cap_path, e.result.hc22000_path,
                                                               e.result.hccapx_path)) for e in batch.results))
                owner._maybe_auto_open_dir(produced)
            owner._notify_task_result("自动抓包结果", summary)
