"""无线设置后台入口，复用公共连接、任务互斥和线程收尾。"""
from dataclasses import replace
from uuid import uuid4
from PySide6.QtCore import QObject, Qt, Signal, Slot
from .openwrt.wireless_setup_manager import WirelessSetupManager
from .openwrt.wireless_setup_models import WirelessState, WirelessTaskResult, StoredWirelessPassword
from .openwrt.radio_identity import valid_radio_name
from .workflow_diagnostics import record_workflow


class WirelessSetupService(QObject):
    """常驻状态不含密码，页面释放不妨碍后台恢复。"""
    snapshotChanged = Signal(object)
    completed = Signal(object)
    _checkRequested = Signal()
    _scanBatch = Signal(str, object, object)

    def __init__(self, signals):
        super().__init__(signals)
        self.signals, self.owner = signals, signals.owner
        self.manager = WirelessSetupManager(self.owner.openwrt_backend.command_runner)
        self.snapshot = WirelessState()
        self.active = self._pending = self._enabled = False
        self._identity = None
        self._dependency_key = None
        self._inspection_revision = None
        self._scan_session = None
        self._scanBatch.connect(self._scan_results, Qt.QueuedConnection)
        self._checkRequested.connect(self._auto_check, Qt.QueuedConnection)
        self.owner.openwrtSnapshotChanged.connect(self._connection_changed)
        self.owner.task_controller.stateChanged.connect(self._task_changed)
        self.signals.snapshotChanged.connect(self._inventory_changed)

    def enable(self):
        """第一次开页请求检测；已有任务时等待释放。"""
        self._enabled = True
        if self.snapshot.environment is None:
            self._pending = True
            self._checkRequested.emit()

    def _connection_changed(self, state):
        identity = self._connection_identity() if state.connection_state.value == "connected" else None
        dependencies = tuple(sorted((item.key, item.state.value, tuple(item.missing_packages))
                                    for item in getattr(state, "dependencies", ())
                                    if item.key in {"wpa_cli", "signal", "iw", "iwinfo"}))
        changed = dependencies != self._dependency_key
        self._dependency_key = dependencies
        if identity is self._identity:
            if changed and identity is not None and not self.active and self._enabled and self.snapshot.environment is not None:
                self.snapshot = replace(self.snapshot, environment=None, detail="无线依赖已变化，正在重新检测。")
                self.snapshotChanged.emit(self.snapshot)
                self._pending = True
                self._checkRequested.emit()
            return
        self._identity = identity
        self._inspection_revision = None
        self.manager._proof = None
        self.snapshot = WirelessState()
        self.snapshotChanged.emit(self.snapshot)
        self._pending = self._enabled and identity is not None
        self._checkRequested.emit()

    def _inventory_changed(self, snapshot):
        """无线内容确实变化后重新检测，普通刷新不产生检测循环。"""
        env = self.snapshot.environment
        revision = snapshot.inventory.revision
        if (not getattr(self.signals, "_auto_refresh_blocked", False) and
                not self.active and snapshot.connected and not snapshot.busy and not snapshot.stale and
                len(revision) == 64 and revision != getattr(self, "_inspection_revision", None) and
                (env is None or not env.ready or env.data.get("revision", "")[:64] != revision)):
            self.snapshot = replace(self.snapshot, environment=None, detail="无线配置已变化，正在重新检测。")
            self.snapshotChanged.emit(self.snapshot)
            self._pending = self._enabled
            self._checkRequested.emit()

    def _task_changed(self, state, _kind, _session):
        if state in ("idle", "failed") and self._pending:
            self._checkRequested.emit()

    @Slot()
    def _auto_check(self):
        if getattr(self.signals, "_auto_refresh_blocked", False):
            self._pending = False
            return
        if self._pending and not self.owner.task_controller.busy and self.signals.snapshot.connected:
            self._pending = False
            self.check()

    def _connection_identity(self):
        ssh = self.owner.openwrt_backend.command_runner.ssh_client
        return getattr(ssh, "_client", ssh)

    def check(self):
        return self._start("inspect", lambda cancel, message: self.manager.inspect(cancel))

    def read_password(self, section, revision):
        return self._start("password", lambda cancel, message: self.manager.read_password(section, revision, cancel))

    def relocation_preview(self):
        from .openwrt.relocation_manager import RelocationManager
        return self._start("move_preview", lambda cancel, message: RelocationManager(self.manager).preview(cancel))

    def relocate(self, review):
        from copy import deepcopy
        from .openwrt.relocation_manager import RelocationManager
        approved = deepcopy(review)
        return self._start("relocate", lambda cancel, message: RelocationManager(self.manager).apply(approved, cancel, message))

    def orphan_preview(self):
        from .openwrt.orphan_interface_manager import OrphanInterfaceManager
        return self._start("orphan_preview", lambda cancel, message: OrphanInterfaceManager(self.manager).preview(cancel))

    def cleanup_orphans(self, review, selected):
        from copy import deepcopy
        from .openwrt.orphan_interface_manager import OrphanInterfaceManager
        approved, names = deepcopy(review), tuple(selected)
        return self._start("orphan_cleanup", lambda cancel, message: OrphanInterfaceManager(self.manager).cleanup(approved, names, cancel, message))

    def review_pending(self):
        return self._start("pending_review", lambda cancel, message: self.manager.review_pending(cancel))

    def commit_pending(self, token):
        return self._start("pending_commit", lambda cancel, message: self.manager.commit_pending(token, cancel))

    def install(self, approved):
        device = self.owner.openwrt_backend.snapshot.device
        return self._start("install", lambda cancel, message: self.manager.install(tuple(approved), device, cancel, message))

    def scan(self, seconds, radio):
        if not valid_radio_name(radio):
            return False
        return self._start("scan", lambda cancel, message, on_results=None: self.manager.scan(seconds, cancel, message, radio=radio, on_results=on_results),
                           scan_radio=radio)

    @Slot(str, object, object)
    def _scan_results(self, session, identity, networks):
        if (not self.active or self.snapshot.action != "scan" or session != self._scan_session
                or self._connection_identity() is not identity or not self.signals.snapshot.connected):
            return
        networks = tuple(networks)[:1024]
        if networks != self.snapshot.networks:
            self.snapshot = replace(self.snapshot, networks=networks)
            self.snapshotChanged.emit(self.snapshot)

    def save(self, request, *, apply=True):
        return self._start("save" if apply else "save_only",
            lambda cancel, message: self.manager.save(request, cancel, message, apply=apply))

    def delete(self, section, revision):
        return self._start("delete", lambda cancel, message: self.manager.delete(section, revision, cancel, message))

    def cancel(self):
        """仅取消自己的任务，恢复仍由后台线程负责。"""
        if not self.active or self.snapshot.action == "pending_commit":
            return False
        accepted = self.owner.cancel_openwrt_task()
        if accepted:
            self._pending = False
            self.signals._auto_refresh_blocked = True
            record_workflow(self.owner, "wireless_setup", "cancelled", stage=self.snapshot.action, reason="user_request")
        return accepted

    def _start(self, action, operation, *, scan_radio=""):
        if self.active or self.owner._shutting_down or not self.signals.snapshot.connected or self.owner.task_controller.busy:
            return False
        self.active = True
        identity = self._connection_identity()
        self._identity = identity
        session = uuid4().hex
        kind = "wireless_inspect" if action == "inspect" else "wireless_setup"
        event = self.owner.task_controller.begin(kind, session)
        if event is None:
            self.active = False
            return False
        self.signals._auto_refresh_blocked = False
        if action == "inspect":
            inventory = getattr(self.signals.snapshot, "inventory", None)
            self._inspection_revision = getattr(inventory, "revision", None)
        record_workflow(self.owner, "wireless_setup", "started", stage=action)
        self._scan_session = session if action == "scan" else None
        self.snapshot = replace(self.snapshot, busy=True, action=action,
            scan_radio=scan_radio if action == "scan" else self.snapshot.scan_radio,
            networks=() if action == "scan" else self.snapshot.networks, detail={
            "scan": "正在用 " + scan_radio + " 扫描上级信号，可点击停止扫描。",
            "inspect": "正在重新检测无线组件与配置。"}.get(action, self.snapshot.detail))
        self.snapshotChanged.emit(self.snapshot)

        def run(cancel, message=None):
            try:
                report = message or (lambda *_: None)
                if action == "scan":
                    value = operation(cancel, report,
                        lambda rows: self._scanBatch.emit(session, identity, rows))
                else:
                    value = operation(cancel, report)
                return WirelessTaskResult(action, value)
            except (ValueError, InterruptedError) as exc:
                return WirelessTaskResult(action, error=str(exc), code="cancelled" if isinstance(exc, InterruptedError) else "invalid_response")
            except Exception as exc:
                from .openwrt.signal_manager import SignalError
                safe = str(exc) if isinstance(exc, SignalError) else "无线任务执行失败，请检查连接和组件后重试。"
                return WirelessTaskResult(action, error=safe, code=exc.code if isinstance(exc, SignalError) else "internal")

        def finished(result):
            valid = self._connection_identity() is identity and self.signals.snapshot.connected
            self.owner.task_controller.finish(failed=bool(result.error))
            self.active = False
            self.snapshot = replace(self.snapshot, busy=False, action="")
            if valid:
                if not result.error and action in ("inspect", "install"):
                    env = result.value
                    detail = "无线设置环境已就绪。" if env.ready else "；".join((*env.issues, "缺少组件：" + "、".join(env.missing) if env.missing else "")).strip("；")
                    self.snapshot = replace(self.snapshot, environment=env, detail=detail, pending_changes=False)
                    record_workflow(self.owner, "wireless_setup", "completed" if env.ready else "blocked",
                                    stage=action, reason="ready" if env.ready else detail)
                    if not env.ready:
                        self.signals._message(detail, "warning")
                elif not result.error and action == "pending_commit":
                    self.snapshot = replace(self.snapshot, environment=None, pending_changes=False,
                        detail="配置已提交，正在刷新。本次只保存，未重新加载网络。")
                    self.signals._message(self.snapshot.detail, "success")
                    self.signals.refresh()
                elif not result.error and action == "orphan_cleanup":
                    detail = "已清理 %d 个已确认无人使用的遗留接口，未重载网络。" % len(result.value["removed"])
                    self.snapshot = replace(self.snapshot, environment=None, detail=detail)
                    self.signals._message(detail, "success")
                    self.signals.refresh()
                    self._pending = True
                    self._checkRequested.emit()
                elif not result.error and action == "relocate":
                    detail = "\u642c\u5bb6\u6e05\u7406\u5df2\u5b8c\u6210\uff1a\u5df2\u5220\u9664 %d \u4e2a\u65e0\u7ebf\u914d\u7f6e\uff0c\u66f4\u6362 %d \u4e2a\u7f51\u7edc MAC\uff0c\u4e3b\u673a\u540d\u4e3a %s\u3002" % (result.value["removed"], result.value["macs"], result.value["hostname"])
                    self.snapshot = replace(self.snapshot, environment=None, networks=(), scan_radio="", pending_changes=False, detail=detail)
                    self.signals._message(detail, "success")
                    self.signals.refresh()
                    self._pending = True
                    self._checkRequested.emit()
                elif not result.error and action == "scan":
                    record_workflow(self.owner, "wireless_setup", "completed", stage=action, reason="verified")
                    self.snapshot = replace(self.snapshot, networks=result.value, detail=self.snapshot.scan_radio + " 扫描完成，共发现 %d 个接入点。" % len(result.value))
                elif not result.error and action == "save_only":
                    detail = "信号已保存为禁用，需手动启用；本次未重载无线。"
                    self.snapshot = replace(self.snapshot, environment=None, detail=detail)
                    self.signals._message(detail, "success")
                    self.signals.refresh()
                    self._pending = True
                    self._checkRequested.emit()
                elif not result.error and action in ("save", "delete"):
                    self.snapshot = replace(self.snapshot, environment=None, detail="信号已删除，正在重新读取。" if action == "delete" else "设置已保存，正在重新检测。")
                    self._pending = True
                    self._checkRequested.emit()
                    if action == "delete":
                        self.signals._message("信号已删除并核对剩余无线状态。" + ("保留了共用或无法确认所属的网络接口。" if result.value.get("retained_network") else ""), "success")
                        self.signals.refresh()
                elif result.error:
                    record_workflow(self.owner, "wireless_setup", "cancelled" if isinstance(result.error, str) and result.code == "cancelled" else "failed",
                                    stage=action, reason=result.code or "invalid_response")
                    self.snapshot = replace(self.snapshot, detail=result.error, environment=None if action == "inspect" else self.snapshot.environment,
                        pending_changes=result.code == "pending" or self.snapshot.pending_changes)
                self.snapshotChanged.emit(self.snapshot)
                self.completed.emit(result)
                if result.error:
                    self.signals._message(result.error, "error")
            else:
                text = result.error or "连接已断开或路由器已变化，原任务结果不能用于当前设备，请重新读取。"
                if result.error or action in ("save", "save_only", "delete", "install", "pending_commit", "relocate", "orphan_cleanup"):
                    self.signals._message("原路由器任务：" + text, "warning")
                self.manager._proof = None
                self.snapshot = WirelessState()
                self.snapshotChanged.emit(self.snapshot)
                self.completed.emit(WirelessTaskResult(action, error=text, code="connection_changed"))

            if isinstance(result.value, StoredWirelessPassword):
                result.value.clear()

        from .task_completion import start_service_worker
        return start_service_worker(self.owner, session, run, event, finished,
            lambda *_: finished(WirelessTaskResult(action, error="无线任务异常结束，请重新检查设备。")),
            None, self.signals._forward_worker_message)
