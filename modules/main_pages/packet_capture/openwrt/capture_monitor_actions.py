"""Manual temporary capture interface review, normal cleanup and force cleanup."""
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QCheckBox, QHeaderView, QLabel
from modules.ui_scale_manager import UiScaleManager
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.main_pages.page_action_buttons import create_page_text_button, set_page_action_enabled
from .orphan_review_dialog import OrphanReviewDialog
from modules.user_errors import user_error

TITLE="\u4e34\u65f6\u6293\u5305\u63a5\u53e3\u6e05\u7406"


class MonitorReviewDialog(OrphanReviewDialog):
    def __init__(self,review,parent=None):
        super().__init__(review,parent,title=TITLE,
            header_labels=("\u63a5\u53e3","\u6765\u6e90","\u68c0\u67e5\u7ed3\u679c"),
            description="\u67e5\u770b\u626b\u63cf\u3001\u6293\u5305\u548c\u81ea\u52a8\u6293\u5305\u521b\u5efa\u7684\u4e34\u65f6\u76d1\u542c\u63a5\u53e3\uff0c\u52fe\u9009\u540e\u6e05\u7406\u3002",
            footer_text="\u6e05\u7406\u524d\u4f1a\u518d\u6b21\u68c0\u67e5\u3002\u4ec5\u5220\u9664\u6240\u9009\u4e34\u65f6\u63a5\u53e3\uff0c\u4e0d\u91cd\u8f7d\u7f51\u7edc\u3002",
            source_labels={"capture":"\u626b\u63cf / \u5b9a\u5411\u6293\u5305", "auto":"\u81ea\u52a8\u6293\u5305\uff08\u6309\u540d\u79f0\u8bc6\u522b\uff09"},
            reasons={"busy":"\u68c0\u6d4b\u5230\u5360\u7528\uff0c\u53ef\u9009\u62e9\u5f3a\u5236\u6e05\u7406", "protected":"\u5f52\u5c5e\u6216\u72b6\u6001\u672a\u786e\u8ba4\uff0c\u4fdd\u7559"},
            result_text="\u672a\u53d1\u73b0\u5360\u7528\uff0c\u53ef\u6e05\u7406")
        self.tree.header().setSectionResizeMode(0,QHeaderView.Fixed)
        self.tree.setColumnWidth(0,UiScaleManager.for_widget(self).px(185))
        self.review=review
        self.force_box=QCheckBox("\u5f3a\u5236\u6e05\u7406\uff08\u8df3\u8fc7\u5360\u7528\u5224\u65ad\uff09",self)
        self.force_box.setToolTip("\u53ef\u80fd\u4e2d\u65ad\u5176\u4ed6\u7a0b\u5e8f\u6b63\u5728\u4f7f\u7528\u7684\u6240\u9009\u63a5\u53e3\u3002")
        self.content_layout.addWidget(self.force_box)
        self.force_box.toggled.connect(self._force_changed)
        self.capture_service=getattr(parent,"capture_service",None)
        self.busy_hint=QLabel("\u6293\u5305\u4efb\u52a1\u6b63\u5728\u8fd0\u884c\uff0c\u5f53\u524d\u53ef\u67e5\u770b\uff1b\u8bf7\u5148\u505c\u6b62\u4efb\u52a1\u518d\u6e05\u7406\u3002",self)
        self.busy_hint.setObjectName("settingsHint")
        self.busy_hint.setWordWrap(True)
        self.content_layout.addWidget(self.busy_hint)
        self.tree.itemChanged.connect(self._sync_cleanup)
        if self.capture_service is not None:self.capture_service.activityChanged.connect(self._sync_cleanup)
        self._sync_cleanup()

    def _sync_cleanup(self,*_):
        busy=self.capture_service is not None and self.capture_service.is_busy
        self.busy_hint.setVisible(busy)
        self.clean_button.setEnabled(not busy and bool(self.selected()))

    def _force_changed(self,enabled):
        for i,row in enumerate(self.review['rows']):
            item=self.tree.topLevelItem(i)
            selectable=row['eligible'] or (enabled and row.get('force_eligible',False))
            if selectable:
                item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
                if item.checkState(0)!=Qt.Checked:item.setCheckState(0,Qt.Unchecked)
            else:
                item.setCheckState(0,Qt.Unchecked)
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
        self._sync_cleanup()


