"""Persistent result guidance and explicitly requested local network recovery."""
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme,_create_dialog_button
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
        if self.waiting_for_restart:
            self.ready=QCheckBox("我已确认路由器重启完成，网线或 Wi-Fi 已连接",self)
            self.content_layout.addWidget(self.ready)
        plan['guide']=result['note']+'\n\n'+plan['guide']
        self.panel=LanRecoveryPanel(self)
        scale=UiScaleManager.for_widget(self)
        self.panel.guide.setMinimumHeight(scale.px(230))
        self.panel.guide.setMaximumHeight(scale.px(300))
        self.content_layout.addWidget(self.panel,1)
        if self.broker is not None and self.progress_signal is not None:
            self.offer=self.broker.create_offer(plan,ready=not self.waiting_for_restart)
            self.panel.update_payload(self.broker.payload(self.offer))
            self.progress_signal.connect(self._progress)
            self.panel.requested.connect(self._request)
            if self.waiting_for_restart:self.ready.toggled.connect(self._ready)
            broker,key=self.broker,self.offer
            self.destroyed.connect(lambda *_:broker.release(key))
        else:
            self.panel.update_payload(dict(plan,can_renew=False))
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

    def _progress(self,key,value):
        if key==self.offer and isinstance(value,dict) and isinstance(value.get('network_recovery'),dict):
            self.panel.update_payload(value['network_recovery'])
            if self.waiting_for_restart:self.ready.setEnabled(not value['network_recovery'].get('busy'))

    def _release(self,*_):
        if self.offer:
            self.broker.release(self.offer)
            self.offer=None
            self.progress_signal.disconnect(self._progress)
