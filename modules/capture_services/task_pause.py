"""任务安全边界的协作式暂停，不参与远端操作或清理。"""

from threading import Condition


class TaskPauseControl:
    """持有取消事件与状态通知；等待只由 worker 在安全边界调用。"""

    def __init__(self, cancel_event, changed):
        self._condition = Condition()
        self._cancel_event = cancel_event
        self._changed = changed
        self._state = "running"

    @property
    def state(self):
        with self._condition:
            return self._state

    def set_paused(self, paused):
        """请求暂停或继续，返回是否改变状态；清理或取消后拒绝请求。"""
        with self._condition:
            if self._cancel_event.is_set() or self._state == "cleaning":
                return False
            if paused:
                if self._state != "running":
                    return False
                self._state = "pausing"
            else:
                if self._state not in {"pausing", "paused"}:
                    return False
                self._state = "running"
                self._condition.notify_all()
            self._changed()
            return True

    def checkpoint(self):
        """在候选边界等待继续或取消，返回是否允许继续；不轮询。"""
        with self._condition:
            if self._state == "pausing" and not self._cancel_event.is_set():
                self._state = "paused"
                self._changed()
            self._condition.wait_for(
                lambda: self._state not in {"pausing", "paused"}
                or self._cancel_event.is_set()
            )
            return not self._cancel_event.is_set()

    def cancel(self):
        """设置取消并唤醒等待者，允许 worker 立即进入既有 finally。"""
        with self._condition:
            self._cancel_event.set()
            self._condition.notify_all()

    def finish(self):
        """进入不可暂停的清理阶段，清除待暂停请求并唤醒等待者。"""
        with self._condition:
            if self._state != "cleaning":
                self._state = "cleaning"
                self._condition.notify_all()
                self._changed()
