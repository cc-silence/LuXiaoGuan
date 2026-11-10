"""Show local recovery-directory access failures on the UI thread."""
from PySide6.QtCore import QObject, Qt, Signal, QRunnable, QThreadPool
from modules.common_widgets.common_dialogs import MessageDialog, ConfirmDialog


class BackupSignals(QObject):
    finished = Signal(bool)


class BackupWorker(QRunnable):
    def __init__(self, store, issue):
        super().__init__()
        self.store, self.issue = store, issue
        self.signals = BackupSignals()

    def run(self):
        try:
            self.store.backup_corrupt(self.issue)
        except Exception:
            import logging
            logging.getLogger(__name__).warning('Recovery record backup failed')
            self.signals.finished.emit(False)
        else:
            self.signals.finished.emit(True)


class RecoveryAccessPrompt(QObject):
    requested = Signal()

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self._shown_connection = None
        self._dialog = None
        self._worker = None
        self.requested.connect(self._show, Qt.QueuedConnection)
        page.service.openwrtSnapshotChanged.connect(self.observe)

    def observe(self, snapshot):
        if getattr(getattr(snapshot, 'connection_state', None), 'value', '') == 'connected':
            self.requested.emit()

    def _show(self):
        if self._dialog is not None or self._worker is not None:
            return
        backend = self.page.service.openwrt_backend
        ssh = backend.ssh_client
        issue = getattr(backend.reconnect_cleanup, '_password_check_issue', None)
        connection = getattr(ssh, '_client', None)
        if not getattr(ssh, 'connected', False) or connection is None or issue is None:
            return
        if issue[0] != str(getattr(ssh, 'host_fingerprint', '')):
            return
        record = getattr(backend.reconnect_cleanup, '_corrupt_password_record', None)
        if record is not None and record.fingerprint == issue[0]:
            if connection is self._shown_connection:
                return
            self._shown_connection = connection
            self._review_corrupt(backend, record, connection)
            return
        reason = issue[1].get('reason')
        if reason not in ('recovery_directory', 'recovery_record_io', 'recovery_record_validation', 'recovery_record_json'):
            return
        if connection is self._shown_connection:
            return
        self._shown_connection = connection
        root = backend.password_recovery_store.root
        if reason in ('recovery_directory', 'recovery_record_io'):
            message = ('\u8f6f\u4ef6\u65e0\u6cd5\u6b63\u5e38\u68c0\u67e5\u672c\u5730\u6062\u590d\u6570\u636e\u3002\u8bf7\u68c0\u67e5\u8be5\u76ee\u5f55\u662f\u5426\u53ef\u8bbf\u95ee\u3001\u5f53\u524d\u7528\u6237\u662f\u5426\u6709\u8bfb\u5199\u6743\u9650\uff0c\u6216\u5c06\u8f6f\u4ef6\u79fb\u5230\u53ef\u5199\u4f4d\u7f6e\u540e\u91cd\u8bd5\u3002'
                       + '\n\n' + str(root)
                       + '\n\n' + '\u539f\u6062\u590d\u6570\u636e\u672a\u5220\u9664\uff0c\u8bf7\u4fdd\u7559\u539f\u8f6f\u4ef6\u76ee\u5f55\u4ee5\u4fbf\u6392\u67e5\u3002')
        else:
            message = ('\u672c\u5730\u6062\u590d\u8bb0\u5f55\u672a\u901a\u8fc7\u6821\u9a8c\uff0c\u53ef\u80fd\u662f\u683c\u5f0f\u4e0d\u517c\u5bb9\u6216\u8eab\u4efd\u65e0\u6cd5\u786e\u8ba4\u3002\u8f6f\u4ef6\u672a\u79fb\u52a8\u6216\u5220\u9664\u6570\u636e\uff0c\u8bf7\u4fdd\u7559\u8be5\u76ee\u5f55\u4ee5\u4fbf\u6392\u67e5\u3002\n\n' + str(root))
        dialog = MessageDialog('\u672c\u5730\u6062\u590d\u6570\u636e\u68c0\u67e5\u5931\u8d25', message, self.page)
        self._dialog = dialog
        dialog.finished.connect(self._finished)
        dialog.open()

    def _review_corrupt(self, backend, record, connection):
        note = ('\u8f6f\u4ef6\u53d1\u73b0\u672c\u5730\u6062\u590d\u8bb0\u5f55\u5185\u5bb9\u65e0\u6548\uff0c\u65e0\u6cd5\u7528\u5b83\u6062\u590d\u8def\u7531\u5668\u914d\u7f6e\u3002\u662f\u5426\u5907\u4efd\u5e76\u79fb\u51fa\u8be5\u8bb0\u5f55\uff1f\u539f\u6570\u636e\u4f1a\u4fdd\u7559\uff0c\u4e0d\u4f1a\u81ea\u52a8\u4fee\u6539\u8def\u7531\u5668\u3002\u5982\u679c\u4fdd\u7559\u8bb0\u5f55\uff0c\u90e8\u5206\u529f\u80fd\u5c06\u65e0\u6cd5\u7ee7\u7eed\uff1b\u626b\u63cf\u4ecd\u53ef\u7ee7\u7eed\u3002\n\n' + str(record.path))
        dialog = ConfirmDialog('\u6062\u590d\u8bb0\u5f55\u65e0\u6548', note, self.page,
                               yes_text='\u5907\u4efd\u5e76\u79fb\u51fa', no_text='\u4fdd\u7559')
        self._dialog = dialog
        def complete(result):
            self._finished()
            if result != ConfirmDialog.Accepted:
                return
            ssh = backend.ssh_client
            if not ssh.connected or getattr(ssh, '_client', None) is not connection or ssh.host_fingerprint != record.fingerprint:
                return
            self._worker = BackupWorker(backend.password_recovery_store, record)
            self._worker.signals.finished.connect(self._backup_finished)
            QThreadPool.globalInstance().start(self._worker)
        dialog.finished.connect(complete)
        dialog.open()

    def _backup_finished(self, success):
        self._worker = None
        message = ('\u8bb0\u5f55\u5df2\u5907\u4efd\u5e76\u79fb\u51fa\u3002\u8bf7\u91cd\u65b0\u767b\u5f55\u8def\u7531\u5668\uff0c\u8ba9\u8f6f\u4ef6\u518d\u6b21\u68c0\u67e5\u3002' if success else
                   '\u5907\u4efd\u79fb\u51fa\u672a\u5b8c\u6210\uff0c\u539f\u8bb0\u5f55\u5df2\u4fdd\u7559\u3002\u8bf7\u68c0\u67e5\u76ee\u5f55\u6743\u9650\u540e\u91cd\u8bd5\u3002')
        dialog = MessageDialog('\u6062\u590d\u8bb0\u5f55\u5904\u7406', message, self.page)
        self._dialog = dialog
        dialog.finished.connect(self._finished)
        dialog.open()

    def _finished(self, *_):
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.deleteLater()
        self.requested.emit()
