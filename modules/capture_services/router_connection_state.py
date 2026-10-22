"""统一底层 SSH 断连事件与常驻登录快照，不轮询。"""
import weakref
from dataclasses import replace
from PySide6.QtCore import QObject,Signal,Slot,Qt
from shiboken6 import isValid
from .openwrt.models import OpenWrtConnectionState

class RouterConnectionState(QObject):
    """传输线程结束后回到主线程发布断连；旧连接不能覆盖新登录。"""
    connectionLost=Signal(object)
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner
        self.ssh=owner.openwrt_backend.ssh_client
        self.connectionLost.connect(self._lost,Qt.QueuedConnection)
        owner.openwrtSnapshotChanged.connect(self.reconcile,Qt.QueuedConnection)
        reference=weakref.ref(self)
        def callback(client):
            observer=reference()
            if observer is not None and isValid(observer):observer.connectionLost.emit(client)
        self.ssh.connection_lost_callback=callback

    @Slot(object)
    def _lost(self,client):
        if self.ssh._client is client or self.ssh._client is None:self.reconcile()

    @Slot(object)
    def reconcile(self,snapshot=None):
        current=self.owner.openwrt_backend.snapshot
        if current.connection_state==OpenWrtConnectionState.CONNECTED and not self.ssh.connected:
            note='路由器连接已断开，请到“路由器登录”重新登录。'
            self.owner.openwrt_backend.snapshot=replace(current,
                connection_state=OpenWrtConnectionState.DISCONNECTED,
                connection_detail=note,host_fingerprint='',last_error=note)
            self.owner.openwrtSnapshotChanged.emit(self.owner.openwrt_backend.snapshot)
            self.owner.settingsMessageGenerated.emit(note,'error')
