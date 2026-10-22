"""抓包后台任务的单任务状态控制器。"""

from threading import Event, Lock

from PySide6.QtCore import QObject, Signal

from modules.capture_services.models import CaptureSessionState
from modules.capture_services.openwrt.models import CaptureTaskType
from modules.capture_services.task_pause import TaskPauseControl


class CaptureTaskController(QObject):
    """保证同一时间只有一个会修改无线环境的抓包任务。

    参数：parent 是 QObject 所有者，通常为 CaptureService。
    返回值：begin 返回本次任务的取消事件；已有任务时返回 None。
    副作用：维护当前任务类型、会话 ID 和取消事件，并通过 stateChanged 通知服务层。
    调用限制：本类不创建线程、不执行命令；worker 必须在 finally 中调用 finish。
    """

    stateChanged = Signal(str, str, str)
    pauseChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = Lock()
        self._cancel_event = Event()
        self._state = CaptureSessionState.IDLE
        self._task_type = ""
        self._session_id = ""
        self.pause_control = None

    @property
    def busy(self):
        """返回当前是否已有任务占用无线环境，无副作用。"""

        return self._state in {
            CaptureSessionState.CHECKING,
            CaptureSessionState.STARTING,
            CaptureSessionState.CAPTURING,
            CaptureSessionState.STOPPING,
        }

    def begin(self, task_type, session_id, state=CaptureSessionState.STARTING):
        """占用任务槽并返回独立取消事件；已有任务时返回 None。"""

        if not self._lock.acquire(blocking=False):
            return None
        self._cancel_event = Event()
        self._task_type = str(task_type or "")
        self._session_id = str(session_id or "")
        self.pause_control = (
            TaskPauseControl(self._cancel_event, lambda key=self._session_id: self.pauseChanged.emit(key))
            if self._task_type == CaptureTaskType.PASSWORD_VERIFY.value else None
        )
        self.set_state(state)
        return self._cancel_event

    def set_state(self, state):
        """更新任务状态；只有持有任务槽时才允许进入运行状态。"""

        normalized = state if isinstance(state, CaptureSessionState) else CaptureSessionState(state)
        self._state = normalized
        self.stateChanged.emit(normalized.value, self._task_type, self._session_id)

    def cancel(self):
        """请求当前 worker 正常停止，不等待线程、不强制终止。"""

        if not self.busy or self._cancel_event.is_set():
            return False
        if self.pause_control is not None:
            self.pause_control.cancel()
        else:
            self._cancel_event.set()
        self.set_state(CaptureSessionState.STOPPING)
        return True

    def set_paused(self, paused):
        """仅对当前可暂停任务发请求，不执行远端操作、不阻塞主线程。"""
        if not self.busy or self.pause_control is None:
            return False
        return self.pause_control.set_paused(paused)

    def finish(self, failed=False):
        """释放任务槽；failed 决定最终通知 FAILED 或 IDLE。"""

        if not self._lock.locked():
            return
        if self.pause_control is not None:
            self.pause_control.finish()
            self.pause_control = None
        final_state = CaptureSessionState.FAILED if failed else CaptureSessionState.IDLE
        self._state = final_state
        self.stateChanged.emit(final_state.value, self._task_type, self._session_id)
        self._task_type = ""
        self._session_id = ""
        self._lock.release()

    def snapshot(self):
        """返回可供服务层建模的状态元组，不暴露线程或后端对象。"""

        return self._state, self._task_type, self._session_id
