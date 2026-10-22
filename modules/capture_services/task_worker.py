"""抓包服务通用后台 worker。"""

from PySide6.QtCore import QObject, Signal, Slot


class CaptureTaskWorker(QObject):
    """在 QThread 中执行一个已完成主线程确认的普通可调用对象。

    参数：operation 接收 cancel_event 并返回普通 Python 结果；启用进度时还接收 progress
    回调；启用消息时还接收 message(text, level) 回调；只启用消息时接收 cancel 和
    message。信号：succeeded 返回结果，progressChanged 返回普通模型，messageChannel
    返回用户可见文本与级别，failed 返回用户摘要和详细诊断，finished 保证始终发出。
    副作用：operation 可能执行 SSH、文件或远端操作；本类禁止创建 QWidget/QDialog。
    """

    succeeded = Signal(object)
    failed = Signal(str, str)
    progressChanged = Signal(object)
    messageChannel = Signal(str, str)
    finished = Signal()

    def __init__(self, operation, cancel_event, progress_enabled=False, message_enabled=False, parent=None):
        """保存后台操作和取消事件；两个 enabled 决定是否传入线程安全回调。"""

        super().__init__(parent)
        self.operation = operation
        self.cancel_event = cancel_event
        self.progress_enabled = bool(progress_enabled)
        self.message_enabled = bool(message_enabled)

    @Slot()
    def run(self):
        """执行任务并保证任何路径都会发出 finished。"""

        try:
            if self.progress_enabled and self.message_enabled:
                result = self.operation(
                    self.cancel_event, self.progressChanged.emit, self.messageChannel.emit
                )
            elif self.progress_enabled:
                result = self.operation(self.cancel_event, self.progressChanged.emit)
            elif self.message_enabled:
                result = self.operation(self.cancel_event, self.messageChannel.emit)
            else:
                result = self.operation(self.cancel_event)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__, repr(exc))
        finally:
            self.finished.emit()
