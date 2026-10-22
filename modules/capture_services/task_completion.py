"""后台任务结果交接：先结束线程，再回到主线程发布结果。"""

from PySide6.QtCore import QObject, QThread, Qt, Slot
from .task_worker import CaptureTaskWorker


class CaptureTaskCompletion(QObject):
    """暂存一个任务的结果，不在 worker 仍在收尾时调用业务回调。

    owner 是常驻服务，session_id 仅标识本次任务；成功/失败回调均在主线程调用。
    结果处理后才移除任务引用，避免退出时提前关闭共享连接。
    """

    def __init__(self, owner, session_id, success, failure):
        super().__init__(owner)
        self._owner = owner
        self._session_id = str(session_id)
        self._success = success
        self._failure = failure
        self._outcome = None
        self._published = False

    @Slot(object)
    def succeeded(self, result):
        """仅暂存成功结果，不释放任务槽或创建下一个 worker。"""
        self._outcome = (True, result)

    @Slot(str, str)
    def failed(self, summary, detail):
        """暂存原有错误摘要和详情，保持失败回调的参数契约。"""
        self._outcome = (False, (summary, detail))

    @Slot()
    def publish(self):
        """线程结束后发布一次；即使业务回调出错也回收本任务的引用。"""
        if self._published:
            return
        self._published = True
        owner, outcome = self._owner, self._outcome
        try:
            if outcome is None:
                self._failure(
                    "后台任务未返回结果，请重试并导出错误日志。",
                    "Capture worker exited without a terminal result",
                )
            elif outcome[0]:
                self._success(outcome[1])
            else:
                self._failure(*outcome[1])
        finally:
            # 回调可能启动新任务；只清理本任务，不碰新任务槽或其结果。
            self._outcome = None
            self._success = self._failure = self._owner = None
            try:
                owner._cleanup_openwrt_worker(self._session_id)
            finally:
                self.deleteLater()


def start_capture_task(owner, session_id, operation, cancel_event,
                       success, failure, progress=None, message=None):
    """装配常驻服务持有的线程、worker 和每任务独立的结果交接对象。

    进度和消息仍实时发送；终局结果仅由线程结束信号触发。
    不阻塞主线程，不使用计时器、轮询或强制终止。
    """
    thread = QThread(owner)
    worker = completion = None
    try:
        worker = CaptureTaskWorker(
            operation, cancel_event,
            progress_enabled=progress is not None,
            message_enabled=message is not None,
        )
        completion = CaptureTaskCompletion(owner, session_id, success, failure)
        # 服务保留 thread/worker 强引用；thread 同时持有交接对象直到收尾。
        thread._capture_completion = completion
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(completion.succeeded, Qt.QueuedConnection)
        worker.failed.connect(completion.failed, Qt.QueuedConnection)
        if progress is not None:
            worker.progressChanged.connect(progress)
        if message is not None:
            worker.messageChannel.connect(message)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(completion.publish, Qt.QueuedConnection)
        thread.finished.connect(thread.deleteLater)
        owner._openwrt_workers[session_id] = (thread, worker)
        thread.start()
    except Exception:
        if not thread.isRunning():
            # No worker event loop started: deferred deletion cannot run there.
            from shiboken6 import delete, isValid
            owner._openwrt_workers.pop(session_id, None)
            for resource in (worker, completion):
                if resource is not None and isValid(resource):
                    delete(resource)
            thread.deleteLater()
        raise


def start_service_worker(owner, session_id, operation, cancel_event,
                         success, failure, progress=None, message=None):
    """Publish startup rejection through the same terminal path as worker errors."""
    try:
        owner._start_openwrt_worker(session_id, operation, cancel_event,
                                    success, failure, progress, message)
    except Exception as exc:
        from .workflow_diagnostics import record_workflow
        record_workflow(owner, "worker_start", "failed", stage="start", exception_type=type(exc).__name__)
        failure("\u540e\u53f0\u4efb\u52a1\u672a\u80fd\u542f\u52a8\uff0c\u8bf7\u91cd\u8bd5\u5e76\u5bfc\u51fa\u65e5\u5fd7\u3002", type(exc).__name__)
        activity = getattr(owner, "activityChanged", None)
        if activity is not None:
            activity.emit()
        return False
    return True
