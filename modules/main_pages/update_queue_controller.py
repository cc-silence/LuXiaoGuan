"""软件更新队列：主线程按事件分配名额，等待任务不创建线程。"""
from collections import OrderedDict
from copy import deepcopy
import logging

from PySide6.QtCore import QObject, Qt, Signal, Slot

MAX_CONCURRENT_UPDATES = 3
_TERMINAL_STATES = {'ready', 'no_plan', 'success', 'failed'}


class UpdateQueueController(QObject):
    """统一管理预检和更新名额，同一软件只能入队一次。

    page 提供启动、状态和信息回调；所有方法在主线程调用。
    保存点击时的配置快照，不保留表格控件或已完成任务。
    """
    _dispatch_requested = Signal()

    def __init__(self, page):
        super().__init__(page)
        self._page = page
        self._waiting = OrderedDict()
        self._active = {}
        self._scheduled = False
        self._dispatching = False
        self._dispatch_requested.connect(self._dispatch, Qt.QueuedConnection)

    @property
    def busy(self):
        return bool(self._waiting or self._active)

    def contains(self, software_id):
        return software_id in self._waiting or software_id in self._active

    def status(self, software_id):
        if software_id in self._waiting:
            return 'queued'
        task = self._active.get(software_id)
        return task['status'] if task else None

    def enqueue(self, row_data, exclusive_key=''):
        """保存快照并入队；同一互斥分组不同时运行。"""
        sid = row_data.get('id', '')
        if not sid or self.contains(sid):
            return False
        row = {key: value for key, value in row_data.items() if key not in ('table', 'row')}
        row['node'] = deepcopy(row.get('node', {}))
        self._waiting[sid] = {'row': row, 'exclusive_key': exclusive_key, 'status': 'queued'}
        self._page._set_rows_status(sid, 'queued')
        self._notify_activity()
        self._schedule()
        return True

    def cancel(self, software_id):
        """只取消尚未启动的任务，不停止运行中的安装。"""
        task = self._waiting.pop(software_id, None)
        if task is None:
            return False
        self._page._set_rows_status(software_id, 'ready')
        self._notify_activity()
        self._schedule()
        return True

    def record_status(self, software_id, status):
        """记录状态；预检中止可立即释放，更新则等线程真正退出。"""
        task = self._active.get(software_id)
        if task is None:
            return
        task['status'] = status
        if (status in _TERMINAL_STATES and software_id not in self._page._threads
                and software_id not in self._page._preflight_threads):
            self.finish(software_id)

    def finish(self, software_id):
        """释放名额和快照，用事件触发下一件更新。"""
        if self._active.pop(software_id, None) is not None:
            self._notify_activity()
            self._schedule()

    def _notify_activity(self):
        runtime = self._page.runtime
        if runtime is not None:
            runtime.notify_activity_changed()

    def _schedule(self):
        if not self._scheduled:
            self._scheduled = True
            self._dispatch_requested.emit()

    def _next_ready(self):
        resources = {task['exclusive_key'] for task in self._active.values() if task['exclusive_key']}
        return next((sid for sid, task in self._waiting.items()
                     if not task['exclusive_key'] or task['exclusive_key'] not in resources), None)

    @Slot()
    def _dispatch(self):
        """有空位时按顺序启动；防止模态确认框的事件循环重入。"""
        self._scheduled = False
        if self._dispatching:
            return
        self._dispatching = True
        try:
            while len(self._active) < MAX_CONCURRENT_UPDATES:
                sid = self._next_ready()
                if sid is None:
                    break
                task = self._waiting.pop(sid)
                self._active[sid] = task
                self._page._set_rows_status(sid, 'checking_processes')
                try:
                    self._page._begin_queued_update(task['row'])
                except Exception as exc:
                    logging.getLogger('winbox.update').exception('Unable to start queued update')
                    self._page._on_update_finished(sid, False, f"启动更新失败：{exc}")
        finally:
            self._dispatching = False
            if len(self._active) < MAX_CONCURRENT_UPDATES and self._next_ready() is not None:
                self._schedule()
