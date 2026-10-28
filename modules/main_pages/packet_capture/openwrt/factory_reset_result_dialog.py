"""Factory-reset result and explicitly requested local network recovery."""
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme,_create_dialog_button
from modules.capture_services.lan_client_recovery import factory_reset_plan
from modules.ui_scale_manager import UiScaleManager
from .lan_recovery_panel import LanRecoveryPanel


class FactoryResetResultDialog(FramelessDialog):
    def __init__(self,result,inventory,owner,parent=None):
        super().__init__('恢复出厂：重新连接',parent,inherit_parent_stylesheet=False,window_controls=False)
        apply_common_dialog_theme(self,parent)
        self.set_scaled_size((620,480))
        self.broker=getattr(owner,'lan_recovery',None)
        self.progress_signal=getattr(owner,'configurationProgressChanged',None)
        self.offer=None
        plan=factory_reset_plan(inventory)
        plan['guide']=result['note']+'\n\n'+plan['guide']
        self.panel=LanRecoveryPanel(self)
        scale=UiScaleManager.for_widget(self)
        self.panel.guide.setMinimumHeight(scale.px(230))
        self.panel.guide.setMaximumHeight(scale.px(300))
        self.content_layout.addWidget(self.panel,1)
        if self.broker is not None and self.progress_signal is not None:
            self.offer=self.broker.create_offer(plan)
            self.panel.update_payload(self.broker.payload(self.offer))
            self.progress_signal.connect(self._progress)
            self.panel.requested.connect(self._request)
            broker,key=self.broker,self.offer
            self.destroyed.connect(lambda *_:broker.release(key))
        else:
            self.panel.update_payload(dict(plan,can_renew=False))
        self.close_button=_create_dialog_button('关闭',self,'settingsPrimaryButton')
        self.button_layout.addWidget(self.close_button)
        self.close_button.clicked.connect(self.accept)
        self.finished.connect(self._release)

    def _request(self,identity):
        if self.offer:self.broker.request(self.offer,identity)

    def _progress(self,key,value):
        if key==self.offer and isinstance(value,dict) and isinstance(value.get('network_recovery'),dict):
            self.panel.update_payload(value['network_recovery'])

    def _release(self,*_):
        if self.offer:
            self.broker.release(self.offer)
            self.offer=None
            self.progress_signal.disconnect(self._progress)