class CaptureMonitorActions(QObject):
    def __init__(self,page,container):
        super().__init__(page)
        self.page=page
        self.service=page.service.setup
        self.waiting=False
        self.connection=None
        self.preview_key=None
        self.dialog=None
        self.button=create_page_text_button(container,"\u68c0\u67e5\u5e76\u6e05\u7406", "\u5148\u67e5\u770b\u63a5\u53e3\u72b6\u6001\uff0c\u652f\u6301\u666e\u901a\u6e05\u7406\u548c\u5f3a\u5236\u6e05\u7406\u3002")
        container.layout().addWidget(self.button)
        self.button.clicked.connect(self.start)
        self.service.completed.connect(self._completed)
        self.service.snapshotChanged.connect(self.sync)
        page.manager.completed.connect(self._preview_completed)
        page.manager.failed.connect(self._preview_failed)

    def sync(self,*_):
        set_page_action_enabled(self.button,not self.waiting and self.dialog is None and not self.page.config_actions.active and not self.page.maintenance_actions.active and not self.page.policy_actions.active and self.page._password_key is None)

    def start(self):
        from .router_login_prompt import require_router_login
        if not require_router_login(self.page,self.page.service.snapshot.connected):return
        if self.waiting:return
        self.connection=self.service._connection_identity()
        try:
            self.preview_key=self.page.manager.submit("capture_monitor_preview")
            self.waiting=True
        except (ValueError,RuntimeError) as exc:
            self.page._message(user_error(exc), "warning")
        self.sync()

    def _preview_completed(self,key,value):
        if key!=self.preview_key:return
        self.preview_key=None
        from modules.capture_services.openwrt.wireless_setup_models import WirelessTaskResult
        self._completed(WirelessTaskResult("capture_monitor_preview",value))

    def _preview_failed(self,key,message):
        if key!=self.preview_key:return
        self.preview_key=None
        self.waiting=False
        self.page._message(message,"warning")
        self.sync()

    def _current(self):
        return self.page.service.snapshot.connected and self.service._connection_identity() is self.connection

    def _completed(self,result):
        if result.action not in {"capture_monitor_preview","capture_monitor_cleanup"} or not self.waiting:return
        self.waiting=False
        if result.error or not self._current():
            self.sync();return
        if result.action=="capture_monitor_cleanup":
            self.page._message("\u5df2\u6e05\u7406\u4e34\u65f6\u6293\u5305\u63a5\u53e3\uff1a"+"\u3001".join(result.value['removed']),"success")
            self.page.service.refresh()
        elif not result.value['rows']:
            dialog=MessageDialog(TITLE,"\u6ca1\u6709\u53d1\u73b0\u9057\u7559\u7684\u4e34\u65f6\u6293\u5305\u63a5\u53e3\u3002",self.page)
            self._show_dialog(dialog)
        else:
            review=result.value
            dialog=MonitorReviewDialog(review,self.page)
            def accepted(code):
                if code==dialog.Accepted:
                    names=dialog.selected();force=dialog.force_box.isChecked()
                    if names:self._confirm(review,names,force)
            self._show_dialog(dialog,accepted)
        self.sync()

    def _show_dialog(self,dialog,callback=None):
        self.dialog=dialog
        def finished(code):
            if self.dialog is dialog:self.dialog=None
            try:
                if callback is not None:callback(code)
            finally:
                dialog.deleteLater()
                self.sync()
        dialog.finished.connect(finished)
        dialog.open()

    def _confirm(self,review,names,force):
        text="\u5220\u9664\u4ee5\u4e0b\u4e34\u65f6\u63a5\u53e3\uff1a\n"+"\n".join(names)
        if force:text+="\n\n\u5f3a\u5236\u6e05\u7406\u4f1a\u8df3\u8fc7\u5360\u7528\u5224\u65ad\uff0c\u53ef\u80fd\u4e2d\u65ad\u5176\u4ed6\u7a0b\u5e8f\u4f7f\u7528\u8fd9\u4e9b\u63a5\u53e3\u3002"
        if any(r.get('legacy') for r in review['rows'] if r['section'] in names):
            text+="\n\n\u6240\u9009 hcxmon \u63a5\u53e3\u5305\u542b\u672a\u767b\u8bb0\u5f52\u5c5e\u7684\u65e7\u63a5\u53e3\uff0c\u8bf7\u786e\u8ba4\u5b83\u662f\u4e4b\u524d\u81ea\u52a8\u6293\u5305\u7559\u4e0b\u7684\u3002"
        confirm=ConfirmDialog("\u786e\u8ba4\u5f3a\u5236\u6e05\u7406" if force else TITLE,text,self.page,yes_text="\u5f3a\u5236\u6e05\u7406" if force else "\u6e05\u7406\u6240\u9009",no_text="\u53d6\u6d88")
        def confirmed(code):
            if code==confirm.Accepted and self._current():
                self.waiting=bool(self.service.cleanup_capture_monitors(review,names,force=force))
                if not self.waiting:self.page._message("\u8bf7\u5148\u505c\u6b62\u6b63\u5728\u8fd0\u884c\u7684\u4efb\u52a1\uff0c\u518d\u91cd\u65b0\u68c0\u67e5\u3002","warning")
        self._show_dialog(confirm,confirmed)

    def dispose(self):
        if self.dialog is not None:self.dialog.reject()
        self.page.manager.completed.disconnect(self._preview_completed)
        self.page.manager.failed.disconnect(self._preview_failed)
        if self.preview_key:self.page.manager.cancel(self.preview_key)
        self.service.completed.disconnect(self._completed)
        self.service.snapshotChanged.disconnect(self.sync)
