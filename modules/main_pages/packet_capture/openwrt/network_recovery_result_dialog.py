"""Persistent result guidance and explicitly requested local network recovery."""
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme,_create_dialog_button,ConfirmDialog
from modules.ui_scale_manager import UiScaleManager
from PySide6.QtWidgets import QCheckBox
from .lan_recovery_panel import LanRecoveryPanel


class NetworkRecoveryResultDialog(FramelessDialog):
    def __init__(self,result,plan,owner,parent=None,*,requires_restart_confirmation=False):
        super().__init__(result.get('recovery_title','恢复出厂：重新连接'),parent,inherit_parent_stylesheet=False,window_controls=False)
        apply_common_dialog_theme(self,parent)
        self.set_scaled_size((620,480))
        self.broker=getattr(owner,'lan_recovery',None)
        self.progress_signal=getattr(owner,'configurationProgressChanged',None)
        self.offer=None
        self.waiting_for_restart=requires_restart_confirmation
        plan=dict(plan)
        verified=result.get('checked_apply') is True and result.get('applied') is True and result.get('outcome') not in {'restored','uncertain'} and not self.waiting_for_restart
        if self.waiting_for_restart:
            self.ready=QCheckBox("我已确认路由器重启完成，网线或 Wi-Fi 已连接",self)
            self.content_layout.addWidget(self.ready)
        plan['guide']=result['note'] if verified else result['note']+'\n\n'+plan['guide']
        if verified:plan['adapters']=[]
        self.panel=LanRecoveryPanel(self)
        scale=UiScaleManager.for_widget(self)
        self.panel.guide.setMinimumHeight(scale.px(230))
        self.panel.guide.setMaximumHeight(scale.px(300))
        self.content_layout.addWidget(self.panel,1)
        if not verified and self.broker is not None and self.progress_signal is not None:
            self.offer=self.broker.create_offer(plan,ready=not self.waiting_for_restart)
            self.panel.update_payload(self.broker.payload(self.offer))
            self.progress_signal.connect(self._progress)
            self.panel.requested.connect(self._request)
            if self.waiting_for_restart:self.panel.reconnectRequested.connect(self._reconnect)
            if self.waiting_for_restart:self.ready.toggled.connect(self._ready)
            broker,key=self.broker,self.offer
            self.destroyed.connect(lambda *_:broker.release(key))
        else:
            self.panel.update_payload(dict(plan,can_renew=False))
            if verified:self.panel.status.hide()
        self.close_button=_create_dialog_button('关闭',self,'settingsPrimaryButton')
        self.button_layout.addWidget(self.close_button)
        self.close_button.clicked.connect(self.accept)
        self.finished.connect(self._release)

    def _ready(self,checked):
        if self.offer:
            self.broker.set_ready(self.offer,checked)
            self.panel.update_payload(self.broker.payload(self.offer))

    def _request(self,identity):
        if self.offer:self.broker.request(self.offer,identity)

    def _reconnect(self,identity):
        if not self.offer:
            return
        text="\u5c06\u91cd\u65b0\u8fde\u63a5\u6240\u9009\u7f51\u5361\u4e00\u6b21\uff0c\u8be5\u7f51\u5361\u4e0a\u7684\u7f51\u7edc\u8fde\u63a5\u4f1a\u77ed\u6682\u4e2d\u65ad\u3002\u786e\u5b9a\u7ee7\u7eed\uff1f"
        if ConfirmDialog("\u91cd\u65b0\u8fde\u63a5\u7f51\u5361",text,self).exec()==ConfirmDialog.Accepted and self.offer:
            self.broker.request(self.offer,identity,reconnect=True)

    def _progress(self,key,value):
        if key==self.offer and isinstance(value,dict) and isinstance(value.get('network_recovery'),dict):
            self.panel.update_payload(value['network_recovery'])
            if self.waiting_for_restart:self.ready.setEnabled(not value['network_recovery'].get('busy'))

    def _release(self,*_):
        if self.offer:
            self.broker.release(self.offer)
            self.offer=None
            self.progress_signal.disconnect(self._progress)
