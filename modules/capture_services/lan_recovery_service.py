"""GUI-thread renewal broker; OS work runs in separately owned workers."""
from PySide6.QtCore import QObject
from threading import Event
from uuid import uuid4
import time
from .lan_client_recovery import RenewalGate


class LanRecoveryService(QObject):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.offers={};self.standalone=set()
    def create_offer(self,plan):
        """Post-task recovery stays available while its result dialog is open."""
        if len(self.offers)>=32:raise RuntimeError("Too many network recovery offers")
        key='reset-recovery-'+uuid4().hex
        gate=RenewalGate(plan);gate.start(time.monotonic()+600);gate.arm()
        self.offers[key]=(gate,{})
        self.standalone.add(key)
        return key
    def payload(self,key):
        item=self.offers.get(key)
        return item[0].payload() if item else {}
    def observe(self,key,value):
        payload=value.get('network_recovery')
        if isinstance(payload,dict) and isinstance(payload.get('control'),RenewalGate):
            self.offers[key]=(payload['control'],dict(value))
        elif key in self.offers:self.offers[key]=(self.offers[key][0],dict(value))
    def release(self,key):
        self.standalone.discard(key)
        item=self.offers.pop(key,None)
        if item:item[0].close()
    def request(self,key,identity):
        item=self.offers.get(key)
        if getattr(self.owner,"_shutting_down",False):return False
        if item is None or (key not in self.standalone and key not in self.owner._openwrt_workers):return False
        gate,value=item;claimed=gate.claim(str(identity))
        if claimed is None:
            self.owner.configurationProgressChanged.emit(key,dict(value,network_recovery=gate.payload()))
            return False
        adapter,timeout=claimed
        def emit():
            current=self.offers.get(key)
            if current is None or current[0] is not gate:return
            self.owner.configurationProgressChanged.emit(key,dict(current[1],network_recovery=gate.payload()))
        def operation(cancel):
            from modules.platform_services.lan_network import renew
            from .management_diagnostics import ManagementTrace
            runner=getattr(getattr(self.owner,'openwrt_backend',None),'command_runner',None)
            trace=ManagementTrace(runner,'client_network_renew',dict(kind='network'))
            def permitted():
                return not cancel.is_set() and not getattr(self.owner,'_shutting_down',False) and gate.permit()
            def perform(event,report):
                if not permitted():raise InterruptedError('网卡地址更新已取消。')
                return renew(adapter,timeout,permitted)
            return trace.execute(perform,cancel,lambda *_:None)
        def completed(result):
            if key in self.standalone:
                gate.finish_renewal('已重新获取网卡地址。请使用恢复后的默认地址登录路由器。');emit();return
            gate.finish_renewal('\u5df2\u91cd\u65b0\u83b7\u53d6\u7f51\u5361\u914d\u7f6e\uff0c\u6b63\u5728\u5c1d\u8bd5\u8fde\u63a5\u8def\u7531\u5668\u3002');emit()
        def failed(message,detail):
            gate.finish_renewal('\u91cd\u65b0\u83b7\u53d6\u672a\u5b8c\u6210\uff1a'+message);emit()
        emit()
        from .task_completion import start_capture_task
        try:start_capture_task(self.owner,'lan-renew-'+uuid4().hex,operation,Event(),completed,failed)
        except Exception as exc:failed(str(exc),'')
        self.owner.activityChanged.emit()
        return True
