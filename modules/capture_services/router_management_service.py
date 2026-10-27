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
    READ_ONLY = frozenset({'local_list', 'local_upload_selection', 'list', 'common_read', 'policy_read', 'policy_status'})
    completed = Signal(str, object)
    failed = Signal(str, str)
    progress = Signal(str, object)
    status = Signal(str, str)
    passwordChanged = Signal(object)
    policyStatusChanged = Signal(object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        backend = owner.openwrt_backend
        self.manager = RouterManagement(backend.ssh_client, backend.command_runner)
        from .openwrt.compatible_tool import CompatibleToolInstaller
        self.tools = CompatibleToolInstaller(backend)
        self.queue = deque()
        self.current = None
        self._diagnostic_tasks = {}
        self.policy_status = None
        self._policy_connection = None
        self._publishing_key = None
        owner.activityChanged.connect(self._drain, Qt.QueuedConnection)
        owner.openwrtSnapshotChanged.connect(self._connection_changed)

    def submit(self, operation, **arguments):
        """绑定连接实例和设备指纹，队列上限 32 项。"""
        action = operation
        if action not in {'policy_status','policy_read','policy_preview','policy_apply','maintenance_reboot','maintenance_preview','maintenance_backup','maintenance_apply','common_read','common_save','password_change','upload_directory','download_directory','file_batch','tool_reset','tool_check','tool_install','local_list','local_upload_selection','local_delete','local_mkdir','list','upload','download','mkdir','delete','rename','chmod'}:
            raise ValueError('不支持这种管理操作。')
        if (action.startswith("maintenance_") or action in {"policy_preview","policy_apply"}) and (self.current or self.queue or (self.owner.is_busy and not self._publication_continuation())):
            raise RuntimeError("请等待当前路由器任务结束后再操作。")
        if action == 'password_change':
            from .openwrt.router_password import PasswordChange
            if self.current or self.queue or self.owner.is_busy:
                raise RuntimeError("请等待当前路由器任务结束。")
            if not isinstance(arguments.get('request'), PasswordChange):
                raise ValueError("密码修改请求无效。")
        if action in {'policy_preview','policy_apply','common_read','common_save','maintenance_preview','maintenance_backup','maintenance_apply'}:
            from copy import deepcopy
            arguments = deepcopy(arguments)
        if action == "common_save":
            if type(arguments.get("apply", False)) is not bool:
                raise ValueError("Invalid application mode")
            if arguments.get("apply") and arguments.get("review", {}).get("config") != "network":
                raise ValueError("Only LAN settings support save and reboot")
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
        if action == "password_change":
            target = arguments["request"].settings
            if (target.get("host"), target.get("port"), target.get("user")) != (ssh._host, ssh._port, ssh._user):
                raise ValueError("登录目标已变化，请重新打开密码设置。")
        if len(self.queue) >= 32:
            raise RuntimeError('等待队列已满。')
        key = uuid4().hex
        from .management_diagnostics import ManagementTrace
        self._diagnostic_tasks[key] = ManagementTrace(self.manager.runner, action, arguments, len(self.queue))
        self.queue.append((key, action, arguments, ssh.host_fingerprint, ssh._client, Event()))
        self.status.emit(key, '已排队')
        self._drain()
        return key

    @Slot()
    def _drain(self):
        if self.current or not self.queue or self.owner._shutting_down:
            return
        item = self.queue[0]
        if self.owner.is_busy and item[1] not in self.READ_ONLY:
            item = next((entry for entry in self.queue if entry[1] in self.READ_ONLY), None)
            if item is None:
                return
        key, action, args, fingerprint, connection, cancel = item
        ssh = self.manager.ssh
        if not ssh.connected or ssh.host_fingerprint != fingerprint or ssh._client is not connection:
            self.queue.remove(item)
            trace=self._diagnostic_tasks.pop(key,None)
            if trace is not None:trace.cancelled("connection_changed")
            from .router_password_actions import clear_password_request
            clear_password_request(item[2])
            self.failed.emit(key, '连接已变化，已取消旧操作。')
            self._drain()
            return
        readonly = action in self.READ_ONLY
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
        reconnect_password=self.owner.openwrt_password() if action in {"common_save","policy_apply"} and hasattr(self.owner,"openwrt_password") else ""
        connection_settings=dict(args.get("connection_settings",{}),host=getattr(ssh,"_host",None),port=getattr(ssh,"_port",None),user=getattr(ssh,"_user",None)) if action in {"common_save","policy_apply"} else {}
        if connection_settings:
            authenticated=getattr(ssh,"authenticated_settings",None)
            if isinstance(authenticated,dict) and authenticated:connection_settings=dict(authenticated)
        def operation(event, report):
            if ssh._client is not connection or ssh.host_fingerprint != fingerprint:
                raise RuntimeError('连接已变化，请重新登录并提交操作。')
            if action.startswith("policy_"):
                from .openwrt.router_policy import RouterPolicyManager
                policies = RouterPolicyManager(self.manager)
                if action == "policy_status":
                    from .openwrt.router_boot_status import read_boot_status
                    value = read_boot_status(self.manager, event)
                elif action == "policy_read": value = policies.read(event)
                elif action == "policy_preview":
                    value = policies.preview(args["kind"], args["options"], event)
                    if value.get("new_address") and hasattr(self.owner,"lan_recovery"):
                        from .lan_client_recovery import attach_inventory
                        value=attach_inventory(value,ssh)
                else: value = policies.apply(args["review"], args["backup_path"], args.get("backup_expected"), event, report,
                    connection_settings=connection_settings,password=reconnect_password,fingerprint=fingerprint)
            elif action.startswith("maintenance_"):
                from .openwrt.router_backup import RouterBackupManager
                maintenance = RouterBackupManager(self.manager)
                if action == "maintenance_reboot":
                    from .openwrt.router_reboot import reboot_router
                    value = reboot_router(self.manager, event)
                elif action == "maintenance_preview":
                    value = maintenance.preview(args["kind"], args["source"], event)
                elif action == "maintenance_backup":
                    value = maintenance.export(args["destination"], args.get("expected"), event, report)
                else:
                    value = maintenance.execute(args["review"], args["backup_path"], args.get("backup_expected"), event, report)
            elif action == 'password_change':
                from .openwrt.router_password import change_password
                value = change_password(self.manager, args['request'], event)
            elif action == 'common_read':
                from .openwrt.common_settings import read_settings
                from .openwrt.connection_bound_runner import ConnectionBoundRunner
                value = read_settings(ConnectionBoundRunner(self.manager.runner, ssh, connection, fingerprint), args['config'], event)
                if args['config']=='network' and hasattr(self.owner,'lan_recovery'):
                    from .lan_client_recovery import attach_inventory
                    value=attach_inventory(value,ssh)
            elif action == 'common_save':
                from .openwrt.common_settings import save_settings
                if args.get("apply", False) or args["review"].get("config")=="system":
                    from .openwrt.checked_apply import apply_common
                    value = apply_common(self.manager, args["review"], args["values"], event, report,
                        connection_settings, reconnect_password, fingerprint)
                else:
                    from .openwrt.connection_bound_runner import ConnectionBoundRunner
                    value = save_settings(ConnectionBoundRunner(self.manager.runner, ssh, connection, fingerprint), args['review'], args['values'], event)
            elif action == 'file_batch':
                from .router_file_batch import run_file_batch
                value = run_file_batch(self.manager, args['jobs'], event, report,
                    lambda: ssh.connected and ssh._client is connection and ssh.host_fingerprint == fingerprint)
            elif action == 'tool_reset':
                value = self.tools.reset(event)
            elif action == 'tool_check':
                value = self.tools.check(event)
            elif action == 'tool_install':
                value = self.tools.install(event)
            elif action == 'local_upload_selection':
                from modules.platform_services.local_upload_selection import inspect_upload_paths
                value = inspect_upload_paths(args['paths'], event)
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
        trace=self._diagnostic_tasks[key]
        def traced_operation(event,report):
            return trace.execute(operation,event,report)
        try:
            self.owner._start_openwrt_worker(key,traced_operation,cancel,self._success,
                failure_slot=self._failure,progress_slot=self._progress)
        except Exception as exc:
            trace.failed(exc)
            self._failure("\u540e\u53f0\u4efb\u52a1\u672a\u80fd\u542f\u52a8\uff0c\u8bf7\u91cd\u8bd5\u5e76\u5bfc\u51fa\u65e5\u5fd7\u3002",type(exc).__name__)
        self.owner.activityChanged.emit()

    @Slot(object)
    def _success(self, value):
        key = self.current[0]
        if self.current[1]=="policy_apply" and value.get("session_restored"):
            ssh=self.manager.ssh
            if ssh.connected and ssh.host_fingerprint==self.current[3] and hasattr(self.owner.openwrt_backend,"snapshot"):
                from dataclasses import replace
                from .openwrt.models import OpenWrtConnectionState
                self.owner.openwrt_backend.snapshot=replace(self.owner.openwrt_backend.snapshot,connection_state=OpenWrtConnectionState.CONNECTED,connection_detail="局域网连接已恢复",host_fingerprint=ssh.host_fingerprint,last_error="")
                self.owner.openwrtSnapshotChanged.emit(self.owner.openwrt_backend.snapshot)
        if self.current[1] in {"policy_read", "policy_status"}:
            ssh = self.manager.ssh
            self.policy_status = {"boot_state": value.get("boot_state")} if ssh.connected and ssh._client is self.current[4] and ssh.host_fingerprint == self.current[3] else None
            self.policyStatusChanged.emit(self.policy_status)
        if self.current[1] == 'password_change':
            from .router_password_actions import accept_password_result
            accept_password_result(self, value)
        if self.current[1] in {"tool_check", "tool_install", "tool_reset"}:
            self.owner.settingsMessageGenerated.emit(str(value.get("note", "兼容工具检查完成")), "success")
        self._finish(bool((self.current[1] == 'file_batch' and value.get('error')) or (isinstance(value, dict) and value.get('outcome') == 'uncertain')))
        self._publishing_key = key
        try:
            self.completed.emit(key, value)
        finally:
            self._publishing_key = None

    def _publication_continuation(self):
        key = self._publishing_key
        workers = getattr(self.owner, "_openwrt_workers", {})
        if not key or set(workers) != {key} or self.owner.task_controller.busy:
            return False
        return not workers[key][0].isRunning()

    @Slot(str, str)
    def _failure(self, summary, detail):
        key = self.current[0]
        trace=self._diagnostic_tasks.get(key)
        if trace is not None and not trace.terminal:trace.failed(RuntimeError("worker_failure"))
        if self.current[1] in {"policy_read", "policy_status"}:
            self.policy_status = None
            self.policyStatusChanged.emit(None)
        observer=getattr(self.owner,"router_connection_state",None)
        if observer is not None:observer.reconcile()
        # Common-settings failures are presented by the page owning the request key.
        action = self.current[1]
        if action not in {"password_change", "common_read", "common_save", "policy_read", "policy_preview", "policy_apply"} and not action.startswith("maintenance_"):
            self.owner.settingsMessageGenerated.emit(("本机文件操作未完成：" if self.current[1].startswith("local_") else "路由器管理操作未完成：")+summary, "error")
        self._finish(True)
        self.failed.emit(key, summary)

    @Slot(object)
    def _progress(self, value):
        if self.current:
            self.progress.emit(self.current[0], value)

    def _finish(self, failed):
        self._diagnostic_tasks.pop(self.current[0],None)
        if self.current[1] not in self.READ_ONLY:
            self.owner.task_controller.finish(failed=failed)
        from .router_password_actions import clear_password_request
        clear_password_request(self.current[2])
        self.current = None
        # Activity is delivered only after the owning thread has released its resources.

    def cancel(self, key):
        """只取消指定窗口提交的任务。"""
        if self.current and self.current[0] == key:
            self.current[-1].set()
        for item in tuple(self.queue):
            if item[0] == key:
                self.queue.remove(item)
                trace=self._diagnostic_tasks.pop(key,None)
                if trace is not None:trace.cancelled()
                from .router_password_actions import clear_password_request
                clear_password_request(item[2])
                self.failed.emit(key, '操作已取消。')

    @Slot(object)
    def _connection_changed(self, snapshot):
        ssh = self.manager.ssh
        connection = ssh._client if ssh.connected else None
        if connection is not self._policy_connection:
            self._policy_connection = connection
            self.policy_status = None
            self.policyStatusChanged.emit(None)
            if connection is not None and not self.owner._shutting_down:
                try: self.submit("policy_status")
                except RuntimeError: pass
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
