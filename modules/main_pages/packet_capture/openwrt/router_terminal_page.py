"""内嵌路由器终端，前端按需加载，使用公共主题、按钮和确认弹窗。"""
import json
import shlex
import posixpath
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Slot, QUrl, Qt, QPoint
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QApplication,QFileDialog,QSizePolicy
from modules.config_manager import BASE_DIR
from modules.ui_scale_manager import UiScaleManager
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.dialog_theme import create_themed_menu
from modules.main_pages.page_action_buttons import create_page_text_button, create_page_icon_button


def terminal_command(path,script=False):
    """将绝对路由器路径正确引用；脚本在其目录执行，明确需要 Bash 时先检查。"""
    if not path.startswith("/") or any(ord(char)<32 for char in path):
        raise ValueError("路由器路径无效。")
    folder=posixpath.dirname(path) if script else path
    command="cd -- "+shlex.quote(folder)
    if script and not path.lower().endswith('.sh'):
        return command+" && "+shlex.quote("./"+posixpath.basename(path))
    if script:
        file=shlex.quote("./"+posixpath.basename(path))
        command+=(" && (IFS= read -r first < "+file+
            "; case \"$first\" in '#!'*bash*) "
            "if command -v bash >/dev/null 2>&1; then bash -- "+file+
            "; else printf '%s\\n' '脚本需要 Bash，但路由器未安装。'; fi;; "
            "*) sh -- "+file+";; esac)")
    return command


def terminal_url(text):
    """只接受用户点击的 HTTP/HTTPS 网页链接，不把链接当命令。"""
    if len(text)>8192 or any(ord(char)<32 for char in text):return None
    url=QUrl(text,QUrl.StrictMode)
    return url if url.isValid() and url.scheme().lower() in {'http','https'} and url.host() and not url.userInfo() else None


class TerminalBridge(QObject):
    """只为本地终端资源提供输入、确认粘贴和流量确认接口。"""
    output=Signal(str)
    control=Signal(str)
    def __init__(self,page):
        super().__init__(page);self.page=page
    @Slot(str)
    def input(self,text):self.page.service.send(text)
    @Slot(int,int)
    def resize(self,cols,rows):self.page.service.resize(cols,rows)
    @Slot(int)
    def ack(self,count):self.page.service.acknowledge(count)
    @Slot(str)
    def copy(self,text):QApplication.clipboard().setText(text)
    @Slot(str)
    def paste(self,text):self.page.confirm_paste(text)
    @Slot()
    def requestPaste(self):self.page.confirm_paste(QApplication.clipboard().text())
    @Slot(bool)
    def selectionChanged(self,selected):
        self.page._has_selection=selected
        self.page.copy_button.setEnabled(self.page.ready and selected)
    @Slot(str)
    def openUrl(self,text):self.page.open_url(text)
    @Slot(int,int,bool,str)
    def contextMenu(self,x,y,selected,url):self.page.show_context_menu(x,y,selected,url)
    @Slot(int)
    def zoom(self,direction):self.page.zoom(direction)
    @Slot()
    def findRequested(self):self.page.find_edit.show();self.page.find_edit.setFocus()
    @Slot()
    def ready(self):
        self.page.ready=True
        self.page.apply_theme()
        self.page._status(self.page.service.state)
        if self.page._start_requested:self.page.service.start()


