"""Update-page export adapter; the runtime owns the existing export worker."""
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog


class UpdateLogExport(QObject):
    changed = Signal()

    def __init__(self, presenter):
        super().__init__(presenter)
        self.presenter = presenter
        self.active = False
        self.message = ""
        self._service = None

    def start(self, owner):
        from modules.config_manager import user_downloads_dir
        from modules.log_services import build_export_filename
        from modules.app_update_services.diagnostics import snapshot
        from modules.app_update_services.diagnostics_export import export_update_logs
        runtime = self.presenter.runtime
        if self.active or runtime._exiting:
            return
        service = runtime.log_export_service
        if service.active:
            self.message = "\u5df2\u6709\u65e5\u5fd7\u6b63\u5728\u5bfc\u51fa\uff0c\u8bf7\u7b49\u5f85\u5b8c\u6210\u3002"
            self.changed.emit()
            return
        picked, _ = QFileDialog.getSaveFileName(owner, "\u5bfc\u51fa\u66f4\u65b0\u65e5\u5fd7",
            str(user_downloads_dir() / build_export_filename('update')), 'ZIP (*.zip)')
        if not picked or runtime._exiting:
            return
        self._service = service
        service.completed.connect(self._completed)
        service.failed.connect(self._failed)
        service.activityChanged.connect(self._activity_changed)
        self.active = True
        self.message = "\u6b63\u5728\u5bfc\u51fa\u66f4\u65b0\u65e5\u5fd7\u2026"
        self.changed.emit()
        if not service.start(export_operation=export_update_logs, destination=picked,
                             entries=snapshot(), app_version=self.presenter.checker.current_version):
            self.message = "\u5bfc\u51fa\u670d\u52a1\u6b63\u5fd9\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002"
            self._detach()

    def _completed(self, result):
        self.message = "\u66f4\u65b0\u65e5\u5fd7\u5df2\u5bfc\u51fa\uff1a" + result['zip_path']
        self.changed.emit()

    def _failed(self, reason):
        from modules.app_update_services.diagnostics import record
        record("log_export_failed", error="export_worker_failed")
        self.message = "\u65e5\u5fd7\u5bfc\u51fa\u5931\u8d25\uff1a" + reason
        self.changed.emit()

    def _activity_changed(self):
        if self._service is not None and not self._service.active:
            self._detach()

    def _detach(self):
        service, self._service = self._service, None
        if service is not None:
            service.completed.disconnect(self._completed)
            service.failed.disconnect(self._failed)
            service.activityChanged.disconnect(self._activity_changed)
        self.active = False
        self.changed.emit()
