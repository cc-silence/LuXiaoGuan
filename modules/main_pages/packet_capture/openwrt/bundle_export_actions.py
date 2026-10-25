"""Nonblocking local-package export dialog and shared task progress ownership."""
from PySide6.QtCore import QObject,Qt
from PySide6.QtWidgets import QFileDialog
from modules.common_widgets.common_dialogs import MessageDialog
from .router_task_progress import RouterTaskProgressDialog

class BundleExportActions(QObject):
    def __init__(self,parent,owner,kind):
        super().__init__(parent)
        self.parent=parent
        self.owner=owner
        self.kind=kind
        self.service=None
        self.dialog=None
        self.progress=None
        self.disposed=False

    def open(self):
        if self.disposed:
            return
        if self.dialog is not None:
            self.dialog.show();self.dialog.raise_();self.dialog.activateWindow()
            return
        if self.service is None:
            name=self.kind+"_bundle_export"
            self.service=getattr(self.owner,name,None)
            if self.service is None:
                from modules.capture_services.bundle_export_service import BundleExportService
                self.service=BundleExportService(self.owner,self.kind)
                setattr(self.owner,name,self.service)
            self.service.profilesReady.connect(self._profiles)
            self.service.exported.connect(self._exported)
            self.service.failed.connect(self._failed)
            self.service.progress.connect(self._progress)
        if self.service.active:
            return
        self._start_progress("读取本地依赖清单")
        self.service.load()

    def _start_progress(self,title):
        if self.progress is not None:
            self.progress.finish()
        self.progress=RouterTaskProgressDialog(title,self.service.cancel,self.parent)
        self.progress.show()

    def _profiles(self,value):
        self._finish_progress()
        if self.disposed:
            return
        from .bundle_export_dialog import BundleExportDialog
        self.dialog=BundleExportDialog(value,self.parent)
        self.dialog.exportRequested.connect(self._choose_destination)
        self.dialog.finished.connect(self._closed)
        self.dialog.open()

    def _choose_destination(self,selection,wanted):
        destination=QFileDialog.getExistingDirectory(self.dialog,"选择导出位置")
        if not destination or self.disposed:
            return
        self.dialog.accept()
        self._start_progress("提取离线依赖")
        self.service.export(selection,wanted,destination)

    def _closed(self,*_):
        if self.dialog is not None:
            dialog,self.dialog=self.dialog,None
            dialog.deleteLater()

    def _progress(self,value):
        if self.progress is not None:
            self.progress.update_progress(value)

    def _finish_progress(self):
        if self.progress is not None:
            dialog,self.progress=self.progress,None
            dialog.finish()

    def _failed(self,message):
        if self.disposed:
            return
        if self.progress is not None:
            dialog,self.progress=self.progress,None
            dialog.finish(False,message)

    def _exported(self,value):
        self._finish_progress()
        if self.disposed:
            return
        message="已提取 "+str(value["count"])+" 个软件包（含配套依赖）。\n保存位置："+value["path"]
        dialog=MessageDialog("提取完成",message,self.parent)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.open()

    def dispose(self):
        self.disposed=True
        if self.service is not None:
            self.service.cancel()
            for signal,slot in ((self.service.profilesReady,self._profiles),(self.service.exported,self._exported),
                                (self.service.failed,self._failed),(self.service.progress,self._progress)):
                signal.disconnect(slot)
        self._finish_progress()
        if self.dialog is not None:
            self.dialog.reject()
