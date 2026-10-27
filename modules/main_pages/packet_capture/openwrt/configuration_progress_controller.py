"""One task-bound apply dialog, shared by router configuration entry points."""
from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from .router_task_progress import RouterTaskProgressDialog


class ConfigurationProgressController(QObject):
    def __init__(self, page, service):
        super().__init__(page)
        self.page=page
        self.service=service
        self.dialog=None
        self.key=None
        self._subscription=service.configurationProgressChanged.connect(self.update)

    @Slot(str, object)
    def update(self, key, value):
        if value is None:
            if key==self.key:self._finish()
            return
        if not isinstance(value,dict) or not value.get("configuration_apply"):
            return
        if self.key is not None and key!=self.key:
            return
        if self.dialog is None or not isValid(self.dialog) or not self.dialog.running:
            parent=QApplication.activeModalWidget()
            if parent is None or (parent is not self.page.window() and not self.page.window().isAncestorOf(parent)):
                parent=self.page.window()
            existing=[w for w in self.page.window().findChildren(RouterTaskProgressDialog)
                      if w.running and w.isVisible()]
            self.dialog=existing[0] if len(existing)==1 else RouterTaskProgressDialog(
                "正在应用路由器配置",self.service.cancel_openwrt_task,parent)
            self.key=key
            self._request_subscription=self.dialog.networkRenewRequested.connect(self._request_renewal)
            self.dialog.show()
        self.dialog.update_progress(value)

    def _request_renewal(self,identity):
        recovery=getattr(self.service,"lan_recovery",None)
        if recovery is not None and self.key is not None:recovery.request(self.key,identity)

    def _finish(self):
        dialog=self.dialog
        self.dialog=None;self.key=None
        if dialog is not None and isValid(dialog):
            QObject.disconnect(self._request_subscription)
            dialog.finish()

    def dispose(self):
        QObject.disconnect(self._subscription)
        self._finish()
