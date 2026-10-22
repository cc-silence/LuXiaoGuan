"""启动防火墙审计的公共进度弹窗。"""
from PySide6.QtCore import QObject, Qt
from modules.common_widgets.task_progress_dialog import TaskProgressDialog


class _AuditDialog(TaskProgressDialog):
    def request_cancel(self):
        """检查中不允许关闭，避免跳过放行检查。"""
        if not self.running:
            super().request_cancel()


class StartupFirewallPresenter(QObject):
    """只在主线程管理模态弹窗，不执行系统命令。"""
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.dialog = None
        self.done = False
        controller.finished.connect(self._finished)
        controller.stage_changed.connect(self._stage_changed)

    def show_for(self, window):
        """锁住主窗口操作直到检查结束。"""
        if self.done or self.dialog is not None:
            return
        self.dialog = _AuditDialog("防火墙检查", parent=window, show_immediately=False)
        self.dialog.setWindowModality(Qt.ApplicationModal)
        self.dialog.cancel_button.hide()
        self._stage_changed(getattr(self.controller,"stage","checking"))
        self.dialog.show()
        self.controller.start()

    def _stage_changed(self, stage):
        messages = {"checking": "\u6b63\u5728\u68c0\u67e5\u9632\u706b\u5899\u89c4\u5219\u2026\u2026",
                    "adding": "\u6b63\u5728\u6dfb\u52a0\u9632\u706b\u5899\u89c4\u5219\u2026\u2026"}
        if self.dialog is not None and stage in messages:
            self.dialog.update_stage(messages[stage])

    def _finished(self, success, error):
        self.done = bool(success)
        if self.dialog is not None:
            dialog = self.dialog
            if success:
                self.dialog = None
            else:
                dialog.finished.connect(self._closed)
            dialog.cancel_button.show()
            dialog.finish(success, error)

    def _closed(self, _result):
        self.dialog = None
