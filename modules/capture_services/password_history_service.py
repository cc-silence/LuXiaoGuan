"""Background attempt-history maintenance, independent of router connectivity."""
from uuid import uuid4
from PySide6.QtCore import QObject, Signal
from .models import CaptureSessionState
from .openwrt.models import CaptureTaskType
from .openwrt.password_history import clear_history

class PasswordHistoryService(QObject):
    recordsCleared = Signal()

    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner

    def clear_async(self):
        owner=self.owner
        if owner._shutting_down or owner.task_controller.busy:return False
        session=uuid4().hex
        event=owner.task_controller.begin(CaptureTaskType.PASSWORD_HISTORY_CLEAR.value,session,CaptureSessionState.CHECKING)
        if event is None:return False
        def clear(cancel):
            if cancel.is_set():raise InterruptedError('History clearing cancelled')
            return clear_history(cancel=cancel)
        def success(count):
            from .password_target_status import clear_records
            clear_records(owner)
            owner.messageGenerated.emit("\u5df2\u6e05\u7406\u5df2\u8bd5\u8bb0\u5f55\u548c\u5df2\u731c\u51fa\u5bc6\u7801\u8bb0\u5f55\uff0c\u5bfc\u51fa\u7684\u6587\u672c\u6587\u4ef6\u4fdd\u7559\u3002",'success')
            owner.task_controller.finish()
            self.recordsCleared.emit()
        def failure(summary,detail):
            owner.messageGenerated.emit('清理已尝试记录失败，原记录保留，请查看日志。','error')
            owner.task_controller.finish(failed=True)
        try:owner._start_openwrt_worker(session,clear,event,success,failure)
        except BaseException:
            owner.task_controller.finish(failed=True);raise
        return True
