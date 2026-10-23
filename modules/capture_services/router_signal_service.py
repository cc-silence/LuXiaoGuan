"""独立信号管理服务，借用常驻抓包服务的任务槽和 worker 所有权。"""

from dataclasses import replace
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, Signal, Slot

from modules.capture_services.openwrt.models import CaptureTaskType
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
from modules.capture_services.openwrt.signal_models import SignalFailure, SignalSnapshot
from modules.capture_services.openwrt.signal_package_feed import default_cache_dir
from .workflow_diagnostics import record_workflow

# 唯一会改路由器无线配置的其他任务；它结束后必须重新核实，不能自动解锁。
_WIRELESS_WRITERS = frozenset({CaptureTaskType.PASSWORD_VERIFY.value, "wireless_setup"})
# 他任务执行中的状态行文案；没列到的抓包类任务统一说抓包中。
_BUSY_COPY = {
    "wireless_apply_check": "管理连接检查中",
    "wireless_inspect": "\u65e0\u7ebf\u73af\u5883\u68c0\u6d4b\u4e2d",
    "wireless_setup": "无线设置中",
    CaptureTaskType.ROUTER_MANAGEMENT.value: "\u8def\u7531\u5668\u7ba1\u7406\u4e2d",
    CaptureTaskType.WIFI_SCAN.value: "信号扫描中",
    CaptureTaskType.PASSWORD_VERIFY.value: "在线密码验证中",
    CaptureTaskType.CONNECT.value: "设备连接中",
    CaptureTaskType.DEVICE_PROBE.value: "设备探测中",
    CaptureTaskType.DEPENDENCY_CHECK.value: "依赖检查中",
    CaptureTaskType.DEPENDENCY_INSTALL.value: "依赖安装中",
    CaptureTaskType.DEPENDENCY_UNINSTALL.value: "依赖卸载中",
    CaptureTaskType.RECOVERY.value: "远端恢复中",
}


