"""Shared session-bound presentation of capture dependency tasks."""
from PySide6.QtCore import QObject
from modules.common_widgets.task_progress_dialog import TaskProgressDialog


class CaptureDependencyProgress(QObject):
    def __init__(self, page, service, accepts, title=None):
        super().__init__(page)
        self.page = page
        self.service = service
        self.accepts = accepts
        self.title = title
        self.dialog = None
        self.session = None
        self.disposed = False
        service.taskChanged.connect(self.changed)
        service.taskFinished.connect(self.finished)

    def changed(self, task):
        if self.disposed or task is None:
            return
        if self.session is None:
            if not self.accepts(task):
                return
            self.session = task.session_id
            title = self.title or {
                "install": "\u5b89\u88c5\u6293\u5305\u4f9d\u8d56",
                "uninstall": "\u5378\u8f7d\u6293\u5305\u4f9d\u8d56",
                "check": "\u68c0\u67e5\u6293\u5305\u4f9d\u8d56",
            }[task.action]
            self.dialog = TaskProgressDialog(title, parent=self.page)
            self.dialog.cancelRequested.connect(
                lambda session=task.session_id: self.service.cancel(session))
        if task.session_id == self.session:
            self.dialog.update_stage(task.message)

    def finished(self, session, success, message):
        if self.disposed or session != self.session:
            return
        dialog, self.dialog = self.dialog, None
        self.session = None
        if dialog is not None:
            dialog.finish(success, message)

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.service.taskChanged.disconnect(self.changed)
        self.service.taskFinished.disconnect(self.finished)
        if self.dialog is not None:
            self.dialog.finish(True)
            self.dialog = None
        self.session = None
