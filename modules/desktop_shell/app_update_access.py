"""Nonblocking secure testing-code persistence for the Linux update center."""
from PySide6.QtCore import QObject,Signal
from modules.platform_services.runtime import is_linux
from modules.platform_services.app_update_access import TARGET,normalize_code


class UpdateAccess(QObject):
    activityChanged=Signal()
    def __init__(self,parent):
        super().__init__(parent);self.loaded=not is_linux();self.code='';self.controller=None
        if is_linux():
            from modules.platform_services.credential_controller import CredentialController
            self.controller=CredentialController(self)
            self.controller.activityChanged.connect(self.activityChanged)
    @property
    def active(self):return self.controller is not None and self.controller.active
    def load(self,callback):
        def done(value,error):
            if error is None:
                try:self.code=normalize_code(value);self.loaded=True
                except (ValueError,AttributeError) as exc:error=exc
            callback(error)
        self.controller.submit('read',TARGET,'','',self,done)
    def save(self,value,callback):
        value=normalize_code(value)
        def done(result,error):
            if error is None:self.code=value;self.loaded=True
            callback(error)
        self.controller.submit('write' if value else 'delete',TARGET,'RouterManager tester',value,self,done)
    def stop(self):
        if self.controller is not None:self.controller.shutdown()
