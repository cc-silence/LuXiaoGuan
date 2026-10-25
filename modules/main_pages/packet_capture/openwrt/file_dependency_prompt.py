"""File browsing dependency diagnosis and confirmed installation flow."""
from PySide6.QtCore import QObject, Signal, Qt
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.common_widgets.task_progress_dialog import TaskProgressDialog


class FileDependencyPrompt(QObject):
    diagnoseRequested = Signal()

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.owner = getattr(page.service, "owner", None)
        self.dependency = getattr(self.owner, "dependency_service", None)
        self.session = None
        self.dialog = None
        self.binding = None
        self.original_error = ""
        self.action = None
        self._starting = False
        self._disposed = False
        self.diagnoseRequested.connect(self._diagnose, Qt.QueuedConnection)
        if self.dependency is not None:
            self.dependency.taskChanged.connect(self._changed)
            self.dependency.taskFinished.connect(self._finished, Qt.QueuedConnection)

    def handle_failure(self, message):
        if self.dependency is None or self._disposed:
            return False
        if self.binding is not None:
            return True
        self.binding = self.page.service.manager.ssh._client
        self.original_error = message
        self.diagnoseRequested.emit()
        return True

    def _same_connection(self):
        ssh = self.page.service.manager.ssh
        return not self._disposed and ssh.connected and ssh._client is self.binding

    def _diagnose(self):
        if not self._same_connection():
            self.binding = None
            return
        self._start("check")

    def _start(self, action):
        self.action = action
        self._starting = True
        try:
            started = (self.dependency.check_common_async() if action == "check" else
                       self.dependency.install_async({}, sftp_only=True))
        finally:
            self._starting = False
        if not started and self.binding is not None:
            self.binding = None
            MessageDialog("\u65e0\u6cd5\u6253\u5f00\u76ee\u5f55", self.original_error +
                          "\n\u8bf7\u7b49\u5f85\u5f53\u524d\u4efb\u52a1\u7ed3\u675f\uff0c\u518d\u5230\u8def\u7531\u5668\u767b\u5f55\u9875\u68c0\u67e5\u6587\u4ef6\u4f20\u8f93\u652f\u6301\u3002", self.page).exec()

    def _changed(self, task):
        if task is None or task.group != "common":
            return
        if self._starting:
            self.session = task.session_id
            self.dialog = TaskProgressDialog(
                "\u5b89\u88c5\u6587\u4ef6\u4f20\u8f93\u652f\u6301" if self.action == "install" else
                "\u68c0\u67e5\u6587\u4ef6\u4f20\u8f93\u652f\u6301", parent=self.page)
            self.dialog.cancelRequested.connect(lambda key=task.session_id: self.dependency.cancel(key))
        if task.session_id == self.session and self.dialog is not None:
            self.dialog.update_stage(task.message)

    def _finished(self, session, success, message):
        if session != self.session or self._disposed:
            return
        self.session = None
        same_connection = self._same_connection()
        item = next((item for item in self.owner.openwrt_snapshot.dependencies if item.key == "sftp"), None) if same_connection else None
        state = item.state.value if item is not None else "unknown"
        cancelled = (self.dialog is not None and self.dialog.cancelling) or "\u5df2\u53d6\u6d88" in message
        diagnosed = self.action == "check" and not cancelled and state in {"missing", "ready"}
        dialog, self.dialog = self.dialog, None
        if dialog is not None:
            dialog.finish(success or diagnosed, message)
        if cancelled or not same_connection or (not success and not diagnosed):
            self.binding = None
            return
        if self.action == "check" and state == "missing":
            result = ConfirmDialog("\u5b89\u88c5\u6587\u4ef6\u4f20\u8f93\u652f\u6301",
                "\u8def\u7531\u5668\u7f3a\u5c11\u6587\u4ef6\u4f20\u8f93\u652f\u6301\uff0c\u6682\u65f6\u65e0\u6cd5\u6d4f\u89c8\u3001\u4e0a\u4f20\u6216\u4e0b\u8f7d\u6587\u4ef6\u3002\n"
                "\u5c06\u5b89\u88c5 openssh-sftp-server \u53ca\u5176\u5fc5\u9700\u4f9d\u8d56\uff0c\u53ef\u80fd\u9700\u8981\u8054\u7f51\u4e0b\u8f7d\uff1b\u4e0d\u66ff\u6362 SSH \u767b\u5f55\u670d\u52a1\u3002\u5b89\u88c5\u6210\u529f\u540e\u91cd\u8bd5\u6253\u5f00\u76ee\u5f55\u3002",
                self.page, yes_text="\u5b89\u88c5").exec()
            if result == ConfirmDialog.Accepted and self._same_connection():
                self._start("install")
                return
        elif self.action == "install" and state == "ready":
            self.binding = None
            self.page._refresh(True)
            return
        else:
            detail = getattr(item, "detail", "")
            MessageDialog("\u65e0\u6cd5\u6253\u5f00\u76ee\u5f55", self.original_error +
                          ("\n" + detail if detail else ""), self.page).exec()
        self.binding = None

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.binding = None
        if self.dialog is not None:
            self.dialog.finish(True)
            self.dialog = None
        if self.dependency is not None:
            self.dependency.taskChanged.disconnect(self._changed)
            self.dependency.taskFinished.disconnect(self._finished)