class RouterTerminalPage(QWidget):
    """终端内容仅在内存和受限历史中保留，不订阅或导出普通信息日志。"""
    def __init__(self,service,parent=None):
        super().__init__(parent)
        self.service=service
        self.ready=False
        self.view=None
        self._start_requested=False
        self._pending_command=None
        self._terminal_connection=None
        snapshot=getattr(service.owner,"openwrt_snapshot",None)
        model=getattr(getattr(snapshot,"device",None),"model","")
        self._device_name=model if isinstance(model,str) else ""
        self.light_theme=False
        self._font_size=14
        self._has_selection=False
        self._failure_detail=getattr(service,'failure_reason','')
        self._export_service=None
        self._applied_terminal_theme=None
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(8)
        row=QHBoxLayout()
        self.status=QLabel("请先登录路由器，进入本页后连接终端。",self)
        self.status.setObjectName("captureDescription")
        # 允许状态文字压缩，避免长失败原因把整页最小宽度顶到 950+，窄窗口时被强制撑宽。
        self.status.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        row.addWidget(self.status,1)
        self.connect_button=create_page_text_button(self,"连接终端","只新建终端通道，不重跑上一条命令。")
        self.connect_button.clicked.connect(self.enter)
        self.close_button=create_page_text_button(self,"断开终端","只断开显示通道，路由器内的会话和脚本继续运行。")
        self.close_button.clicked.connect(self.close_terminal)
        self.clear_button=create_page_text_button(self,"清空屏幕","只清除本地显示，不向路由器发送命令。")
        self.clear_button.clicked.connect(lambda:self.control(action="clear"))
        self.export_button=create_page_text_button(self,"导出诊断","导出终端诊断信息（不含终端内容），便于反馈排查。")
        self.export_button.clicked.connect(self.export_diagnostics)
        for button in (self.connect_button,self.close_button,self.clear_button,self.export_button):row.addWidget(button)
        self.copy_button=create_page_icon_button(self,str(BASE_DIR/"images/icons/cil-clone.png"),"复制选中的终端文字")
        self.paste_button=create_page_icon_button(self,str(BASE_DIR/"images/icons/cil-clipboard.png"),"粘贴剪贴板内容；多行命令会先确认")
        self.copy_button.clicked.connect(lambda:self.control(action="copy"))
        self.paste_button.clicked.connect(lambda:self.confirm_paste(QApplication.clipboard().text()))
        for button in (self.copy_button,self.paste_button):
            UiScaleManager.for_widget(self).bind_metrics(button,fixed_size=(32,32),icon_size=18)
            row.addWidget(button)
        self.copy_button.setEnabled(False)
        self.install_button=create_page_text_button(self,'安装会话保留支持','在路由器上安装 tmux，断开后脚本仍继续运行。')
        self.install_button.clicked.connect(self.install_session_support)
        self.install_button.hide();row.addWidget(self.install_button)
        self._install_dialog=None
        self._install_session=None
        dependency=getattr(service.owner,'dependency_service',None)
        if dependency is not None:
            dependency.taskChanged.connect(self._dependency_changed)
            dependency.taskFinished.connect(self._dependency_finished)
        layout.addLayout(row)
        self.notice=QLabel('',self)
        self.notice.setObjectName('captureDescription');self.notice.setWordWrap(True)
        self.notice.hide();layout.addWidget(self.notice)
        self._session_support_installed=False
        self._session_support_state='unknown'
        self.find_edit=QLineEdit(self);self.find_edit.setObjectName("captureLineEdit")
        self.find_edit.setPlaceholderText("查找终端历史，Enter 查找下一个")
        self.find_edit.returnPressed.connect(lambda:self.control(action="find",text=self.find_edit.text()))
        self.find_edit.hide();layout.addWidget(self.find_edit)
        self.placeholder=QLabel("路由器终端：直接输入命令；Tab 补全，↑/↓ 历史，Ctrl+C 中断。",self)
        self.placeholder.setObjectName("captureDescription");layout.addWidget(self.placeholder,1)
        self.layout_root=layout
        self.bridge=TerminalBridge(self)
        service.output.connect(self.bridge.output)
        service.statusChanged.connect(self._status)
        service.failureDetail.connect(self._failure_detail_changed)
        service.owner.openwrtSnapshotChanged.connect(self._connection_changed)
        self._status(service.state)

    def control(self,**value):
        if self.ready:self.bridge.control.emit(json.dumps(value,ensure_ascii=False))

    def _load_view(self):
        if self.view:return
        from modules.terminal_widgets.native_view import NativeTerminalView
        self.view=NativeTerminalView(self.bridge,self)
        self.placeholder.hide();self.layout_root.addWidget(self.view,1)
        self.bridge.ready()

    def enter(self):
        self._failure_detail=""
        try:
            self._load_view()
        except (ImportError, OSError, AttributeError) as exc:
            self.service.report_component_failure(exc)
            return
        self._start_requested=True
        if self.ready:self.service.start();self.control(action="focus")

    def _connection_changed(self,snapshot):
        if self.service.ssh._client is not self._terminal_connection:
            self._session_support_installed=False;self._session_support_state='unknown'
        items=getattr(snapshot,'dependencies',())
        if isinstance(items,(tuple,list)):
            support=next((item for item in items if item.key=='terminal'),None)
            if support is not None:
                self._session_support_installed=support.state.value=='ready'
                self._session_support_state=support.state.value

        model=getattr(getattr(snapshot,"device",None),"model","")
        self._device_name=model if isinstance(model,str) and self.service.ssh.connected else ""
        if self._pending_command and self.service.ssh._client is not self._pending_command[1]:
            self._pending_command=None
        self._status(self.service.state)

    def _status(self,state):
        labels={"idle":"终端尚未连接","connecting":"正在连接终端…","connected":"终端已连接",
            "stopping":"正在关闭终端…","closed":"终端已关闭","disconnected":"终端连接已断开，点击重新连接",
            "failed":"终端连接失败，请检查路由器登录状态后重试","close_failed":"终端通道回收失败，请重新登录"}
        prefix=self._device_name+" · " if self._device_name else ""
        mode=getattr(self.service,'session_mode','unknown')
        persistent=mode=='persistent'
        labels['connected']='终端已连接 · 会话保留中' if persistent else '普通终端已连接'
        note=''
        if state=='connected' and not persistent:
            note=('会话保留支持已安装。下次连接终端时启用，当前脚本不会自动迁移。' if self._session_support_installed else
                  '可正常输入命令。当前为普通终端，断线后脚本可能停止；可选安装会话保留支持。')
        elif state=='failed':
            note="终端连接失败："+(self._failure_detail or "请检查路由器登录状态后重试。")
        elif state in {'disconnected','close_failed'}:
            note=labels[state]
        if state=='connected' and mode=='plain_unavailable':
            note='会话保留工具检测未通过，已打开普通终端，可正常输入命令。断线后脚本可能停止。'
        self.notice.setText(note);self.notice.setVisible(bool(note))
        missing=self._session_support_state=='missing' or (mode=='plain_missing' and self._session_support_state=='unknown')
        self.install_button.setVisible(missing and not self._session_support_installed)
        self.install_button.setEnabled(self.service.ssh.connected and not getattr(self.service.owner,'is_busy',False))
        dependency=getattr(self.service.owner,'dependency_service',None)
        task=getattr(dependency,'task',None)
        if task is not None and task.group=='terminal' and task.action=='install':
            self.install_button.setVisible(True);self.install_button.setEnabled(False)
            self.install_button.setText('安装中')

        label=labels.get(state,"终端输入队列已满，请稍后再试")
        if state=='failed' and self._failure_detail:label="终端连接失败："+self._failure_detail
        self.status.setText(prefix+label)
        self.status.setToolTip(label)
        self.connect_button.setText("重新连接终端" if state in {"failed","disconnected","closed"} else "连接终端")
        self.connect_button.setEnabled(self.service.ssh.connected and not self.service.active)
        self.close_button.setEnabled(self.service.active)
        self.paste_button.setEnabled(self.ready and state=="connected")
        self.control(action="state",connected=state=="connected")
        if state=="connected" and self._terminal_connection is not self.service.ssh._client:
            self._terminal_connection=self.service.ssh._client
            self.control(action="reset")
        self.close_button.setToolTip('只断开显示通道，脚本继续运行。' if persistent else '普通终端断开可能中断脚本。')
        if state=="connected" and self._pending_command:
            command,connection=self._pending_command;self._pending_command=None
            if connection is self.service.ssh._client:self.service.send("\x05\x15"+command+"\r")
        if state in {"closed","failed","disconnected","close_failed","tmux_missing","tmux_failed"}:self._pending_command=None

    def open_url(self,text):
        """在系统浏览器打开链接，不在本地终端页面执行远程内容。"""
        url=terminal_url(text)
        if url is None:return False
        if QDesktopServices.openUrl(url):return True
        MessageDialog('无法打开链接','请检查系统默认浏览器，或从右键菜单复制链接后手动打开。',self).exec()
        return False

    def show_context_menu(self,x,y,selected,url):
        """复用公共菜单，右键不再直接向命令行粘贴。"""
        if not self.view:return
        menu=create_themed_menu(self,self.light_theme)
        def add(text,callback,enabled=True):
            action=menu.addAction(text);action.setEnabled(enabled);action.triggered.connect(callback)
        if terminal_url(url) is not None:
            add('打开链接',lambda:self.open_url(url))
            add('复制链接',lambda:QApplication.clipboard().setText(url))
            menu.addSeparator()
        add('复制',lambda:self.control(action='copy'),selected)
        add('粘贴',lambda:self.confirm_paste(QApplication.clipboard().text()),self.service.state=='connected' and bool(QApplication.clipboard().text()))
        add('全选',lambda:self.control(action='selectAll'))
        add('查找',self.bridge.findRequested)
        add('清空屏幕',lambda:self.control(action='clear'))
        try:menu.exec(self.view.mapToGlobal(QPoint(x,y)))
        finally:menu.deleteLater()

    def close_terminal(self):
        persistent=getattr(self.service,'session_mode','unknown')=='persistent'
        note=('只断开显示通道，脚本仍在路由器中运行，重连可接回。' if persistent else
              '当前为普通终端。断开可能中断正在运行的脚本。')
        if self.service.active and ConfirmDialog('断开路由器终端',note+'确认断开？',self).exec()==ConfirmDialog.Accepted:
            self._pending_command=None;self.service.stop()

    def install_session_support(self):
        """复用依赖服务和公共进度窗，不在主线程安装。"""
        dependency=getattr(self.service.owner,'dependency_service',None)
        if dependency is None or self.service.owner.is_busy:return
        if ConfirmDialog('安装终端会话保留支持',
            '将安装 tmux 及其必需依赖。不替换 SSH 服务。脚本在断线后继续运行，重连可恢复当前画面。路由器重启后会话不保留。',self,
            yes_text='安装').exec()==ConfirmDialog.Accepted:
            dependency.install_async({'terminal':True},scope='terminal')

    def _failure_detail_changed(self,reason):
        """接收服务转发的失败原因；正处失败态时刷新可见文字。"""
        self._failure_detail=str(reason or "")
        if self.service.state=='failed':self._status('failed')

    def _diagnostic_entries(self):
        """终端诊断文字；只含状态与原因，不含终端内容或命令。"""
        items=getattr(getattr(self.service.owner,'openwrt_snapshot',None),'dependencies',()) or ()
        support=next((item for item in items if getattr(item,'key','')=='terminal'),None)
        state=getattr(getattr(support,'state',None),'value','unknown')
        entries = [
            ("路由器终端诊断","info"),
            (f"设备型号：{self._device_name or '未知'}","info"),
            (f"终端状态：{self.service.state}","info"),
            (f"会话模式：{getattr(self.service,'session_mode','unknown')}","info"),
            (f"失败原因：{self._failure_detail or '无'}","info"),
            (f"会话保留支持：{state}；{getattr(support,'detail','') or '无'}","info"),
        ]

        history=getattr(self.service,'history_diagnostic','')
        if history:entries.append((history,'warning'))
        return entries

    def export_diagnostics(self):
        """导出终端诊断 ZIP；复用公共日志导出，含 router-info.json，不含终端内容。"""
        from modules.common_widgets.common_dialogs import MessageDialog
        from modules.config_manager import user_downloads_dir
        from modules.display_privacy import Masker
        from modules.log_services import LogExportService, build_export_filename
        owner=self.window() or self
        runtime=getattr(self.window(),'runtime',None)
        if runtime is not None:
            if getattr(runtime,'_exiting',False):return
            service=runtime.log_export_service
        else:
            service=getattr(owner,'log_export_service',None)
            if service is None:
                service=LogExportService(owner);owner.log_export_service=service
        if service.active:
            MessageDialog('导出终端诊断','已有日志正在导出，请等待完成。',owner).exec();return
        picked,_=QFileDialog.getSaveFileName(owner,'导出终端诊断',
            str(user_downloads_dir()/build_export_filename('router-terminal')),'ZIP (*.zip)')
        if not picked:return
        from modules.capture_services.openwrt.diagnostic_context import router_context_json
        masker=Masker()
        try:
            backend = self.service.owner.openwrt_backend
            router_context=router_context_json(self.service.owner.openwrt_snapshot,masker,
                radio_evidence=backend.command_runner.radio_diagnostics.snapshot(),
                operation_evidence=backend.command_runner.operation_diagnostics.snapshot(),
                signal_snapshot=self.service.owner.router_signals.snapshot,
                signal_setup_snapshot=self.service.owner.router_signals.setup.snapshot,
                application_window=self.window(),
                device_history=self.service.owner.diagnostic_history,
                app_version=QApplication.instance().applicationVersion())
        except Exception as exc:
            MessageDialog('导出终端诊断','无法获取当前路由器诊断信息：'+str(exc),owner).exec();return
        self._export_service=service
        service.completed.connect(self._export_completed)
        service.failed.connect(self._export_failed)
        version_label=getattr(getattr(self.window(),'ui',None),'version',None)
        version=version_label.text().lstrip('v') if version_label is not None else ''
        started=service.start(destination=picked,category='router-terminal',
            entries=self._diagnostic_entries(),masker=masker,router_context=router_context,app_version=version)
        if not started:
            self._export_teardown()
            MessageDialog('导出终端诊断','另一个导出任务已开始，请等待完成后重试。',owner).exec()

    def _export_completed(self,result):
        from modules.common_widgets.common_dialogs import MessageDialog
        if getattr(getattr(self.window(),'runtime',None),'_exiting',False):return
        window=self.window() or self
        self._export_teardown()
        MessageDialog('导出终端诊断完成','已导出到：\n'+str(result.get('zip_path','')),window).exec()

    def _export_failed(self,reason):
        from modules.common_widgets.common_dialogs import MessageDialog
        if getattr(getattr(self.window(),'runtime',None),'_exiting',False):return
        window=self.window() or self
        self._export_teardown()
        MessageDialog('导出终端诊断失败',str(reason)+'\n请检查目录权限和磁盘空间。',window).exec()

    def _export_teardown(self):
        """断开本次导出订阅，避免共享导出服务上残留页面槽。"""
        service=self._export_service
        if service is None:return
        for signal,slot in ((service.completed,self._export_completed),(service.failed,self._export_failed)):
            try:signal.disconnect(slot)
            except (TypeError,RuntimeError):pass
        self._export_service=None

    def _dependency_changed(self,task):
        if task is None or task.group!='terminal':return
        if task.action!='install' or (self._install_dialog is None and not self.isVisible()):return
        from modules.common_widgets.task_progress_dialog import TaskProgressDialog
        self.install_button.setVisible(True);self.install_button.setEnabled(False)
        self.install_button.setText('安装中')
        if self._install_dialog is None:
            self._install_session=task.session_id
            self._install_dialog=TaskProgressDialog('安装终端会话保留支持',parent=self)
            self._install_dialog.cancelRequested.connect(lambda key=task.session_id:self.service.owner.dependency_service.cancel(key))
        self._install_dialog.update_stage(task.message)

    def _dependency_finished(self,session,success,message):
        if session!=self._install_session:return
        if self._install_dialog is not None:self._install_dialog.finish(success,message)
        self._install_dialog=None;self._install_session=None
        self.install_button.setText('安装会话保留支持')
        if success:
            self._session_support_installed=True;self._session_support_state='ready'
        self._status(self.service.state)
        if success and not self.service.active:self.enter()

    def confirm_paste(self,text):
        if self.service.state!="connected" or not text:return
        if len(text.encode("utf-8"))>65536:
            self.status.setText("粘贴内容超过 64 KiB，请分批粘贴。")
            return
        if any(char in text for char in ("\r","\n")):
            dialog=ConfirmDialog("确认粘贴多行命令",text+"\n\n多行内容可能立即执行，确认发送？",self)
            if dialog.exec()!=ConfirmDialog.Accepted:return
        self.control(action="paste",text=text)

    def open_path(self,path,script=False):
        command=terminal_command(path,script)
        note=path+"\n\n"+command
        if self.service.active:note+="\n\n请确认终端已回到命令提示符；其他命令运行中请先按 Ctrl+C 退出。未提交的命令输入将清除，再执行本次操作。"
        if ConfirmDialog("在终端运行文件" if script else "在终端打开目录",note,self).exec()!=ConfirmDialog.Accepted:return False
        connection=self.service.ssh._client
        if not self.service.ssh.connected:return False
        self._pending_command=(command,connection)
        self.enter()
        if self.service.state=="connected":self._status("connected")
        return True

    def set_light_theme(self,enabled):
        self.light_theme=bool(enabled);self.apply_theme()

    def zoom(self,direction):
        """Ctrl 滚轮双向调整逻辑字号，保留页面内偏好并跟随应用缩放。"""
        if not direction:return
        size=max(8,min(32,self._font_size+(1 if direction>0 else -1)))
        if size!=self._font_size:
            self._font_size=size
            self.apply_theme()

    def apply_theme(self):
        scale=UiScaleManager.for_widget(self)
        value=(round(scale.px(self._font_size)),self.light_theme)
        if self.ready and value!=self._applied_terminal_theme:
            self._applied_terminal_theme=value
            self.control(action="theme",fontSize=value[0],light=value[1])

    def dispose(self):
        """活动会话由常驻机制保护页面；这里只解除页面订阅和本地原生控件。"""
        dependency=getattr(self.service.owner,'dependency_service',None)
        if dependency is not None:
            dependency.taskChanged.disconnect(self._dependency_changed)
            dependency.taskFinished.disconnect(self._dependency_finished)
        if self._install_dialog is not None:
            self._install_dialog.cancelRequested.disconnect()
            self._install_dialog.finish(True);self._install_dialog=None
        self.service.output.disconnect(self.bridge.output)
        self.service.statusChanged.disconnect(self._status)
        self.service.failureDetail.disconnect(self._failure_detail_changed)
        self.service.owner.openwrtSnapshotChanged.disconnect(self._connection_changed)
        self._export_teardown()
        if self.view:
            self.view.dispose()
            self.view.deleteLater()
            self.view=None
            self.ready=False
