"""Connection-bound policy cards, previews, and asynchronous task lifecycle."""
from PySide6.QtWidgets import QLabel,QHBoxLayout,QAbstractButton
from modules.main_pages.packet_capture.ui_helpers import create_capture_card
from modules.main_pages.page_action_buttons import create_page_text_button,set_page_action_enabled
from modules.common_widgets.common_dialogs import MessageDialog
from .router_policy_dialogs import TITLES,InitialPolicyDialog,PolicyReviewDialog,show_details
from .router_task_progress import RouterTaskProgressDialog


class RouterPolicyActions:
    def __init__(self,page):
        self.page=page;self.pending={};self.buttons={};self.disposed=False;self.modal=False
        self.dialogs={}
        signal=getattr(page.manager,"progress",None)
        if signal is not None:signal.connect(self._progress)
        signal=getattr(page.manager,"policyStatusChanged",None)
        if signal is not None:signal.connect(self._status_changed)

    @property
    def active(self):return bool(self.pending) or self.modal

    def create_cards(self,body,icon_root):
        descriptions={
            "initial":"\u9996\u6b21\u4f7f\u7528\u65f6\u7edf\u4e00\u8bbe\u7f6e\uff1b\u6267\u884c\u524d\u9009\u62e9\u9879\u76ee\u5e76\u9884\u89c8\u5f71\u54cd\u3002",
            "boot":"\u51b3\u5b9a\u4ee5\u540e\u5f00\u673a\u662f\u5426\u81ea\u52a8\u8fde\u63a5\u4fdd\u5b58\u7684\u4e0a\u7ea7 Wi-Fi\u3002",
            "station":"\u4e00\u6b21\u66f4\u6362\u4e0a\u7ea7\u8fde\u63a5\u7684\u540d\u79f0\u548c\u65e0\u7ebf\u5730\u5740\uff0c\u4fdd\u7559\u5bc6\u7801\u3002",
            "ap":"\u4e00\u6b21\u66f4\u6362\u81ea\u5efa\u70ed\u70b9\u540d\u79f0\u548c\u65e0\u7ebf\u5730\u5740\uff0c\u4fdd\u7559\u5bc6\u7801\u3002"}
        icons={"initial":"System Shield.png","boot":"System Start.png","station":"System WiFi.png","ap":"System Router.png"}
        cards=[]
        for kind in ("initial","boot","station","ap"):
            card,layout=create_capture_card(body,TITLES[kind],descriptions[kind],icon_path=icon_root/icons[kind],actions_at_bottom=True)
            button=create_page_text_button(card,"\u6253\u5f00\u8bbe\u7f6e" if kind=="initial" else "\u5207\u6362\u7b56\u7565" if kind=="boot" else "\u66f4\u6362",descriptions[kind])
            button.clicked.connect(lambda checked=False,k=kind:self.start(k))
            self.buttons[kind]=button;layout.addWidget(button)
            if kind!="boot":self.add_details(card,layout,kind)
            cards.append(card)
        return tuple(cards)

    def add_details(self,card,layout,kind):
        button=create_page_text_button(card,"\u8be6\u60c5","\u67e5\u770b\u8fd9\u9879\u529f\u80fd\u4f1a\u505a\u4ec0\u4e48\u3001\u6709\u4ec0\u4e48\u5f71\u54cd\u3002")
        button.clicked.connect(lambda:show_details(kind,self.page))
        row=QHBoxLayout()
        row.setContentsMargins(0,0,0,0)
        row.setSpacing(8)
        for index in reversed(range(layout.count())):
            widget=layout.itemAt(index).widget()
            if isinstance(widget,QAbstractButton):
                layout.removeWidget(widget)
                row.insertWidget(0,widget,1)
        row.addWidget(button)
        layout.addLayout(row)
        return button

    def _identity(self):
        ssh=self.page.manager.manager.ssh
        return ssh._client,ssh.host_fingerprint

    def _current(self,identity):
        return self.page.manager.manager.ssh.connected and identity==self._identity()

    def _status_changed(self,*_):self.page._sync()

    def sync(self,available):
        for button in self.buttons.values():set_page_action_enabled(button,available)
        status=getattr(self.page.manager,"policy_status",None)
        state=status.get("boot_state") if isinstance(status,dict) else None
        ssh=self.page.manager.manager.ssh
        text="\u672a\u767b\u5f55" if not ssh.connected else "\u72b6\u6001\u672a\u77e5" if state is None else "\u5df2\u5f00\u542f" if state else "\u5df2\u5173\u95ed"
        current=getattr(self.page.manager,"current",None)
        if state is None and ssh.connected and current and current[1] in {"policy_status","policy_read"}:
            text="\u8bfb\u53d6\u4e2d"
        self.buttons["boot"].setText(text)
        self.buttons["boot"].setToolTip("\u70b9\u51fb\u540e\u91cd\u65b0\u6838\u5bf9\u771f\u5b9e\u72b6\u6001\uff0c\u9884\u89c8\u5e76\u786e\u8ba4\u5207\u6362\u3002")

    def start(self,kind):
        page=self.page
        if self.active or page.capture_service.is_busy or not page.manager.manager.ssh.connected:return
        try:
            identity=self._identity()
            key=page.manager.submit("policy_read")
            self.pending[key]=("read",kind,identity)
            self._show_progress(key,kind)
            page.status_label.setText("\u6b63\u5728\u8bfb\u53d6\u8def\u7531\u5668\u771f\u5b9e\u914d\u7f6e\uff0c\u5c1a\u672a\u4fee\u6539\u3002")
        except Exception as exc:self._show_error(str(exc))
        page._sync()

    def completed(self,key,result):
        item=self.pending.pop(key,None)
        if item is None:return False
        self._finish_progress(key)
        if self.disposed:return True
        phase,kind,identity=item
        page=self.page
        self.modal=True;page._sync()
        try:
            if phase=="apply":
                if "boot_state" in result and self._current(identity):
                    page.manager.policy_status=dict(boot_state=result["boot_state"])
                page.status_label.setText(result["note"])
                dialog=MessageDialog("\u64cd\u4f5c\u7ed3\u679c",result["note"],page)
                try:dialog.exec()
                finally:dialog.deleteLater()
                if page.manager.manager.ssh.connected and self._current(identity):
                    key=page.manager.submit("policy_status");self.pending[key]=("read","read",identity)
                return True
            if not self._current(identity):
                page.status_label.setText("\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u6253\u5f00\u8bbe\u7f6e\u3002");return True
            if phase=="read":
                if kind=="read":
                    page.status_label.setText("\u5df2\u8bfb\u53d6\u5f53\u524d\u5f00\u673a\u8fde\u63a5\u7b56\u7565\u3002");return True
                options={}
                if kind=="initial":
                    dialog=InitialPolicyDialog(result,page)
                    try:
                        if dialog.exec()!=dialog.Accepted:return True
                        options=dialog.values()
                    finally:dialog.deleteLater()
                elif kind=="ap":
                    from .ap_batch_dialog import AccessPointBatchDialog
                    dialog=AccessPointBatchDialog(result,page)
                    try:
                        if dialog.exec()!=dialog.Accepted:return True
                        options=dialog.values()
                    finally:dialog.deleteLater()
                elif kind=="boot":
                    state=result.get("boot_state")
                    if state is None:raise RuntimeError("\u5f53\u524d\u7b56\u7565\u672a\u77e5\u6216\u5b58\u5728\u5176\u4ed6\u811a\u672c\u51b2\u7a81\uff0c\u672a\u4fee\u6539\u3002")
                    options=dict(enabled=not state)
                if not self._current(identity):return True
                key=page.manager.submit("policy_preview",kind=kind,options=options)
                self.pending[key]=("preview",kind,identity)
                self._show_progress(key,kind)
                page.status_label.setText("\u6b63\u5728\u6838\u5bf9\u8bbe\u5907\u80fd\u529b\u5e76\u751f\u6210\u9884\u89c8\u3002")
            elif phase=="preview":
                dialog=PolicyReviewDialog(result,page)
                try:
                    if dialog.exec()!=dialog.Accepted:return True
                    backup_requested=dialog.backup_requested
                finally:dialog.deleteLater()
                if not self._current(identity):return True
                path,expected=None,None
                if backup_requested:
                    picked=page.maintenance_actions._destination(before=True)
                    if picked is None or not self._current(identity):return True
                    path,expected=picked
                key=page.manager.submit("policy_apply",review=result,backup_path=path,backup_expected=expected)
                self.pending[key]=("apply",kind,identity)
                self._show_progress(key,kind)
                page.status_label.setText("正在备份并应用设置……" if backup_requested else "正在应用设置……")
        except Exception as exc:self._show_error(str(exc))
        finally:self.modal=False;page._sync()
        return True

    def _show_progress(self,key,kind):
        progress=RouterTaskProgressDialog(TITLES[kind],lambda:self.page.manager.cancel(key),self.page)
        self.dialogs[key]=progress;progress.show()

    def _show_error(self,message):
        self.page.status_label.setText(message)
        dialog=MessageDialog("未能完成设置",message,self.page)
        try:dialog.exec()
        finally:dialog.deleteLater()

    def _progress(self,key,value):
        dialog=self.dialogs.get(key)
        if dialog is not None:dialog.update_progress(value)

    def _finish_progress(self,key):
        dialog=self.dialogs.pop(key,None)
        if dialog is not None:dialog.finish()

    def failed(self,key,message):
        if self.pending.pop(key,None) is None:return False
        self._finish_progress(key)
        if not self.disposed:self._show_error(message);self.page._sync()
        return True

    def dispose(self):
        self.disposed=True
        for key in tuple(self.pending):self.page.manager.cancel(key)
        self.pending.clear()
        for key in tuple(self.dialogs):self._finish_progress(key)
        for name,slot in (("progress",self._progress),("policyStatusChanged",self._status_changed)):
            signal=getattr(self.page.manager,name,None)
            if signal is not None:signal.disconnect(slot)
