"""登录页依赖下方的兼容工具卡片。"""
from PySide6.QtCore import QObject,Slot
from modules.display_privacy import PrivacyLabel
from modules.common_widgets.common_dialogs import ConfirmDialog,MessageDialog
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.main_pages.packet_capture.ui_helpers import create_capture_card


from .ui_features import PMKID_UI_VISIBLE


class CompatibleToolSection(QObject):
    """复用公共卡片和常驻任务服务，不创建独立窗口。"""
    def __init__(self,parent,service):
        super().__init__(parent)
        self.service=service
        self.pending={}
        self.card,self.layout=create_capture_card(parent,'PMKID 工具',
            '按当前固件和架构查找官方兼容包，并存安装，不替换原工具或系统依赖。')
        self.card.setVisible(PMKID_UI_VISIBLE)
        self.current_tool=PrivacyLabel('',self.card)
        self.current_tool.setObjectName('captureStatusValue')
        self.layout.addWidget(self.current_tool)
        self.note=PrivacyLabel('',self.card)
        self.note.setObjectName('captureDescription')
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        self.layout.addWidget(self.note)
        self.buttons=[]
        self.check_button=self._button('检查并安装兼容工具',self.check)
        self.reset_button=self._button('切回原有工具',lambda:self._submit('tool_reset'))
        self.cancel_button=self._button('取消任务',self.cancel)
        self.check_button.setToolTip('按当前固件和架构查找匹配包，找到后确认安装；不会替换原工具，仍需验证无线驱动兼容性。')
        self.reset_button.setToolTip('以后使用路由器原有工具，不卸载兼容工具，也不修改固件。')
        self.cancel_button.setToolTip('取消本区域的检查或安装，等待临时资源清理完成。')
        service.completed.connect(self._completed)
        service.failed.connect(self._failed)
        service.owner.openwrtSnapshotChanged.connect(self.apply_snapshot)
        service.owner.activityChanged.connect(self._activity_changed)
        self.apply_snapshot(None)

    def _button(self,text,callback):
        button=create_page_text_button(self.card,text,text)
        button.setObjectName('captureSecondaryButton')
        button.clicked.connect(callback)
        self.buttons.append(button)
        self.layout.addWidget(button)
        return button

    def set_light_theme(self,enabled):
        self.card.light_theme=bool(enabled)
        for button in self.buttons:button.set_light_theme(bool(enabled))

    def check(self):
        self._submit('tool_check')

    def _submit(self,action):
        self.note.setVisible(True)
        try:
            key=self.service.submit(action)
            self.pending[key]=action
            self.note.setText('已提交，等待执行。')
            self.apply_snapshot(None)
        except Exception as exc:
            self.note.setText(str(exc))

    @Slot(object)
    def apply_snapshot(self,snapshot):
        connected=self.service.manager.ssh.connected
        busy=self.service.owner.is_busy
        selected=self.service.tools.backend.pmkid_capture.tool_selection
        compatible=bool(connected and selected and selected[0] is self.service.manager.ssh._client)
        self.current_tool.setText("当前工具："+("兼容工具" if compatible else "路由器原有工具") if connected else "请先登录路由器")
        for button in self.buttons:button.setEnabled(connected and not busy and not self.pending)
        actions=set(self.pending.values())
        self.check_button.setText("检测中" if "tool_check" in actions else "安装中" if "tool_install" in actions else "检查并安装兼容工具")
        self.reset_button.setVisible(compatible)
        self.cancel_button.setVisible(bool(self.pending))
        self.cancel_button.setEnabled(bool(self.pending))

    @Slot()
    def _activity_changed(self):
        self.apply_snapshot(None)

    @Slot(str,object)
    def _completed(self,key,result):
        action=self.pending.pop(key,None)
        if action is None:return
        if action=='tool_check':
            text='找到适用于当前系统的工具：'+result['version']+'\n来源：'+result.get('source','当前固件的官方软件源')+'\n适用系统：OpenWrt '+result['release']+'\n\n'+result['note']
            self.note.setText(text)
            if ConfirmDialog('确认安装工具',text,self.card,yes_text='继续安装',no_text='暂不安装').exec()==ConfirmDialog.Accepted:
                self._submit('tool_install')
        else:self.note.setText(result['note'])
        self.apply_snapshot(None)

    @Slot(str,str)
    def _failed(self,key,message):
        if self.pending.pop(key,None) is not None:
            self.note.setText(message)
            self.apply_snapshot(None)

    def cancel(self):
        keys=tuple(self.pending)
        self.pending.clear()
        for key in keys:self.service.cancel(key)
        if keys:self.note.setText('已请求取消，正在清理临时文件。')
        self.apply_snapshot(None)

    def dispose(self):
        self.cancel()
        for signal,slot in ((self.service.completed,self._completed),(self.service.failed,self._failed),
                (self.service.owner.openwrtSnapshotChanged,self.apply_snapshot),(self.service.owner.activityChanged,self._activity_changed)):
            try:signal.disconnect(slot)
            except (TypeError,RuntimeError):pass
