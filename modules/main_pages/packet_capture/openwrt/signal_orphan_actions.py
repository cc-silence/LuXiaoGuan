"""Read-only orphan preview, user selection and final cleanup confirmation."""
from PySide6.QtCore import QObject
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.main_pages.page_action_buttons import create_page_text_button, set_page_action_enabled
from .orphan_review_dialog import OrphanReviewDialog
from .settings_impacts import ORPHAN_SAVE_WARNING


class OrphanInterfaceActions(QObject):
    def __init__(self,page,container):
        super().__init__(page)
        self.page=page
        self.service=getattr(page.service,"setup",None)
        self._waiting=False
        self._identity=None
        self.button=create_page_text_button(container,"开始检查",
            "\u53ea\u8bfb\u68c0\u67e5\u65e7\u4e2d\u7ee7\u7f51\u7edc\u63a5\u53e3\uff0c\u5148\u67e5\u770b\u4f9d\u636e\u518d\u9009\u62e9\u6e05\u7406\u3002")
        container.layout().addWidget(self.button)
        self.button.clicked.connect(self.review)
        page.service.snapshotChanged.connect(self._sync)
        if self.service is not None:
            self.service.snapshotChanged.connect(self._sync)
            self.service.completed.connect(self._completed)
        self._sync()

    def _sync(self,*_):
        state=self.page.service.snapshot
        supported=self.service is not None and hasattr(self.service,"orphan_preview")
        if self._waiting and (not state.connected or self.service._connection_identity() is not self._identity):
            self._waiting=False
        self.button.setVisible(True)
        self.button.setToolTip("\u5148\u68c0\u67e5\u518d\u9009\u62e9\u6e05\u7406\uff0c\u4e0d\u4f1a\u76f4\u63a5\u5220\u9664\u3002" if supported else "\u63a5\u53e3\u68c0\u67e5\u670d\u52a1\u5c1a\u672a\u5c31\u7eea\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u540e\u91cd\u8bd5\u3002")
        set_page_action_enabled(self.button, bool(supported and state.connected and not state.busy and not self.service.active and not self._waiting))

    def review(self):
        if not self.button.isEnabled():return
        self._identity=self.service._connection_identity()
        self._waiting=True
        if not self.service.orphan_preview():
            self._waiting=False
            self.page.status_label.setText("\u8bf7\u7b49\u5f85\u5f53\u524d\u4efb\u52a1\u7ed3\u675f\u540e\u91cd\u8bd5\u3002")
        self._sync()

    def _completed(self,result):
        if result.action!="orphan_preview" or not self._waiting:return
        self._waiting=False
        if not result.error and self._current():
            review=result.value
            if not review["rows"]:
                MessageDialog("\u9057\u7559\u63a5\u53e3\u68c0\u67e5","\u6ca1\u6709\u53d1\u73b0\u9700\u8981\u68c0\u67e5\u7684\u9057\u7559\u63a5\u53e3\u3002",self.page).exec()
            else:
                dialog=OrphanReviewDialog(review,self.page)
                try:
                    if dialog.exec()==dialog.Accepted:
                        selected=dialog.selected()
                        if selected:
                            confirm=ConfirmDialog("\u786e\u8ba4\u6e05\u7406\u9057\u7559\u63a5\u53e3",
                                "\u4ec5\u6e05\u7406\u4ee5\u4e0b\u5df2\u52fe\u9009\u63a5\u53e3\uff1a\n"+"\n".join(selected)+
                                "\n\n" + ORPHAN_SAVE_WARNING,self.page,
                                yes_text="\u6e05\u7406\u6240\u9009",no_text="\u53d6\u6d88")
                            try:
                                if confirm.exec()==confirm.Accepted:
                                    if not self._current():
                                        self.page.status_label.setText("\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u3002")
                                    elif not self.service.cleanup_orphans(review,selected):
                                        self.page.status_label.setText("\u6e05\u7406\u672a\u5f00\u59cb\uff0c\u8bf7\u7b49\u5f85\u4efb\u52a1\u7ed3\u675f\u540e\u91cd\u65b0\u68c0\u67e5\u3002")
                            finally:confirm.deleteLater()
                finally:dialog.deleteLater()
        self._sync()

    def _current(self):
        return self.page.service.snapshot.connected and self.service._connection_identity() is self._identity

    def dispose(self):
        self._waiting=False
        self.page.service.snapshotChanged.disconnect(self._sync)
        if self.service is not None:
            self.service.snapshotChanged.disconnect(self._sync)
            self.service.completed.disconnect(self._completed)
