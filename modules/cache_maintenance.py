"""由服务所有者持有的一次性后台维护；完成信号在真实线程结束后发出。"""

import logging
from collections import deque
from threading import Event
from PySide6.QtCore import QObject, QThread, Signal


class _MaintenanceThread(QThread):
    def __init__(self, action, parent):
        super().__init__(parent)
        self.action = action
        self.cancel = Event()
        self.error = ""

    def run(self):
        try:
            self.action(self.cancel)
        except Exception as exc:
            self.error = f"缓存维护未完成：{exc}。剩余文件已保留，请核对路径和权限。"


class CacheMaintenance(QObject):
    """单次任务槽；调用方以 active 阻止并发使用和提前退出，无主线程等待。"""
    activityChanged = Signal()
    warning = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = None
        self._pending = deque()

    @property
    def active(self):
        return self.thread is not None

    def start(self, action):
        if self.active:
            return False
        thread = _MaintenanceThread(action, self)
        self.thread = thread
        thread.finished.connect(self._finished)
        self.activityChanged.emit()
        try:
            thread.start()
        except Exception:
            self.thread = None
            thread.deleteLater()
            self.activityChanged.emit()
            raise
        return True

    def cancel(self):
        self._pending.clear()
        if self.thread is not None:
            self.thread.cancel.set()

    def enqueue(self, action):
        """连续删除共用串行队列，界面无需等待前一个目录清理完。"""
        if self.active:
            self._pending.append(action)
            return True
        return self.start(action)

    def _finished(self):
        thread = self.thread
        self.thread = None
        if thread.error:
            logging.getLogger(__name__).warning(thread.error)
            self.warning.emit(thread.error, "warning")
        thread.deleteLater()
        if self._pending:
            self.start(self._pending.popleft())
        else:
            self.activityChanged.emit()
