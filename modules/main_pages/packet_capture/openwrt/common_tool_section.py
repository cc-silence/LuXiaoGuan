"""登录页常用工具依赖：文件传输和终端会话保留。"""
from PySide6.QtCore import QObject,Slot,QEvent,Qt
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.common_widgets.task_progress_dialog import TaskProgressDialog
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.main_pages.packet_capture.ui_helpers import create_capture_card
from modules.capture_services.openwrt.common_tool_dependencies import TOOLS, OPTIONAL_TOOL_KEYS
from .dependency_row import create_dependency_row, set_dependency_row_installed


class CommonToolSection(QObject):
    """订阅常驻状态；界面释放只回收订阅和弹窗，不拥有后台安装。"""
    def __init__(self,parent,owner, *, other=False):
        super().__init__(parent)
        from modules.capture_services.openwrt.other_tool_dependencies import OTHER_TOOLS
        self.other = other
        self.tools = OTHER_TOOLS if other else TOOLS
        self.tool_keys = frozenset(tool.key for tool in self.tools)
        self.tool_by_key = {tool.key: tool for tool in self.tools}
        self.optional_keys = self.tool_keys if other else OPTIONAL_TOOL_KEYS
        self.task_groups = self.tool_keys | ({"other_check"} if other else {"common", "common_all", "terminal"})
        self.bulk_group = "other_check" if other else "common_all"
        self.bulk_text = "检查其他依赖" if other else "检查并安装路小管必需依赖"
        self.owner=owner
        self.service=owner.dependency_service
        self.dialog=None
        self.dialog_session=None
        self.dialog_action=None
        self.card,self.layout=create_capture_card(parent,"其他依赖 ·需联网安装" if other else "常用工具依赖",
            "可选网页功能和服务，按需单独安装。仅支持联网安装，不提供离线包；软件源与常用依赖一致。" if other else "\u767b\u5f55\u540e\u81ea\u52a8\u68c0\u67e5\u5168\u90e8\u4f9d\u8d56\uff1b\u53ef\u9009\u5de5\u5177\u4ec5\u5728\u624b\u52a8\u9009\u62e9\u65f6\u5b89\u88c5\u3002", collapsible=True, expanded=False)
        from .common_bundle_controls import CommonBundleControls
        self.offline_controls=None if other else CommonBundleControls(self.card,self.layout,owner)
        self.rows={}
        self.names={}
        self.row_widgets={}
        self.selected_key=None
        self.layout.setSpacing(2)
        entries=() if other else (('sftp','文件传输','openssh-sftp-server'),('terminal','终端会话保留','tmux'))
        entries+=tuple((tool.key,tool.title,tool.package) for tool in self.tools)
        entries = tuple(sorted(entries, key=lambda item: item[0] == "terminal" or item[0] in OPTIONAL_TOOL_KEYS))
        for position,(key,title,package) in enumerate(entries):
            if key == "terminal" or key in self.optional_keys:
                title += "\uff08\u53ef\u9009\uff09"
            container,name,status,button=create_dependency_row(
                self.card,key,title,package,position,self._clicked,observer=self)
            self.row_widgets[key]=container
            self.names[key]=name
            if other and self.tool_by_key[key].summary:
                name.setText(name.text() + "（" + self.tool_by_key[key].summary + "）")
            if key in self.tool_keys:name.setToolTip(self.tool_by_key[key].description)
            self.layout.addWidget(container);self.rows[key]=(status,button)
        self.install_all_button=create_page_text_button(self.card,self.bulk_text,"只读检查本组可选功能，不自动安装。" if other else "只补齐本组缺失项，复用统一安装流程。")
        self.install_all_button.setObjectName("captureSecondaryButton")
        self.install_all_button.setProperty("dependencyInstallPrimary", True)
        self.install_all_button.clicked.connect(self._install_all)
        self.layout.addWidget(self.install_all_button)
        if not other:
            self.status,self.button=self.rows['sftp']
            self.terminal_status,self.terminal_button=self.rows['terminal']
        owner.openwrtSnapshotChanged.connect(self.apply_snapshot)
        owner.activityChanged.connect(self._activity_changed)
        self.service.taskChanged.connect(self._task_changed)
        self.service.taskFinished.connect(self._task_finished)
        self.service.requestRejected.connect(self._request_rejected)
        self.apply_snapshot(owner.openwrt_snapshot)
        self._task_changed(self.service.task)

    def _install_all(self):
        session=self.install_all_button.property("dependencyStopSession")
        if session:
            self.service.cancel(str(session))
            return
        if self.other:
            self.service.check_other_async()
            return
        from .common_dependency_actions import install_common
        install_common(self)

    def eventFilter(self,watched,event):
        """点击行或按钮时标明所属依赖；行选择不触发安装卸载。"""
        key=watched.property("dependencyKey")
        if key in self.row_widgets:
            if event.type()==QEvent.FocusIn or (event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton):
                self._select_row(key)
            if event.type()==QEvent.KeyPress and watched is self.row_widgets[key]:
                keys=list(self.row_widgets)
                if event.key() in (Qt.Key_Up,Qt.Key_Down):
                    offset=-1 if event.key()==Qt.Key_Up else 1
                    index=max(0,min(len(keys)-1,keys.index(key)+offset))
                    self.row_widgets[keys[index]].setFocus(Qt.TabFocusReason)
                    return True
        return super().eventFilter(watched,event)

    def _select_row(self,key):
        """仅刷新新旧选中行，不重建列表或更改后台状态。"""
        if self.selected_key==key:return
        previous=self.selected_key
        self.selected_key=key
        for item in (previous,key):
            row=self.row_widgets.get(item)
            if row is None:continue
            row.setProperty("selected",item==key)
            row.style().unpolish(row);row.style().polish(row);row.update()

    def set_light_theme(self,enabled):
        if self.offline_controls is not None:
            self.offline_controls.set_light_theme(enabled)
        self.card.light_theme=bool(enabled)
        self.install_all_button.set_light_theme(bool(enabled))
        for status,button in self.rows.values():button.set_light_theme(bool(enabled))

    @Slot()
    def _activity_changed(self):
        self.apply_snapshot(self.owner.openwrt_snapshot)

    @Slot(object)
    def apply_snapshot(self,snapshot):
        connected=snapshot.connection_state.value=='connected'
        task=self.service.task
        bulk=task is not None and task.group==self.bulk_group
        self.install_all_button.setProperty("dependencyStopSession",task.session_id if bulk else None)
        self.install_all_button.setText(("停止检查" if self.other else "停止安装") if bulk else self.bulk_text)
        self.install_all_button.setEnabled(connected and (bulk or not self.owner.is_busy))
        for key,(status,button) in self.rows.items():
            own=task is not None and key in task.items and task.group in self.task_groups
            item=next((item for item in snapshot.dependencies if item.key==key),None)
            state=item.state.value if item is not None else 'unknown'
            set_dependency_row_installed(self.row_widgets[key], connected and not own and state in {'ready', 'upgradable', 'outdated'})
            if own and connected:
                text={'install':'安装中','uninstall':'卸载中'}.get(task.action,'检测中')
                status.setText(text);button.setText(text);button.setVisible(True);button.setEnabled(False)
                continue
            if not connected:
                status.setText('检测中' if snapshot.connection_state.value=='connecting' else '请先登录')
                button.setText('安装');button.setVisible(True);button.setEnabled(False)
                continue
            if item is None and (key == 'terminal' or key in self.optional_keys):
                status.setText('\u53ef\u9009\uff0c\u672a\u68c0\u67e5')
                button.setText('\u68c0\u67e5')
                button.setVisible(True)
                button.setEnabled(not self.owner.is_busy)
                button.setToolTip('\u53ef\u9009\u5de5\u5177\uff0c\u767b\u5f55\u540e\u81ea\u52a8\u68c0\u67e5\uff0c\u4e0d\u53c2\u4e0e\u6279\u91cf\u8865\u88c5\uff1b\u70b9\u51fb\u53ef\u5237\u65b0\u72b6\u6001\u3002')
                continue
            status.setText({'ready':'已安装','missing':'未安装','checking':'检测中'}.get(state,'检测失败'))
            status.setToolTip(item.detail if item is not None else '请重新检查。')
            button.setText('安装' if state=='missing' else '卸载' if state=='ready' else '重新检查')
            button.setVisible(True);button.setEnabled(state!='checking' and not self.owner.is_busy)
            if key in self.tool_keys:
                if state=='ready' and not self.tool_by_key[key].removable:button.setText('检查依赖')
                button.setToolTip(self.tool_by_key[key].description + ("\n这些是路由器共用组件，为避免影响其他插件，暂不提供一键卸载。点击只重新检查依赖是否齐全。" if not self.tool_by_key[key].removable else ""))
                continue
            button.setToolTip('会话保留工具：断线后继续运行，重连可接回。卸载后普通终端仍可用，但无法重新接回保留会话。' if key=='terminal' else
                '文件传输支持：用于浏览、上传和下载。卸载后文件管理不可用，SSH 登录不受影响。')

    def _clicked(self,key='sftp'):
        if not self.service.ensure_available("selected_tool", key):return
        snapshot=self.owner.openwrt_snapshot
        item=next((item for item in snapshot.dependencies if item.key==key),None)
        state=item.state.value if item is not None else 'unknown'
        if key in self.tool_keys:
            tool=self.tool_by_key[key]
            if state not in {'missing','ready'} or (state=='ready' and not tool.removable):
                self.service.check_tool_async(key)
                return
            uninstall=state=='ready'
            verb='卸载' if uninstall else '安装'
            note=verb+' '+tool.package+'。\n'+tool.description
            note+=('\n只卸载本项明确列出且实际安装的包。先检查卸载计划；如果有其他功能依赖，或会连带删除清单外的包，则停止并保留。逐包处理，中途停止时已卸载的包不会自动装回。卸载后本软件的相关功能也可能不可用，重装可恢复。' if uninstall else
                   '\n可能需要联网安装必需依赖；软件源不匹配时停止，不强制替换已有软件。')
            if self.other and not uninstall:
                note += "\n不使用预制离线包：路由器联网安装，网络失败时由电脑联网下载校验后转装。"
            if ConfirmDialog(verb+tool.title,note,self.card,yes_text=verb).exec()!=ConfirmDialog.Accepted:return
            if uninstall:self.service.uninstall_async({key:True},list(tool.package_names),scope=key)
            else:self.service.install_async({key:True},scope=key)
            return
        terminal=key=='terminal'
        title='终端会话保留支持' if terminal else '文件传输支持'
        package='tmux' if terminal else 'openssh-sftp-server'
        if state in {'missing','ready'}:
            uninstall=state=='ready'
            verb='卸载' if uninstall else '安装'
            note=('卸载后不能重新接回保留会话，请先处理好运行中的脚本。不主动结束已有会话，普通终端仍可用。' if terminal and uninstall else
                  '卸载后 \u8def\u5c0f\u7ba1 无法浏览、上传或下载文件。' if uninstall else
                  '安装时可能需要联网下载必需依赖，支持取消。')
            note=verb+' '+package+'。'+note+'\nSSH 登录服务不会被替换；卸载不移除依赖库。'
            if ConfirmDialog(verb+title,note,self.card,yes_text=verb).exec()!=ConfirmDialog.Accepted:return
            if uninstall:self.service.uninstall_async({'terminal':True} if terminal else {},[package],scope='terminal' if terminal else 'common')
            elif terminal:self.service.install_async({'terminal':True},scope='terminal')
            else:self.service.install_async({},sftp_only=True)
        elif terminal:self.service.check_terminal_async()
        else:self.service.check_common_async()

    @Slot(str)
    def _request_rejected(self, message):
        if not self.other and self.card.isVisible():
            MessageDialog("\u64cd\u4f5c\u672a\u5f00\u59cb", message, self.card).exec()

    @Slot(object)
    def _task_changed(self,task):
        self.apply_snapshot(self.owner.openwrt_snapshot)
        if task is None or task.group not in self.task_groups:return
        if self.dialog is None and not self.card.isVisible():return
        if self.dialog is None:
            self.dialog_session=task.session_id
            self.dialog_action=task.action
            title=self.tool_by_key[task.group].title if task.group in self.tool_keys else '其他依赖' if task.group=='other_check' else '全部常用依赖' if task.group=='common_all' else '终端会话保留支持' if task.group=='terminal' else '文件传输支持'
            self.dialog=TaskProgressDialog({'install':'安装','uninstall':'卸载'}.get(task.action,'检查')+title,
                parent=self.card,cancel_text="取消任务")
            self.dialog.cancelRequested.connect(lambda key=task.session_id:self.service.cancel(key))
        self.dialog.update_stage(task.message)

    @Slot(str,bool,str)
    def _task_finished(self,session,success,message):
        if self.dialog is not None and self.dialog_session==session:
            dialog=self.dialog
            self.dialog=None;self.dialog_session=None
            dialog.finish(success,message,close_on_failure=self.dialog_action=="check")
            self.dialog_action=None
        self.apply_snapshot(self.owner.openwrt_snapshot)

    def dispose(self):
        """断开页面订阅；后台生命周期继续由常驻服务负责。"""
        if self.offline_controls is not None:
            self.offline_controls.dispose()
        for signal,slot in ((self.owner.openwrtSnapshotChanged,self.apply_snapshot),
                            (self.owner.activityChanged,self._activity_changed),
                            (self.service.taskChanged,self._task_changed),
                            (self.service.taskFinished,self._task_finished),
                             (self.service.requestRejected,self._request_rejected)):
            signal.disconnect(slot)
        if self.dialog is not None:
            self.dialog.cancelRequested.disconnect()
            self.dialog.finish(True)
            self.dialog=None;self.dialog_session=None
