"""GUI-thread renewal broker; OS work runs in separately owned workers."""
from PySide6.QtCore import QObject
from threading import Event
from uuid import uuid4
import time
from .lan_client_recovery import RenewalGate


class LanRecoveryService(QObject):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.offers={};self.standalone=set();self.adapter_jobs={}
    def create_offer(self,plan,*,ready=True):
        """Post-task recovery stays available while its result dialog is open."""
        if len(self.offers)>=32:raise RuntimeError("Too many network recovery offers")
        key='network-recovery-'+uuid4().hex
        gate=RenewalGate(plan);gate.start(time.monotonic()+600)
        if ready:gate.arm()
        self.offers[key]=(gate,{})
        self.standalone.add(key)
        return key
    def set_ready(self,key,ready):
        item=self.offers.get(key)
        if key not in self.standalone or item is None:return
        gate=item[0]
        with gate.lock:
            if gate.busy:return
            gate.armed=bool(ready)
            if ready:gate.deadline=time.monotonic()+600

    def payload(self,key):
        item=self.offers.get(key)
        return dict(item[0].payload(),busy_adapters=list(self.adapter_jobs)) if item else {}
    def observe(self,key,value):
        payload=value.get('network_recovery')
        if isinstance(payload,dict) and isinstance(payload.get('control'),RenewalGate):
            self.offers[key]=(payload['control'],dict(value))
        elif key in self.offers:self.offers[key]=(self.offers[key][0],dict(value))
        if key not in self.standalone and key in self.offers and key in self.owner._openwrt_workers:
            gate=self.offers[key][0]
            if not any(row['id'] in self.adapter_jobs for row in gate.adapters):
                identity=gate.take_automatic()
                if identity is not None:self.request(key,identity,automatic=True)
    def release(self,key):
        self.standalone.discard(key)
        item=self.offers.pop(key,None)
        if item:item[0].close()
    def _notify_adapter(self,identity):
        for key,(gate,_) in tuple(self.offers.items()):
            current=self.offers.get(key)
            if current is None or current[0] is not gate:continue
            if any(row['id']==identity for row in gate.adapters):
                self.owner.configurationProgressChanged.emit(key,dict(current[1],network_recovery=self.payload(key)))

    def request(self,key,identity,*,automatic=False):
        item=self.offers.get(key)
        if getattr(self.owner,"_shutting_down",False):return False
        if item is None or (key not in self.standalone and key not in self.owner._openwrt_workers):return False
        identity=str(identity)
        gate,value=item
        if identity in self.adapter_jobs:
            self.owner.configurationProgressChanged.emit(key,dict(value,network_recovery=self.payload(key)))
            return False
        claimed=gate.claim(identity)
        if claimed is None:
            self.owner.configurationProgressChanged.emit(key,dict(value,network_recovery=self.payload(key)))
            return False
        adapter,timeout=claimed
        token=uuid4().hex
        self.adapter_jobs[identity]=token
        def finish(message,current=None):
            if self.adapter_jobs.get(identity)!=token:return
            self.adapter_jobs.pop(identity)
            gate.finish_renewal(message,current)
            self._notify_adapter(identity)
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
            addresses=', '.join(result.get('addresses',[])) if isinstance(result,dict) else ''
            message='\u5df2\u91cd\u65b0\u83b7\u53d6\u7f51\u5361\u5730\u5740\u3002'
            if addresses:message+='\u5f53\u524d\u5730\u5740\uff1a'+addresses+'\u3002'
            message+=('\u8bf7\u7528\u8def\u7531\u5668\u5f53\u524d\u5730\u5740\u767b\u5f55\u3002' if key in self.standalone else
                      '\u6b63\u5728\u5c1d\u8bd5\u8fde\u63a5\u8def\u7531\u5668\u3002')
            finish(message,result)
        def failed(message,detail):
            prefix='\u81ea\u52a8\u91cd\u65b0\u83b7\u53d6\u5730\u5740\u5931\u8d25\uff1a' if automatic else '\u91cd\u65b0\u83b7\u53d6\u672a\u5b8c\u6210\uff1a'
            retry='\n\u8bf7\u5728\u5269\u4f59\u65f6\u95f4\u5185\u70b9\u51fb\u201c\u91cd\u65b0\u83b7\u53d6\u7f51\u7edc\u5730\u5740\u201d\u91cd\u8bd5\uff1bWi-Fi \u8bf7\u5148\u786e\u8ba4\u5df2\u8fde\u56de\u8be5\u8def\u7531\u5668\u3002' if gate.payload().get('can_renew') else '\n\u5269\u4f59\u65f6\u95f4\u4e0d\u8db3\uff0c\u8bf7\u7b49\u5f85\u6062\u590d\u7ed3\u679c\u3002'
            finish(prefix+message+retry)
        self._notify_adapter(identity)
        from .task_completion import start_capture_task
        try:
            started=start_capture_task(self.owner,'lan-renew-'+token,operation,Event(),completed,failed)
            if started is False:
                failed('\u540e\u53f0\u7f51\u5361\u4efb\u52a1\u672a\u80fd\u542f\u52a8\uff0c\u8bf7\u91cd\u8bd5\u3002','')
                return False
        except Exception as exc:
            failed(str(exc),'')
            return False
        self.owner.activityChanged.emit()
        return True
