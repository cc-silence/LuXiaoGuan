"""Stage adapter over the existing shared TaskProgressDialog."""
import math
import time
from PySide6.QtCore import Qt, QTimer, Signal
from modules.common_widgets.task_progress_dialog import TaskProgressDialog


class RouterTaskProgressDialog(TaskProgressDialog):
    networkRenewRequested=Signal(str)
    def __init__(self,title,cancel,parent=None):
        self.phase="\u6b63\u5728\u68c0\u67e5\u8bbe\u5907\uff0c\u8bf7\u7a0d\u5019\u2026\u2026"
        self._allow_cancel=True
        self._deadline=None
        super().__init__(title,parent=parent,show_immediately=False,cancel_text="\u53d6\u6d88",
                         progress_formatter=self._format_progress)
        self._network_panel=None
        self._clock=QTimer(self)
        self._clock.setInterval(250)
        self._clock.timeout.connect(self._show_remaining)
        self.setWindowModality(Qt.WindowModal)
        self.cancelRequested.connect(cancel)
        self.label=self.message
        self.update_stage(self.phase)

    def _format_progress(self,completed,total):
        return self.phase+"\uff08"+str(min(100,round(completed*100/total)))+"%\uff09"

    def update_progress(self,value):
        if not self.running or not isinstance(value,dict):return
        if isinstance(value.get("network_recovery"),dict):
            if self._network_panel is None:
                from .lan_recovery_panel import LanRecoveryPanel
                self._network_panel=LanRecoveryPanel(self)
                self._network_panel.requested.connect(self.networkRenewRequested)
                self.content_layout.addWidget(self._network_panel)
            self._network_panel.update_payload(value["network_recovery"])
        if value.get("stage"):self.phase=str(value["stage"])
        if value.get("uncancellable"):
            self._allow_cancel=False
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("\u8bf7\u7b49\u5f85\u5b8c\u6210")
        if value.get("configuration_apply"):
            self._deadline=value.get("deadline")
            self.path_label.setText("\u6b63\u5728\u4fee\u6539\u8def\u7531\u5668\u914d\u7f6e\uff0c\u8bf7\u52ff\u65ad\u7535\u3002\u5b8c\u6210\u540e\u4f1a\u81ea\u52a8\u7ed3\u675f\u7b49\u5f85\u3002")
            self.path_label.show()
            self._show_remaining()
            if self._deadline is not None and self._deadline>time.monotonic():self._clock.start()
            else:self._clock.stop()
            return
        if value.get("total",0)>0:super().update_progress(value)
        else:super().update_stage(self.phase)

    def _show_remaining(self):
        if not self.running:
            self._clock.stop()
            return
        text=self.phase
        if self._deadline is not None:
            remaining=max(0,math.ceil(self._deadline-time.monotonic()))
            text += ("\n\u672c\u9636\u6bb5\u6700\u957f\u7b49\u5f85\u5269\u4f59 %d \u79d2" % remaining if remaining else
                     "\n\u7b49\u5f85\u65f6\u9650\u5df2\u5230\uff0c\u6b63\u5728\u6838\u5bf9\u7ed3\u679c\u6216\u6062\u590d\uff0c\u8bf7\u52ff\u65ad\u7535\u3002")
        self.bar.setRange(0,0)
        self.message.setText(text)
        if self._deadline is not None and remaining==0:self._clock.stop()

    def request_cancel(self):
        if self.running and not self._allow_cancel:return
        super().request_cancel()

    def finish(self,success=True,message=""):
        if not self.running:return
        self._clock.stop()
        self._deadline=None
        super().finish(success,message)
