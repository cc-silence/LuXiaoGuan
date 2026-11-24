"""重连后恢复同一设备的未完成任务清理，不扫描或删除未知资源。"""
from dataclasses import dataclass
from weakref import proxy
import time


@dataclass(frozen=True)
class PendingCleanup:
    workspace: object
    takeover_record: object
    device_fingerprint: str


class ReconnectCleanup:
    """常驻后端持有一份未完成清理凭据；无线任务互斥保证不会无界累积。"""
    def __init__(self, backend):
        self.backend = proxy(backend)
        self.pending = None
        self._fingerprint = ""
        self._password_check_issue = None

    def set_password_check_issue(self, fingerprint, evidence):
        self._password_check_issue = (str(fingerprint), dict(evidence)) if evidence is not None else None

    def require_password_check_ready(self):
        issue = getattr(self, '_password_check_issue', None)
        if issue is not None and issue[0] == str(self.backend.ssh_client.host_fingerprint):
            from .password_recovery_admission import WARNING
            raise RuntimeError(WARNING)

    def require_ready(self, *, allow_unavailable_password_record=False, active_session_id=""):
        """新任务只在旧任务已确认清理后放行，不执行远端命令。"""
        if not allow_unavailable_password_record:
            self.require_password_check_ready()
        store = getattr(self.backend, 'password_recovery_store', None)
        fingerprint = getattr(self.backend.ssh_client, 'host_fingerprint', '')
        from .password_recovery_admission import read_records, RecoveryRecordsUnavailable
        try:
            records = read_records(store, fingerprint) if store is not None else []
        except RecoveryRecordsUnavailable:
            if not allow_unavailable_password_record:
                raise
            records = []
        if records:
            raise RuntimeError("\u4e0a\u6b21\u5bc6\u7801\u9a8c\u8bc1\u7684\u914d\u7f6e\u6062\u590d\u5c1a\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u540c\u4e00\u8def\u7531\u5668\uff0c\u8f6f\u4ef6\u4f1a\u5148\u81ea\u52a8\u6062\u590d\u5e76\u6838\u5b9e\u3002")
        from .radio_takeover import RadioTakeoverRecord
        takeover = getattr(getattr(self.backend, "radio_takeover", None), "pending_record", None)
        session = getattr(getattr(self.backend, "radio_session", None), "record", None)
        owns_active_takeover = (
            isinstance(takeover, RadioTakeoverRecord) and bool(active_session_id)
            and str(takeover.session_id) == str(active_session_id)
            and getattr(session, "session_id", None) == takeover.session_id
            and getattr(session, "radio", None) == takeover.phy)
        if (isinstance(takeover, RadioTakeoverRecord) and self.pending is None
                and not owns_active_takeover):
            raise RuntimeError("\u4e0a\u6b21\u65e0\u7ebf\u63a5\u7ba1\u5c1a\u672a\u786e\u8ba4\u6062\u590d\uff0c\u8bf7\u67e5\u770b\u767b\u5f55\u65f6\u7684\u6062\u590d\u5931\u8d25\u539f\u56e0\uff0c\u5e76\u6838\u5bf9\u8def\u7531\u5668\u65e0\u7ebf\u914d\u7f6e\u3002")
        if self.pending is not None:
            raise RuntimeError("上次任务清理未完成。请到“路由器登录”页断开后重新登录同一台路由器，软件会自动尝试清理。清理成功后再扫描、抓包；直接重新扫描不能消除残留占用。若仍提示清理失败，请导出诊断日志。")

    def remember(self, workspace, takeover_record):
        """保留确切工作目录、接管凭据和任务所属设备身份，不执行删除。"""
        if self.pending is not None:
            if self.pending.workspace.session_id != workspace.session_id:
                raise RuntimeError("旧任务恢复凭据尚未处理，拒绝覆盖。")
            return
        self.pending = PendingCleanup(workspace, takeover_record, self._fingerprint)

    def recover_for_login(self, fingerprint, cancel_event=None):
        """Unfinished cleanup is advisory for login, never an auth rejection."""
        try:
            return self.recover(fingerprint, cancel_event), False
        except InterruptedError:
            raise
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Login cancelled") from exc
            self.backend.command_runner.operation_diagnostics.record(
                "recovery", operation="reconnect_cleanup", status="deferred",
                exception_type=type(exc).__name__,
                connected_after=bool(self.backend.ssh_client.connected))
            return ("\u767b\u5f55\u5df2\u6210\u529f\uff1b\u65e7\u4efb\u52a1\u6e05\u7406\u672a\u5b8c\u6210\uff0c\u6062\u590d\u8bb0\u5f55\u5df2\u4fdd\u7559\uff0c\u4e0d\u5f71\u54cd\u672c\u6b21\u767b\u5f55\u3002", True)

    def recover(self, fingerprint, cancel_event=None):
        """同设备重连后按进程、无线资源、任务目录顺序恢复；失败或取消保留凭据。"""
        pending = self.pending
        if pending is None:
            self._fingerprint = str(fingerprint)
            return ""
        if not pending.device_fingerprint or pending.device_fingerprint != str(fingerprint):
            raise RuntimeError("已连接登录服务，但无法确认是上次任务的同一台路由器。旧任务恢复记录已保留，请重新连接原设备后再试。")
        workspace = pending.workspace
        sid = str(workspace.session_id)
        if not sid or sid in (".", "..") or "/" in sid or "\\" in sid or str(workspace.remote_dir) != "/tmp/winbox-capture/" + sid:
            raise RuntimeError("旧任务工作目录不合法，恢复记录已保留。")
        backend = self.backend
        deadline = time.monotonic() + 120
        def check():
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("清理已取消，旧任务恢复记录已保留。")
            if time.monotonic() >= deadline:
                raise TimeoutError("清理超时，恢复记录已保留。")
            if not backend.ssh_client.connected:
                raise RuntimeError("清理期间与路由器的连接断开，恢复记录已保留。")
        check()
        journal = backend.command_runner.operation_diagnostics
        journal.record("recovery", operation="reconnect_cleanup", status="started",
            connected_before=bool(backend.ssh_client.connected))
        try:
            for handle in backend.process_manager.handles():
                check()
                backend.process_manager.stop(handle, cancel_event=cancel_event)
            check()
            errors = backend._restore_task_wireless(workspace, pending.takeover_record, cancel_event=cancel_event)
            if errors:
                raise RuntimeError("；".join(errors))
            check()
            if backend.radio_session.active or backend.process_manager.handles():
                raise RuntimeError("旧任务的进程或临时接口尚未确认清理。")
            if not backend.workspace_manager.cleanup_remote(workspace, cancel_event=cancel_event):
                raise RuntimeError("旧任务的远端工作目录未清理完成。")
            check()
            from modules.capture_services.local_cache_cleanup import acknowledge_session
            acknowledge_session(workspace, True)
        except InterruptedError:
            journal.record("recovery", operation="reconnect_cleanup", status="cancelled",
                connected_after=bool(backend.ssh_client.connected))
            raise
        except Exception as exc:
            journal.record("recovery", operation="reconnect_cleanup", status="failed",
                exception_type=type(exc).__name__, connected_after=bool(backend.ssh_client.connected))
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("清理已取消，恢复记录已保留。") from exc
            raise RuntimeError("登录已成功，但上次任务的临时资源清理未完成：" + str(exc) + "恢复记录已保留，请保持路由器在线后重新登录。") from exc
        self.pending = None
        journal.record("recovery", operation="reconnect_cleanup", status="completed",
            connected_after=bool(backend.ssh_client.connected))
        self._fingerprint = str(fingerprint)
        note = "\u4e0a\u6b21\u4efb\u52a1\u9057\u7559\u7684\u4e34\u65f6\u8d44\u6e90\u5df2\u6e05\u7406\u5b8c\u6210\u3002"
        try:
            import json
            receipt = json.loads((workspace.local_dir / "takeover-recovery-result.json").read_text(encoding="utf-8"))
            if receipt.get("status") == "released":
                note += "\u65e0\u7ebf\u4fe1\u53f7\u672a\u80fd\u5b8c\u5168\u6062\u590d\uff0c\u5df2\u4fdd\u7559\u5f53\u524d\u8bbe\u7f6e\uff0c\u8bf7\u5728\u4fe1\u53f7\u7ba1\u7406\u4e2d\u6309\u9700\u5f00\u542f\u3002"
        except (OSError, ValueError, TypeError):
            pass
        return note
