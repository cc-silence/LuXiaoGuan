"""路由器依赖分组任务；复用常驻 worker、互斥和退出回收。"""
from collections import deque
from dataclasses import dataclass, replace
from threading import Event
from uuid import uuid4
from PySide6.QtCore import QObject, Signal
from .openwrt.dependency_manager import OpenWrtDependencyManager
from .openwrt.common_tool_dependencies import TOOL_BY_KEY, TOOL_KEYS, check_tools
from .openwrt.models import CaptureProgress, CaptureTaskType, DependencyInfo, DependencyState
from .openwrt.signal_package_feed import default_cache_dir


@dataclass(frozen=True)
class DependencyTask:
    """页面可订阅的任务状态，不包含凭据或远端连接对象。"""
    session_id: str
    group: str
    items: tuple
    action: str
    message: str


class OpenWrtDependencyService(QObject):
    """任务绑定原连接；页面只显示状态，不拥有安装线程。"""
    taskChanged=Signal(object)
    taskFinished=Signal(str,bool,str)
    requestRejected=Signal(str)

    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner
        self.manager=OpenWrtDependencyManager(owner.openwrt_backend.command_runner)
        self._active=False
        self.task=None
        self._binding=None
        self._diagnostics=deque(maxlen=32)

    @property
    def active(self):
        return bool(self._active)

    @staticmethod
    def installed_packages(dependencies, *, include_sftp=True):
        """确认清单按分组筛选，仅列具名工具包。"""
        names=[]
        for item in dependencies or ():
            if item.state.value!="ready" or item.key in TOOL_KEYS or item.key=="terminal" or item.key=="sftp" and not include_sftp:continue
            packages=("openssh-sftp-server",) if item.key=="sftp" else OpenWrtDependencyManager.FEATURE_PACKAGES.get(item.key,())
            for package in packages:
                if package not in names:names.append(package)
        return names

    def install_async(self,enabled_features,*,sftp_only=False,scope="all"):
        """保留旧调用兼容；抓包入口显式使用 capture，文件传输使用 sftp_only。"""
        return self._start("install","common" if sftp_only else scope,enabled_features)

    def uninstall_async(self,enabled_features,packages=None,*,scope="all"):
        return self._start("uninstall",scope,enabled_features,packages)

    def diagnostic_references(self):
        return tuple(self._diagnostics)

    @staticmethod
    def _save_diagnostic(session,text):
        from .openwrt.workspace import local_session_root,CaptureWorkspaceFactory
        from .local_cache_cleanup import register_session,acknowledge_session
        workspace=CaptureWorkspaceFactory.create(session,local_session_root(),"/tmp/winbox-capture")
        workspace.local_dir.mkdir(exist_ok=False)
        register_session(workspace)
        try:
            path=workspace.local_dir/"package-manager.log"
            with path.open("x",encoding="utf-8") as stream:stream.write(text)
            return str(path)
        finally:acknowledge_session(workspace,True)

    def check_common_async(self):
        """重新检查仅做有界只读探测，不安装包。"""
        return self._start("check","common",{})

    def check_terminal_async(self):
        return self._start('check','terminal',{})

    def check_tool_async(self, key):
        """按目录中的常用工具项只读复测。"""
        if key not in TOOL_KEYS:raise ValueError("未知常用工具。")
        return self._start('check', key, {})

    def cancel(self,session_id):
        """只取消所属任务，旧页面不能取消后来启动的任务。"""
        if self.task and self.task.session_id==session_id:
            return self.owner.task_controller.cancel()
        return False

    def _same_connection(self,binding):
        ssh=self.owner.openwrt_backend.ssh_client
        return ssh.connected and ssh._client is binding[0] and ssh.host_fingerprint==binding[1]

    def _reject(self, reason, action="", group=""):
        from .workflow_diagnostics import record_workflow
        record_workflow(self.owner, "dependency_request", "blocked", reason=reason, stage=action, kind=group)
        self.owner.settingsMessageGenerated.emit(reason, "warning")
        self.requestRejected.emit(reason)
        return False

    def ensure_available(self, action="", group=""):
        reject = lambda reason: self._reject(reason, action, group)
        if self.owner._shutting_down:
            return reject("\u8f6f\u4ef6\u6b63\u5728\u9000\u51fa\uff0c\u4f9d\u8d56\u4efb\u52a1\u672a\u5f00\u59cb\u3002")
        if self._active or self.owner.task_controller.busy or self.owner.is_busy:
            return reject("\u5df2\u6709\u4efb\u52a1\u8fd0\u884c\uff0c\u8bf7\u7b49\u5f85\u5b8c\u6210\u6216\u505c\u6b62\u540e\u518d\u64cd\u4f5c\u3002")
        backend = self.owner.openwrt_backend
        if backend.snapshot.connection_state.value != "connected" or not backend.ssh_client.connected:
            return reject("\u8def\u7531\u5668\u672a\u8fde\u63a5\uff0c\u8bf7\u5148\u767b\u5f55\u540e\u518d\u64cd\u4f5c\u3002")
        if not backend.ssh_client.host_fingerprint:
            return reject("\u65e0\u6cd5\u786e\u8ba4\u5f53\u524d\u8bbe\u5907\u8eab\u4efd\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u540e\u518d\u64cd\u4f5c\u3002")
        return True

    def _start(self,action,group,enabled_features,packages=None):
        if group not in {"all","capture","common","terminal"} | TOOL_KEYS:raise ValueError("未知依赖分组。")
        if group in TOOL_KEYS and action=="uninstall":
            if not TOOL_BY_KEY[group].removable:raise ValueError('信号管理组件与系统共用，不提供一键卸载。')
            allowed=TOOL_BY_KEY[group].package_names
            if packages is not None and tuple(packages)!=allowed:raise ValueError("常用工具项只能卸载自己的软件包。")
            packages=allowed
        if group=="capture" and packages and set(packages).intersection(package for tool in TOOL_BY_KEY.values() for package in tool.package_names):
            raise ValueError("常用工具不能从抓包分组卸载。")
        if group=="terminal" and action=="uninstall":
            if packages is not None and tuple(packages)!=("tmux",):raise ValueError("Terminal scope only removes tmux.")
            packages=("tmux",)
        if group=="capture" and packages and "tmux" in packages:raise ValueError("Capture scope cannot uninstall tmux.")
        if group=="common" and action=="uninstall":
            if packages is not None and tuple(packages)!=("openssh-sftp-server",):
                raise ValueError("常用工具分组只能卸载文件传输支持。")
            packages=("openssh-sftp-server",)
        if not self.ensure_available(action, group):return False
        backend=self.owner.openwrt_backend
        ssh=backend.ssh_client
        binding=(ssh._client,ssh.host_fingerprint)
        features={group:True} if group in TOOL_KEYS else {"terminal":True} if group=="terminal" else {} if group=="common" else {k:v for k,v in dict(enabled_features or {}).items() if k!="terminal" and k not in TOOL_KEYS}
        items=("sftp",) if group=="common" else tuple(features)
        session=uuid4().hex
        kind={"install":CaptureTaskType.DEPENDENCY_INSTALL,"uninstall":CaptureTaskType.DEPENDENCY_UNINSTALL,"check":CaptureTaskType.DEPENDENCY_CHECK}[action]
        event=self.owner.task_controller.begin(kind.value,session)
        if event is None:return self._reject("\u4efb\u52a1\u901a\u9053\u6b63\u5728\u4ea4\u63a5\uff0c\u8bf7\u7b49\u5f85\u5f53\u524d\u4efb\u52a1\u7ed3\u675f\u540e\u91cd\u8bd5\u3002", action, group)
        from .workflow_diagnostics import record_workflow
        record_workflow(self.owner, "dependency_request", "started", stage=action, kind=group)
        self._active=True
        self._binding=binding
        label="终端会话保留支持" if group=="terminal" else "文件传输支持" if group=="common" else "抓包依赖" if group=="capture" else "路由器依赖"
        if group in TOOL_KEYS:label=TOOL_BY_KEY[group].title
        managed=group in {"common","terminal"} or group in TOOL_KEYS
        verb={"install":"安装","uninstall":"卸载","check":"检查"}[action]
        self.task=DependencyTask(session,group,items,action,"正在"+verb+label+"…")
        self.taskChanged.emit(self.task)
        device=backend.snapshot.device
        removal_packages=set(packages if packages is not None else
            self.installed_packages(backend.snapshot.dependencies,include_sftp=group in {"all","common"}))
        backend.snapshot=replace(backend.snapshot,progress=CaptureProgress(self.task.message,0,0,"",True))
        if group=="common":self._merge((DependencyInfo("sftp","文件传输",DependencyState.CHECKING),),group,binding)
        elif group=="terminal":self._merge((DependencyInfo("terminal","终端会话保留",DependencyState.CHECKING),),group,binding)
        elif group in TOOL_KEYS:self._merge((DependencyInfo(group,label,DependencyState.CHECKING),),group,binding)
        self.owner.openwrtSnapshotChanged.emit(backend.snapshot)

        def operation(cancel,message):
            if not self._same_connection(binding):raise RuntimeError("连接已变化，请重新登录。")
            error=""
            diagnostic_path=""
            checked=()
            try:
                if action=="check" and group in TOOL_KEYS:checked=check_tools(self.manager.command_runner,TOOL_KEYS,cancel)
                elif action=="check":checked=(self.manager.check_terminal(cancel),) if group=="terminal" else (self.manager.check_sftp(cancel,fresh=True),)
                elif action=="install":
                    checked=self.manager.install_all(features,device.sftp_available,device,
                        default_cache_dir(),cancel,message,include_sftp=group in {"all","common"})
                else:
                    checked=self.manager.uninstall_packages(features,device.sftp_available,
                        cancel,message,packages,include_sftp=group in {"all","common"})
                if group in TOOL_KEYS and action!="check":
                    checked=check_tools(self.manager.command_runner,TOOL_KEYS,cancel)
            except Exception as exc:
                error=str(exc) or type(exc).__name__
                diagnostic=getattr(exc,"package_diagnostic","")
                if diagnostic:
                    try:diagnostic_path=self._save_diagnostic(session,diagnostic)
                    except (OSError,ValueError):message("\u8be6\u7ec6\u8bca\u65ad\u6587\u4ef6\u672a\u80fd\u4fdd\u5b58\uff0c\u5df2\u4fdd\u7559\u539f\u5b89\u88c5\u9519\u8bef\uff1b\u8bf7\u68c0\u67e5\u672c\u673a\u76ee\u5f55\u4e0e\u6743\u9650\u3002","warning")
                # 取消安装后允许一次只读复测；不继续安装、不忽略安装首错。
                if managed and self._same_connection(binding) and not self.owner._shutting_down:
                    try:
                        if group in TOOL_KEYS:checked=check_tools(self.manager.command_runner,TOOL_KEYS,Event())
                        else:checked=(self.manager.check_terminal(Event()),) if group=="terminal" else (self.manager.check_sftp(Event(),fresh=True),)
                    except Exception as probe_error:
                        checked=(DependencyInfo(items[0],label,DependencyState.UNKNOWN,detail=str(probe_error)),)
            if not self._same_connection(binding):
                return dict(checked=(),error=(error+"；" if error else "")+"连接已断开或变化，任务已停止。已安装部分需重新登录后核对。",diagnostic_path=diagnostic_path)
            if group=="terminal":
                checked=tuple(item for item in checked if item.key=="terminal")
                if not checked:error=error or "Terminal dependency check returned no result."
            if group=="common":
                checked=tuple(item for item in checked if item.key=="sftp")
                if not checked:error=error or "未获取文件传输检测结果，请重新检查。"
            if group in TOOL_KEYS and not any(item.key==group for item in checked):
                error=error or "未获取本项检测结果，请重新检查。"
            verification=tuple(item for item in checked if item.key!="converter"
                and (group not in TOOL_KEYS or item.key==group))
            if action=="uninstall":
                verification=tuple(item for item in verification
                    if removal_packages.intersection(("openssh-sftp-server",) if item.key=="sftp"
                        else self.manager.FEATURE_PACKAGES.get(item.key,())))
            expected=DependencyState.MISSING if action=="uninstall" else DependencyState.READY
            if not error and any(item.state not in {expected,DependencyState.DISABLED} for item in verification):
                error=("\u5378\u8f7d\u540e\u5de5\u5177\u4ecd\u53ef\u7528\u6216\u72b6\u6001\u65e0\u6cd5\u786e\u8ba4\uff0c\u8bf7\u67e5\u770b\u8be6\u60c5\u5e76\u91cd\u65b0\u68c0\u67e5\u3002"
                       if action=="uninstall" else "\u68c0\u6d4b\u5c1a\u672a\u901a\u8fc7\uff0c\u8bf7\u67e5\u770b\u8be6\u60c5\u5e76\u91cd\u65b0\u68c0\u67e5\u3002")
            return dict(checked=tuple(checked),error=error,diagnostic_path=diagnostic_path)

        try:
            self.owner._start_openwrt_worker(session,operation,event,
                lambda result:self._complete(session,result),
                lambda summary,detail:self._complete(session,dict(checked=(),error=summary)),
                None,self._emit_settings_message)
        except Exception as exc:
            self._complete(session,dict(checked=(),error="无法启动依赖任务："+str(exc)))
            return False
        return True

    def _merge(self,checked,group,binding):
        """只替换本组结果；换设备或断连后不发布旧结果。"""
        if not self._same_connection(binding):return
        backend=self.owner.openwrt_backend
        checked=tuple(checked)
        if group=="common":checked=tuple(item for item in checked if item.key=="sftp")
        elif group=="terminal":checked=tuple(item for item in checked if item.key=="terminal")
        elif group in TOOL_KEYS:checked=tuple(item for item in checked if item.key in TOOL_KEYS)
        elif group=="capture":checked=tuple(item for item in checked if item.key not in {"sftp","terminal"} | TOOL_KEYS)
        keys={item.key for item in checked}
        merged=tuple(item for item in backend.snapshot.dependencies if item.key not in keys)+checked
        backend.snapshot=replace(backend.snapshot,dependencies=merged)
        sftp=next((item for item in checked if item.key=="sftp"),None)
        if sftp is not None:
            available=True if sftp.state==DependencyState.READY else False if sftp.state==DependencyState.MISSING else None
            backend.snapshot=replace(backend.snapshot,device=replace(backend.snapshot.device,sftp_available=available))

    def _emit_settings_message(self,text,level):
        self.owner.settingsMessageGenerated.emit(str(text),str(level))
        if self.task:
            self.task=replace(self.task,message=str(text))
            self.taskChanged.emit(self.task)

    def _complete(self,session,result):
        if self.task is None or self.task.session_id!=session:return
        task=self.task
        path=result.get("diagnostic_path")
        if path:
            from pathlib import Path
            from modules.log_services import DiagnosticReference
            self._diagnostics.append(DiagnosticReference(Path(path),Path(path).parent))
        error=str(result.get("error",""))
        if self._same_connection(self._binding):
            checked=result.get("checked",())
            if (task.group in {"common","terminal"} or task.group in TOOL_KEYS) and not checked:
                checked=(DependencyInfo(task.items[0],TOOL_BY_KEY[task.group].title if task.group in TOOL_KEYS else "终端会话保留" if task.group=="terminal" else "文件传输",DependencyState.UNKNOWN,detail=error),)
            if task.group in TOOL_KEYS and not any(item.key==task.group for item in checked):
                checked=tuple(checked)+(DependencyInfo(task.group,TOOL_BY_KEY[task.group].title,DependencyState.UNKNOWN,detail=error),)
            self._merge(checked,task.group,self._binding)
        else:error=error or "连接已变化，结果未应用到其他设备。"
        success=not error
        from .workflow_diagnostics import record_workflow
        record_workflow(self.owner, "dependency_request", "completed" if success else "failed",
                        stage=task.action, kind=task.group, reason=error or "verified")
        message=error or ("文件传输支持已卸载，SSH 登录不受影响。" if task.group=="common" and task.action=="uninstall" else "文件传输检测通过。" if task.group=="common" else "依赖清单已更新。")
        if not error and task.group=='terminal':message='终端会话保留支持已卸载，普通终端仍可使用。' if task.action=='uninstall' else '终端会话保留支持检测通过。'
        if not error and task.group in TOOL_KEYS:
            message=TOOL_BY_KEY[task.group].title+("已卸载。" if task.action=="uninstall" else "检测通过。")
        self._active=False
        self.task=None
        self._binding=None
        self.taskChanged.emit(None)
        self.owner.openwrtSnapshotChanged.emit(self.owner.openwrt_backend.snapshot)
        self.owner.settingsMessageGenerated.emit(message,"success" if success else "info" if "已取消" in error else "error")
        self.owner.task_controller.finish(failed=not success and "已取消" not in error)
        self.taskFinished.emit(session,success,message)

    def _sftp_install_succeeded(self,checked):
        """旧结果入口兼容：只合并文件传输状态，保留抓包开关。"""
        backend=self.owner.openwrt_backend
        sftp=next((item for item in checked if item.key=="sftp"),None)
        merged=tuple(item for item in backend.snapshot.dependencies if item.key!="sftp")
        if sftp is not None:
            merged+=(sftp,)
            backend.snapshot=replace(backend.snapshot,device=replace(backend.snapshot.device,sftp_available=sftp.state.value=="ready"))
        self._install_succeeded(merged)

    def _install_succeeded(self,checked):
        """兼容早期结果入口；新任务统一由连接绑定的完成入口处理。"""
        backend=self.owner.openwrt_backend
        backend.snapshot=replace(backend.snapshot,dependencies=tuple(checked))
        self.owner.openwrtSnapshotChanged.emit(backend.snapshot)
        self.owner.task_controller.finish()
        self._active=False
