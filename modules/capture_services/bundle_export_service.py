"""Resident, cancellable local export tasks shared by rebuilt offline cards."""
from functools import partial
from threading import Event
from uuid import uuid4
from PySide6.QtCore import QObject,Signal
from .task_completion import start_service_worker

class BundleExportService(QObject):
    changed=Signal()
    profilesReady=Signal(object)
    exported=Signal(object)
    progress=Signal(object)
    failed=Signal(str)

    def __init__(self,owner,kind):
        super().__init__(owner)
        self.owner=owner
        self.kind=kind
        self.active=False
        self.cancel_event=None

    def load(self):
        from .openwrt.bundle_export import list_export_profiles
        return self._run(partial(list_export_profiles,self.kind),self.profilesReady.emit,reading=True)

    def export(self,selection,wanted,destination):
        from .openwrt.bundle_export import export_packages
        operation=partial(export_packages,self.kind,selection,tuple(wanted),destination)
        return self._run(operation,self.exported.emit)

    def _run(self,operation,success,reading=False):
        if self.active or self.owner.is_busy or self.owner._shutting_down:
            self.failed.emit("当前有任务运行，请稍后再提取依赖。")
            return False
        self.active=True
        self.cancel_event=Event()
        self.changed.emit()
        def completed(value):
            cancelled=self.cancel_event.is_set() or self.owner._shutting_down
            self.active=False
            self.cancel_event=None
            self.changed.emit()
            if reading and cancelled:
                self.failed.emit("提取已取消。")
            else:
                success(value)
        started=start_service_worker(self.owner,"offline-export-"+uuid4().hex,operation,self.cancel_event,
                                    completed,self._failed,self.progress.emit)
        self.owner.activityChanged.emit()
        return started

    def _failed(self,summary,_detail):
        cancelled=self.cancel_event is not None and self.cancel_event.is_set()
        self.active=False
        self.cancel_event=None
        self.changed.emit()
        self.failed.emit("提取已取消。" if cancelled else summary)

    def cancel(self):
        if self.cancel_event is not None:
            self.cancel_event.set()