class RouterSignalService(QObject):
    """主线程入口及快照所有者；执行器只在现有 worker 内运行。

    owner 是 CaptureService；复用其连接、互斥、取消和退出生命周期。
    信号只包含白名单模型和安全摘要，不暴露原始配置。
    """

    snapshotChanged = Signal(object)
    messageGenerated = Signal(str, str)
    reconnectRequired = Signal(str)
    signalRefreshed = Signal(object)
    _refreshRequested = Signal()

    def __init__(self, owner):
        """装配协作者并订阅连接状态；不执行远程命令。"""
        super().__init__(owner)
        self.owner = owner
        self.manager = RouterSignalManager(owner.openwrt_backend.command_runner)
        self.snapshot = SignalSnapshot()
        self.messages = []
        self._active = False
        self._auto_refresh_blocked = False
        self._foreign_task_active = False
        self._refreshRequested.connect(self._try_auto_refresh, Qt.QueuedConnection)
        self._verified = False
        self.dependencies_missing = False
        self.details_enabled = False
        owner.openwrtSnapshotChanged.connect(self._connection_changed)
        owner.task_controller.stateChanged.connect(self._controller_state_changed)
        self.signalRefreshed.connect(self._apply_external_inventory)
        from .wireless_setup_service import WirelessSetupService
        self.setup = WirelessSetupService(self)

    def _sync_transient(self):
        """按 live 互斥与核实位推导 busy/stale；返回是否变化。

        stale 只在“忙碌中”或“从未核实/核实失败”时为真；别的任务结束且
        本模块数据已核实就自动解锁，不用手动刷新。改无线配置的任务（V）
        结束时清核实位，照样锁住。
        """
        busy = bool(self.owner.task_controller.busy)
        stale = bool(busy or not self._verified)
        installing = bool(busy and self.snapshot.installing)
        changed = bool(
            self.snapshot.busy != busy
            or self.snapshot.stale != stale
            or self.snapshot.installing != installing
        )
        if changed:
            self.snapshot = replace(
                self.snapshot, busy=busy, stale=stale, installing=installing
            )
        return changed

    @Slot(str, str, str)
    def _controller_state_changed(self, state, task_type, _session_id):
        """任务槽变化时同步；他任务起止给提示、结束自动重读恢复。

        本模块自己的任务不管文案（_start 已写好）；V 类任务结束不清核实位，
        而是靠自动重读重新核实。
        """
        if task_type == "signal_management" or self._active:
            if self._sync_transient():
                self.snapshotChanged.emit(self.snapshot)
            return
        if state not in ("idle", "failed"):
            self._foreign_task_active = True
            label = _BUSY_COPY.get(task_type, "抓包中")
            detail = f"{label}，当前功能不可用；结束自动恢复。"
            changed = self._sync_transient()
            if self.snapshot.detail != detail or self.snapshot.busy_label != label:
                self.snapshot = replace(self.snapshot, detail=detail, busy_label=label)
                changed = True
            if changed:
                self.snapshotChanged.emit(self.snapshot)
            return
        if task_type in _WIRELESS_WRITERS:
            self._verified = False
        # Connection broadcasts may already have cleared snapshot.busy before this slot.
        # Track the task lifecycle itself so completion always schedules one fresh read.
        was_busy = bool(self._foreign_task_active or self.snapshot.busy)
        self._foreign_task_active = False
        if self._sync_transient():
            self.snapshotChanged.emit(self.snapshot)
        if self.snapshot.busy_label:
            self.snapshot = replace(self.snapshot, busy_label="")
            self.snapshotChanged.emit(self.snapshot)
        if was_busy and task_type != "wireless_apply_check":
            self._refreshRequested.emit()

    def _try_auto_refresh(self):
        """他任务结束自动重读一次恢复；开不了工就给手动指引，不静默。"""
        if (self._active or self.owner._shutting_down or not self.snapshot.connected
                or getattr(self, "_auto_refresh_blocked", False)):
            return
        record_workflow(self.owner, "signal_refresh", "started", source="task_finished")
        if self._start(None):
            return
        self.snapshot = replace(self.snapshot, detail="任务结束，点“刷新信号列表”更新。")
        self.snapshotChanged.emit(self.snapshot)

    @property
    def active(self):
        """返回本模块是否有正在运行的后台核实任务，无副作用。"""
        return bool(self._active or (getattr(self, "setup", None) and self.setup.active))

    @Slot(object)
    def _connection_changed(self, snapshot):
        """同步连接状态；忙碌与核实位以互斥器与核实位为准，不闩锁。"""
        connected = snapshot.connection_state.value == "connected"
        if not connected:
            self._auto_refresh_blocked = False
            self._verified = False
            self.snapshot = SignalSnapshot(connected=False)
        else:
            self.snapshot = replace(self.snapshot, connected=True)
        self._sync_transient()
        self.snapshotChanged.emit(self.snapshot)

    def refresh(self):
        """主线程申请只读刷新；返回是否占用任务槽并启动后台工作。"""
        previous_blocked = self._auto_refresh_blocked
        setup = getattr(self, "setup", None)
        previous_revision = getattr(setup, "_inspection_revision", None)
        self._auto_refresh_blocked = False
        if setup is not None:
            setup._inspection_revision = None
        started = self._start(None)
        if not started:
            self._auto_refresh_blocked = previous_blocked
            if setup is not None:
                setup._inspection_revision = previous_revision
        return started

    def set_details_enabled(self, enabled):
        """纯显示选项；勾选后在空闲后台读取，不另加轮询。"""
        enabled = bool(enabled)
        changed = enabled != self.details_enabled
        self.details_enabled = enabled
        if changed and enabled and self.snapshot.connected and not self.owner.task_controller.busy:
            self.refresh()

    def change(self, section, enabled, confirmed=False):
        """主线程确认后申请单行启停；拒绝过期、未知行和未确认请求。"""
        row = next((r for r in self.snapshot.inventory.rows if r.section == section), None)
        if not confirmed or self.snapshot.stale or row is None or row.reason:
            return False
        if type(enabled) is not bool or (enabled == (not row.disabled) and not (enabled and row.radio_disabled)):
            return False
        return self._start((section, enabled))

    def repair(self, section, confirmed=False):
        """主线程确认后申请运行态修复；只收“已启用但未运行”的行。

        不修改 UCI，只重载该 radio 并核实接口回来，专治半残信号；其他状态、
        过期快照或未确认一律拒绝，不抢启用/禁用入口。
        """
        row = next((r for r in self.snapshot.inventory.rows if r.section == section), None)
        if not confirmed or self.snapshot.stale or row is None or row.reason:
            return False
        if row.radio_disabled or row.disabled or row.running == "active":
            return False
        return self._start(("__repair__", section))

    def cancel(self):
        """仅取消本模块任务；返回是否发出请求，不阻塞或强制杀线程。"""
        if not self._active:
            return self.setup.cancel()
        accepted = self.owner.cancel_openwrt_task()
        if accepted:
            self._auto_refresh_blocked = True
            self.setup._pending = False
            record_workflow(self.owner, "signal_refresh", "cancelled", reason="user_request")
        return accepted

    def refresh_from_worker(self, cancel_event):
        """供抓包 worker 在接管/恢复后调用；在后台读一次并回主线程发布。

        读失败不再静默：报一条警告指引用户等任务结束自动刷新，绝不能把抓包
        主体搞崩。任务已取消时不打扰（结束后本来就会自动刷新一次）。
        跨线程发 signalRefreshed，主线程槽里落快照。
        """
        try:
            inventory = self.manager.read(cancel_event, details=True) if self.details_enabled else self.manager.read(cancel_event)
        except Exception:
            if cancel_event is not None and cancel_event.is_set():
                return False
            try:
                self.messageGenerated.emit(
                    "接管后信号同步失败（不影响抓包），任务结束后会自动刷新一次。", "warning")
            except Exception:
                pass
            return False
        acknowledged = Event()
        try:
            self.signalRefreshed.emit((inventory, acknowledged))
        except Exception:
            return False
        if not acknowledged.wait(3):
            try:
                self.messageGenerated.emit(
                    "接管后信号列表未能及时显示，已停止启动抓包并准备恢复。", "warning")
            except Exception:
                pass
            return False
        return True

    @Slot(object)
    def _apply_external_inventory(self, payload):
        """落实外部推送的清单：只换数据不动状态，忙闲与按钮由任务槽决定。"""
        inventory, acknowledged = payload if isinstance(payload, tuple) else (payload, None)
        try:
            self.snapshot = replace(self.snapshot, inventory=inventory)
            self.snapshotChanged.emit(self.snapshot)
            self._message("信号列表已同步最新状态。", "info")
        finally:
            if acknowledged is not None:
                acknowledged.set()

    def install_dependencies(self):
        """主线程确认后安装信号依赖；装完自动重读，按钮只由快照回放变化。"""
        return self._start(("__install__",))

    def _start(self, request):
        """借用现有 worker 生命周期；页面销毁后仍由常驻服务持有任务。

        request 为 None=只读刷新，("__install__",)=安装依赖，
        (section, enabled)=单行启停，("__repair__", section)=运行态修复。
        SignalError 转结构化结果，错误码才能穿过 worker 的字符串通道回到主线程。
        """
        if self.owner._shutting_down or not self.snapshot.connected or self.owner.task_controller.busy:
            return False
        self._active = True
        session_id = uuid4().hex
        event = self.owner.task_controller.begin("signal_management", session_id)
        if event is None:
            self._active = False
            return False
        inventory = self.snapshot.inventory
        if request is None:
            details = self.details_enabled
            operation = self._guarded(
                lambda cancel, message=None: self.manager.read(cancel, details=True) if details else self.manager.read(cancel)
            )
            success_slot = self._succeeded
            detail = "正在读取或核实无线配置。"
        elif request == ("__install__",):
            # 设备档案与缓存目录在主线程快照，后台只用值对象，不碰后端。
            device = self.owner.openwrt_backend.snapshot.device
            cache_dir = default_cache_dir()
            operation = self._guarded(
                lambda cancel, message: self.manager.install_auto(
                    device, cache_dir, cancel, message)
            )
            success_slot = self._install_succeeded
            detail = "正在安装信号依赖；路由器没网时自动使用 \u8def\u5c0f\u7ba1 下载安装（需要本机联网），完成后自动重新读取。"
        elif len(request) == 2 and request[0] == "__repair__" and isinstance(request[1], str):
            operation = self._guarded(
                lambda cancel, message=None: self.manager.repair(
                    inventory, request[1], cancel)
            )
            success_slot = self._succeeded
            detail = "正在重新启用该信号所在无线网卡并核实接口回来，不修改名称密码等配置。"
        else:
            operation = self._guarded(
                lambda cancel, message=None: self.manager.change(
                    inventory, request[0], request[1], cancel)
            )
            success_slot = self._succeeded
            detail = "正在读取或核实无线配置。"
        self.snapshot = replace(
            self.snapshot, busy=True, stale=True,
            installing=(request == ("__install__",)), detail=detail,
        )
        self.snapshotChanged.emit(self.snapshot)
        from .task_completion import start_service_worker
        return start_service_worker(
            self.owner, session_id, operation, event, success_slot, self._failed,
            None, self._forward_worker_message if request == ("__install__",) else None,
        )

    def _forward_worker_message(self, text, level):
        """后台播报直转信息窗；只透传脱敏文本。"""
        self.messageGenerated.emit(str(text), str(level))

    @staticmethod
    def _guarded(operation):
        """把 SignalError 转成结果对象；未知异常仍走 worker 失败通道。"""

        def run(cancel_event, message=None):
            try:
                return operation(cancel_event, message)
            except SignalError as exc:
                return SignalFailure(exc.code, str(exc))

        return run

    @Slot(object)
    def _succeeded(self, result):
        """仅在后台返回核实结果后替换清单，不提前翻转按钮。"""
        if isinstance(result, SignalFailure):
            self._finish_failed(result.code, result.message)
            return
        record_workflow(self.owner, "signal_refresh", "completed",
                        reason="pending_changes" if result.pending_changes else "verified")
        self._verified = not result.pending_changes
        detail = ("已刷新信号，有待提交改动，可点“查看待提交改动”核对。" if result.pending_changes
                  else "无线配置与运行状态已核实。")
        self.snapshot = replace(self.snapshot, inventory=result, pending_changes=result.pending_changes, detail=detail)
        self.dependencies_missing = False
        self._message(self.snapshot.detail, "success")
        self.owner.task_controller.finish()
        self._active = False
        self._sync_transient()
        self.snapshotChanged.emit(self.snapshot)

    @Slot(object)
    def _install_succeeded(self, result):
        """依赖装完复测通过后自动重读；失败走结构化失败分支。"""
        if isinstance(result, SignalFailure):
            self._finish_failed(result.code, result.message)
            return
        self.dependencies_missing = False
        detail = "信号依赖已安装并通过核验，正在重新读取无线配置。"
        self._verified = False
        self.snapshot = replace(self.snapshot, detail=detail)
        self._message(detail, "success")
        self.owner.task_controller.finish()
        self._active = False
        self._sync_transient()
        self.snapshotChanged.emit(self.snapshot)
        if not self.refresh():
            self._message("依赖已装好，但任务槽被占用，请稍后手动点刷新信号列表。", "warning")

    @Slot(str, str)
    def _failed(self, summary, detail):
        """失败保留旧清单并锁定操作；不把未确认执行结果当作成功。"""
        self._finish_failed("", summary)

    def _finish_failed(self, code, summary):
        """统一失败收尾；只有缺依赖才点亮安装入口，其他失败不误导。

        缺依赖时状态行不说后端原文，改说指引动作；原文仍进信息窗留痕。
        """
        missing = bool(code == "dependencies")
        detail = (
            "路由器缺少信号运行环境，点“安装信号依赖”，"
            "\u8def\u5c0f\u7ba1 自动联网安装（路由器没网时用本机下载安装）。"
            if missing else summary
        )
        record_workflow(self.owner, "signal_refresh", "cancelled" if code == "cancelled" else "failed", reason=code or "worker_error")
        self._verified = False
        self.snapshot = replace(self.snapshot, detail=detail, pending_changes=code == "pending")
        self.dependencies_missing = missing
        self._message(summary, "error")
        self.owner.task_controller.finish(failed=True)
        self._active = False
        self._sync_transient()
        self.snapshotChanged.emit(self.snapshot)
        if code == "disconnected":
            self.reconnectRequired.emit(summary)

    def _message(self, text, level):
        """保存有限条独立消息用于页面重建回放；不写配置文件。"""
        self.messages = (self.messages + [(text, level)])[-100:]
        self.messageGenerated.emit(text, level)
