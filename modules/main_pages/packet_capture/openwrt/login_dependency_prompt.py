"""One confirmation per SSH login; install both dependency groups through one task."""
from PySide6.QtCore import QObject, Signal, Qt, QEvent
from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.common_widgets.task_progress_dialog import TaskProgressDialog
from modules.capture_services.openwrt.dependency_groups import REQUIRED_COMMON_KEYS, CAPTURE_KEYS, missing_packages


class LoginDependencyPrompt(QObject):
    requested = Signal()

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.owner = page.service
        self.service = self.owner.dependency_service
        self._queued = False
        self._disposed = False
        self._showing = False
        self._approved = None
        self.dialog = None
        self.progress = None
        self.progress_session = None
        self.requested.connect(self._drain, Qt.QueuedConnection)
        self.owner.openwrtSnapshotChanged.connect(self.observe)
        self.owner.activityChanged.connect(self.observe)
        self.service.taskChanged.connect(self._task_changed)
        self.service.taskFinished.connect(self._task_finished)
        page.installEventFilter(self)
        self.observe()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Show:
            self.observe()
        return False

    def observe(self, *_):
        if not self._disposed and not self._queued:
            self._queued = True
            self.requested.emit()

    def _binding(self):
        ssh = self.owner.openwrt_backend.ssh_client
        if not ssh.connected or not ssh.host_fingerprint:
            return None
        return ssh._client, ssh.host_fingerprint

    def _idle(self):
        return not (self.owner._shutting_down or self.owner.is_busy or self.service.active
                    or self.owner.task_controller.busy)

    def _drain(self):
        self._queued = False
        if self._disposed or self._showing:
            return
        binding = self._binding()
        if self._approved is not None:
            original, features = self._approved
            if binding != original:
                self._approved = None
            elif self._idle():
                self._approved = None
                self.service.install_async(features, scope='login_all')
            return
        if binding is None or not self._idle() or not self.page.window().isVisible():
            return
        snapshot = self.owner.openwrt_snapshot
        if snapshot.connection_state.value != 'connected':
            return
        rows = {item.key:item for item in snapshot.dependencies}
        keys = REQUIRED_COMMON_KEYS | CAPTURE_KEYS
        if not keys.issubset(rows) or any(rows[k].state.value == 'checking' for k in keys):
            return
        if getattr(self.service, '_login_prompt_binding', None) == binding:
            return
        missing = tuple(rows[k] for k in sorted(keys) if rows[k].state.value == 'missing')
        if not missing and any(rows[k].state.value == 'unknown' for k in keys):
            return
        self.service._login_prompt_binding = binding
        if not missing:
            return
        packages = missing_packages(missing, keys)
        lines = [item.display_name for item in missing]
        note = ('\u767b\u5f55\u6210\u529f\uff0c\u68c0\u6d4b\u5230\u8def\u7531\u5668\u7f3a\u5c11\u4ee5\u4e0b\u4f9d\u8d56\uff1a\n' + '\u3001'.join(lines)
            + ('\n\n\u7f3a\u5931\u8f6f\u4ef6\u5305\uff1a'+'\u3001'.join(packages) if packages else '')
            + '\n\n\u786e\u5b9a\u540e\u8865\u9f50\u6293\u5305\u548c\u5e38\u7528\u4f9d\u8d56\u4e24\u7ec4\u7684\u7f3a\u9879\uff0c\u5df2\u5173\u95ed\u7684\u6293\u5305\u529f\u80fd\u4e0d\u4f1a\u88ab\u542f\u7528\u3002\u53ea\u5b89\u88c5\u786e\u8ba4\u7f3a\u5931\u7684\u8def\u7531\u5668\u8f6f\u4ef6\u53ca\u5fc5\u9700\u914d\u5957\u4f9d\u8d56\uff0c\u4e0d\u5347\u7ea7\u5df2\u6709\u8f6f\u4ef6\u3002'
            + '\n\u590d\u7528\u672c\u5730\u79bb\u7ebf\u5305\u3001\u8def\u7531\u5668\u8f6f\u4ef6\u6e90\u548c\u7535\u8111\u4e0b\u8f7d\u8f6c\u88c5\u6d41\u7a0b\uff0c\u652f\u6301\u53d6\u6d88\u3002')
        features = {k: rows[k].state.value != 'disabled' for k in CAPTURE_KEYS}
        self._showing = True
        dialog = ConfirmDialog('\u5b89\u88c5\u7f3a\u5931\u4f9d\u8d56', note, self.page,
                               yes_text='\u786e\u5b9a\u5b89\u88c5', no_text='\u53d6\u6d88')
        self.dialog = dialog
        try:
            accepted = dialog.exec() == ConfirmDialog.Accepted
        finally:
            self.dialog = None
            self._showing = False
            dialog.deleteLater()
        if accepted and not self._disposed and self._binding() == binding:
            self._approved = binding, features
            self.observe()

    def _task_changed(self, task):
        if self._disposed or task is None or task.group != 'login_all':
            return
        if self.progress is None:
            self.progress_session = task.session_id
            self.progress = TaskProgressDialog('\u5b89\u88c5\u7f3a\u5931\u4f9d\u8d56',parent=self.page)
            self.progress.cancelRequested.connect(lambda session=task.session_id:self.service.cancel(session))
        self.progress.update_stage(task.message)

    def _task_finished(self, session, success, message):
        if self.progress is not None and self.progress_session == session:
            self.progress.finish(success,message)
            self.progress = None
            self.progress_session = None

    def dispose(self):
        self._disposed = True
        self._approved = None
        self.page.removeEventFilter(self)
        if self.dialog is not None:
            self.dialog.reject()
        if self.progress is not None:
            self.progress.finish(True)
            self.progress = None
        for signal,slot in ((self.owner.openwrtSnapshotChanged,self.observe),
                            (self.owner.activityChanged,self.observe),
                            (self.service.taskChanged,self._task_changed),
                            (self.service.taskFinished,self._task_finished)):
            signal.disconnect(slot)
