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

    def require_ready(self):
        """新任务只在旧任务已确认清理后放行，不执行远端命令。"""
        if self.pending is not None:
            raise RuntimeError("上次任务的临时资源还没有清理完成。请到路由器登录页重新登录原设备，软件会先自动清理再允许新任务。")

    def remember(self, workspace, takeover_record):
        """保留确切工作目录、接管凭据和任务所属设备身份，不执行删除。"""
        if self.pending is not None:
            if self.pending.workspace.session_id != workspace.session_id:
                raise RuntimeError("旧任务恢复凭据尚未处理，拒绝覆盖。")
            return
        self.pending = PendingCleanup(workspace, takeover_record, self._fingerprint)

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
        return "上次任务遗留的临时资源已清理完成。"
