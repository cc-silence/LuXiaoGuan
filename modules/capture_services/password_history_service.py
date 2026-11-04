"""Background attempt-history maintenance, independent of router connectivity."""
from uuid import uuid4
from .models import CaptureSessionState
from .openwrt.models import CaptureTaskType
from .openwrt.password_history import clear_history

class PasswordHistoryService:
    def __init__(self,owner):self.owner=owner

    def clear_async(self):
        owner=self.owner
        if owner._shutting_down or owner.task_controller.busy:return False
        session=uuid4().hex
        event=owner.task_controller.begin(CaptureTaskType.PASSWORD_HISTORY_CLEAR.value,session,CaptureSessionState.CHECKING)
        if event is None:return False
        def clear(cancel):
            if cancel.is_set():raise InterruptedError('History clearing cancelled')
            return clear_history()
        def success(count):
            owner.messageGenerated.emit(f"已清理 {count} 条已尝试记录，下次可重新尝试。",'success')
            owner.task_controller.finish()
        def failure(summary,detail):
            owner.messageGenerated.emit('清理已尝试记录失败，原记录保留，请查看日志。','error')
            owner.task_controller.finish(failed=True)
        try:owner._start_openwrt_worker(session,clear,event,success,failure)
        except BaseException:
            owner.task_controller.finish(failed=True);raise
        return True
