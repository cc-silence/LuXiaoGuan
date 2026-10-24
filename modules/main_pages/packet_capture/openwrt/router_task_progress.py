"""Stage adapter over the existing shared TaskProgressDialog."""
from PySide6.QtCore import Qt
from modules.common_widgets.task_progress_dialog import TaskProgressDialog


class RouterTaskProgressDialog(TaskProgressDialog):
    def __init__(self,title,cancel,parent=None):
        self.phase="\u6b63\u5728\u68c0\u67e5\u8bbe\u5907\uff0c\u8bf7\u7a0d\u5019\u2026\u2026"
        self._allow_cancel=True
        super().__init__(title,parent=parent,show_immediately=False,cancel_text="\u53d6\u6d88",
                         progress_formatter=self._format_progress)
        self.setWindowModality(Qt.WindowModal)
        self.cancelRequested.connect(cancel)
        self.label=self.message
        self.update_stage(self.phase)

    def _format_progress(self,completed,total):
        return self.phase+"\uff08"+str(min(100,round(completed*100/total)))+"%\uff09"

    def update_progress(self,value):
        if not isinstance(value,dict):return
        if value.get("stage"):self.phase=str(value["stage"])
        if value.get("uncancellable"):
            self._allow_cancel=False
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("\u8bf7\u7b49\u5f85\u5b8c\u6210")
        if value.get("total",0)>0:super().update_progress(value)
        else:super().update_stage(self.phase)

    def request_cancel(self):
        if self.running and not self._allow_cancel:return
        super().request_cancel()

    def finish(self,success=True,message=""):
        super().finish(success,message)
