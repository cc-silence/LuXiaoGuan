"""常驻路由器管理队列，复用现有 worker 退出管理。"""
from collections import deque
from threading import Event
from uuid import uuid4
from PySide6.QtCore import QObject, Qt, Signal, Slot
from .openwrt.router_management import RouterManagement
from modules.platform_services.file_metadata import local_entry_hidden
from modules.platform_services.local_file_operations import recycle_local_entry, create_local_directory


class RouterManagementService(QObject):
    """主线程持有队列，写操作占用公共任务槽。"""
    completed = Signal(str, object)
    failed = Signal(str, str)
    progress = Signal(str, object)
    status = Signal(str, str)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        backend = owner.openwrt_backend
        self.manager = RouterManagement(backend.ssh_client, backend.command_runner)
        from .openwrt.compatible_tool import CompatibleToolInstaller
        self.tools = CompatibleToolInstaller(backend)
        self.queue = deque()
        self.current = None
        owner.activityChanged.connect(self._drain, Qt.QueuedConnection)
        owner.openwrtSnapshotChanged.connect(self._connection_changed)

    def submit(self, operation, **arguments):
        """绑定连接实例和设备指纹，队列上限 32 项。"""
        action = operation
        if action not in {'upload_directory','download_directory','file_batch','tool_reset','tool_check','tool_install','local_list','local_delete','local_mkdir','list','upload','download','mkdir','delete','rename','chmod'}:
            raise ValueError('不支持这种管理操作。')
        if action == 'file_batch':
            from copy import deepcopy
            jobs = arguments.get('jobs')
            if not isinstance(jobs, (list, tuple)) or not 1 <= len(jobs) <= 10000:
                raise ValueError('请选择 1 至 10000 个文件。')
            for job in jobs:
                if job.get('operation') not in {'upload_directory','download_directory','upload','download','delete','local_delete','local_move','remote_move'}:
                    raise ValueError('不支持这种批量操作。')
                if not isinstance(job.get('size'), int) or job['size'] < 0:
                    raise ValueError('文件大小无效，请刷新。')
            arguments = deepcopy(arguments)
        ssh = self.manager.ssh
        if self.owner._shutting_down or not ssh.connected or not ssh.host_fingerprint:
            raise RuntimeError('请先登录路由器。')
        if len(self.queue) >= 32:
            raise RuntimeError('等待队列已满。')
        key = uuid4().hex
        self.queue.append((key, action, arguments, ssh.host_fingerprint, ssh._client, Event()))
        self.status.emit(key, '已排队')
        self._drain()
        return key

    @Slot()
    def _drain(self):
        if self.current or not self.queue or self.owner._shutting_down:
            return
        item = self.queue[0]
        if self.owner.is_busy and item[1] not in {'local_list','list'}:
            item = next((entry for entry in self.queue if entry[1] in {'local_list','list'}), None)
            if item is None:
                return
        key, action, args, fingerprint, connection, cancel = item
        ssh = self.manager.ssh
        if not ssh.connected or ssh.host_fingerprint != fingerprint or ssh._client is not connection:
            self.queue.remove(item)
            self.failed.emit(key, '连接已变化，已取消旧操作。')
            self._drain()
            return
        readonly = action in {'local_list','list'}
        if not readonly and self.owner.is_busy:
            return
        if readonly and self.owner._shutting_down:
            return
        writer_cancel = None
        if not readonly:
            writer_cancel = self.owner.task_controller.begin('router_management', key)
            if writer_cancel is None:
                return
            cancel = writer_cancel
            item = (key, action, args, fingerprint, connection, cancel)
        self.queue.remove(next(entry for entry in self.queue if entry[0] == key))
        self.current = item
        self.status.emit(key, '执行中')
        def operation(event, report):
            if ssh._client is not connection or ssh.host_fingerprint != fingerprint:
                raise RuntimeError('连接已变化，请重新登录并提交操作。')
            if action == 'file_batch':
                from .router_file_batch import run_file_batch
                value = run_file_batch(self.manager, args['jobs'], event, report,
                    lambda: ssh.connected and ssh._client is connection and ssh.host_fingerprint == fingerprint)
            elif action == 'tool_reset':
                value = self.tools.reset(event)
            elif action == 'tool_check':
                value = self.tools.check(event)
            elif action == 'tool_install':
                value = self.tools.install(event)
            elif action == 'local_list':
                from pathlib import Path
                import stat
                folder = Path(args['path']).resolve(strict=True)
                entries = []
                for child in folder.iterdir():
                    if event.is_set():
                        raise InterruptedError('Cancelled')
                    if len(entries) >= 10000:
                        raise ValueError('目录超过 10000 项，请打开子目录。')
                    try:
                        info = child.lstat()
                        entries.append(dict(name=child.name, path=str(child), size=info.st_size, mode=info.st_mode, modified=info.st_mtime, identity=self.manager.identity(info), hidden=local_entry_hidden(child.name,info)))
                    except OSError:
                        continue
                value = dict(path=str(folder), entries=entries)
            elif action == 'local_mkdir':
                value = create_local_directory(cancel=event, **args)
            elif action == 'local_delete':
                value = recycle_local_entry(cancel=event, **args)
            elif action == 'list':
                value = self.manager.list_directory(args['path'], event)
            elif action in {'upload_directory','download_directory'}:
                from .openwrt.router_directory_transfer import transfer_directory
                value = transfer_directory(self.manager, action=='upload_directory', cancel=event, progress=report, **args)
            elif action in {'upload','download'}:
                value = self.manager.transfer_file(action=='upload', cancel=event, progress=report, **args)
            elif action in {'mkdir','delete','rename','chmod'}:
                value = self.manager.mutate(action, cancel=event, **args)
            return value
        self.owner._start_openwrt_worker(key, operation, cancel, self._success,
            failure_slot=self._failure, progress_slot=self._progress)
        self.owner.activityChanged.emit()

    @Slot(object)
    def _success(self, value):
        key = self.current[0]
        if self.current[1] in {"tool_check", "tool_install", "tool_reset"}:
            self.owner.settingsMessageGenerated.emit(str(value.get("note", "兼容工具检查完成")), "success")
        self._finish(bool(self.current[1] == 'file_batch' and value.get('error')))
        self.completed.emit(key, value)

    @Slot(str, str)
    def _failure(self, summary, detail):
        key = self.current[0]
        observer=getattr(self.owner,"router_connection_state",None)
        if observer is not None:observer.reconcile()
        self.owner.settingsMessageGenerated.emit(("本机文件操作未完成：" if self.current[1].startswith("local_") else "路由器管理操作未完成：")+summary, "error")
        self._finish(True)
        self.failed.emit(key, summary)

    @Slot(object)
    def _progress(self, value):
        if self.current:
            self.progress.emit(self.current[0], value)

    def _finish(self, failed):
        if self.current[1] not in {'local_list','list'}:
            self.owner.task_controller.finish(failed=failed)
        self.current = None
        # Activity is delivered only after the owning thread has released its resources.

    def cancel(self, key):
        """只取消指定窗口提交的任务。"""
        if self.current and self.current[0] == key:
            self.current[-1].set()
        for item in tuple(self.queue):
            if item[0] == key:
                self.queue.remove(item)
                self.failed.emit(key, '操作已取消。')

    @Slot(object)
    def _connection_changed(self, snapshot):
        if self.current:
            ssh = self.manager.ssh
            if not ssh.connected or ssh._client is not self.current[4]:
                self.current[-1].set()
        self._drain()

    def shutdown(self):
        """退出时取消队列和在途任务，由公共 worker 回收线程。"""
        for item in tuple(self.queue):
            self.cancel(item[0])
        if self.current:
            self.current[-1].set()
