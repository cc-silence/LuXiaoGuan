"""Connection-bound, asynchronous readiness shared by configuration pages."""
from uuid import uuid4
from PySide6.QtCore import QObject,Signal,Qt
from .openwrt.signal_manager import SignalError

READY_NOTICE='\u4fe1\u53f7\u7ba1\u7406\u4f9d\u8d56\u5df2\u9f50\u5168\uff0c\u5df2\u901a\u8fc7\u5b9e\u9645\u68c0\u67e5\u3002'
REQUIREMENTS=(('lua','lua'),('uci','libuci-lua'),('ubus','libubus-lua'),('jsonc','luci-lib-jsonc'))


class ManagementReadiness(QObject):
    changed=Signal()
    _requested=Signal()

    def __init__(self,service):
        super().__init__(service)
        self.service=service
        self.owner=service.owner
        self.connection=self.binding()
        self.probe=None
        self.error=''
        self.pending=False
        self.checking=False
        self._announced=False
        self._last_missing=None
        self._dependency_action=None
        self.install_session=None
        self._dependency_session=None
        self._requested.connect(self._start,Qt.QueuedConnection)
        self.owner.openwrtSnapshotChanged.connect(self._connection_changed)
        self.owner.task_controller.stateChanged.connect(self._task_changed)

    def binding(self):
        ssh=self.owner.openwrt_backend.ssh_client
        return (ssh._client,getattr(ssh,'host_fingerprint','')) if ssh.connected and ssh._client is not None else None

    @property
    def missing(self):
        return tuple(package for key,package in REQUIREMENTS if self.probe is not None and not self.probe.get(key))

    @property
    def ready(self):
        return self.probe is not None and all(self.probe.get(key) for key in ('lua','uci','ubus','jsonc','root','sha256sum'))

    def watch(self,dependencies):
        dependencies.taskChanged.connect(self._dependency_changed)
        dependencies.taskFinished.connect(self._dependency_finished)

    def _dependency_changed(self,task):
        if task is not None:
            self._dependency_action=task.action
            if task.session_id!=self._dependency_session:
                self._dependency_session=task.session_id
                if task.action=='install':self._announced=False
        session=task.session_id if task is not None and task.action=='install' and task.group=='signal' else None
        if session!=self.install_session:
            from dataclasses import replace
            self.install_session=session
            self.service.snapshot=replace(self.service.snapshot,installing=session is not None)
            self.service.snapshotChanged.emit(self.service.snapshot)

    def _dependency_finished(self,*args):
        action,self._dependency_action=self._dependency_action,None
        if action in ('install','uninstall'):self.request()

    def _task_changed(self,state,*args):
        if state in ('idle','failed') and self.pending:self._requested.emit()

    def _connection_changed(self,*args):
        current=self.binding()
        if current!=self.connection:
            self.connection=current
            self.probe=None
            self.error=''
            self.pending=False
            self._announced=False
            self._last_missing=None
            self.service.dependencies_missing=False
            self.service.snapshotChanged.emit(self.service.snapshot)
            self.changed.emit()

    def request(self):
        self._connection_changed()
        if self.binding() is None or self.owner._shutting_down:return False
        if self.checking:return True
        self.pending=True
        self._requested.emit()
        return True

    def _start(self):
        if not self.pending or self.checking or self.owner._shutting_down:return
        binding=self.binding()
        if binding is None:self.pending=False;return
        if self.owner.task_controller.busy:return
        session=uuid4().hex
        self.pending=False
        self.checking=True
        event=self.owner.task_controller.begin('router_inspect',session)
        if event is None:
            self.checking=False
            self.pending=True
            return
        self.error=''
        self.changed.emit()
        manager=self.service.manager
        def operation(cancel):
            if self.binding()!=binding:raise SignalError('disconnected','Connection changed')
            return manager.probe_dependencies(cancel)
        def complete(probe=None,error=''):
            self.checking=False
            if self.binding()==binding and not event.is_set() and not self.owner._shutting_down:
                if error:
                    self.probe=None
                    self.error=error
                else:self.accept(probe)
            else:self._connection_changed()
            self.owner.task_controller.finish(failed=bool(error))
            self.changed.emit()
        from .task_completion import start_service_worker
        start_service_worker(self.owner,session,operation,event,lambda probe:complete(probe),
                             lambda summary,detail:complete(error=summary))

    def accept(self,probe):
        if not isinstance(probe,dict) or any(type(probe.get(key)) is not bool for key in ('lua','uci','ubus','jsonc','root','sha256sum')):
            self.error='\u4f9d\u8d56\u68c0\u67e5\u672a\u8fd4\u56de\u5b8c\u6574\u7ed3\u679c\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u3002'
            self.probe=None
            self.changed.emit()
            return
        self.probe=dict(probe)
        self.error=''
        self.service.dependencies_missing=bool(self.missing)
        if self.ready:
            if not self._announced:self.service._message(READY_NOTICE,'success')
            self._announced=True
            self._last_missing=None
        else:
            self._announced=False
            if self.missing and self.missing!=self._last_missing:
                self.service._message('\u7f3a\u5c11\u914d\u7f6e\u7ba1\u7406\u4f9d\u8d56\uff1a'+'\u3001'.join(self.missing)+'\u3002\u8bf7\u70b9\u51fb\u201c\u5b89\u88c5\u7f3a\u5c11\u4f9d\u8d56\u201d\u3002','warning')
            self._last_missing=self.missing
        self.service.snapshotChanged.emit(self.service.snapshot)
        self.changed.emit()
