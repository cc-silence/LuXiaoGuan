"""Shared dependency consent and readiness banner for router settings."""
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget,QHBoxLayout,QLabel
from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.main_pages.page_action_buttons import create_page_text_button,set_page_action_enabled
from modules.ui_scale_manager import UiScaleManager


def install_management_dependencies(page,service,report):
    readiness=getattr(service,'management_readiness',None)
    missing=getattr(readiness,'missing',()) or ('lua','libuci-lua','libubus-lua','luci-lib-jsonc')
    dialog=ConfirmDialog('\u5b89\u88c5\u7f3a\u5c11\u4f9d\u8d56',
        '\u5e38\u7528\u8bbe\u7f6e\u548c\u4fe1\u53f7\u7ba1\u7406\u9700\u8981\u4ee5\u4e0b\u7ec4\u4ef6\uff1a\n\n'+'\u3001'.join(missing)
        +'\n\n\u786e\u8ba4\u540e\u4f7f\u7528\u73b0\u6709\u4f9d\u8d56\u5b89\u88c5\u6d41\u7a0b\uff0c\u53ea\u5b89\u88c5\u5b9e\u9645\u7f3a\u5c11\u7684\u7ec4\u4ef6\u5e76\u590d\u6d4b\u3002\u8def\u7531\u5668\u65e0\u6cd5\u8054\u7f51\u65f6\uff0c\u53ef\u7531\u8fd9\u53f0\u7535\u8111\u4e0b\u8f7d\u540e\u4f20\u5165\u5b89\u88c5\u3002',page,
        yes_text='\u786e\u8ba4\u5b89\u88c5',no_text='\u53d6\u6d88')
    identity=readiness.binding() if readiness is not None else None
    try:accepted=dialog.exec()==ConfirmDialog.Accepted
    finally:dialog.deleteLater()
    if not accepted:return
    if readiness is not None and (identity is None or readiness.binding()!=identity):
        report('\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u4f9d\u8d56\u540e\u518d\u5b89\u88c5\u3002','warning')
        return
    if not service.install_dependencies():
        report('\u4f9d\u8d56\u5b89\u88c5\u672a\u5f00\u59cb\uff0c\u8bf7\u786e\u8ba4\u5df2\u767b\u5f55\u5e76\u7b49\u5f85\u5176\u4ed6\u4efb\u52a1\u7ed3\u675f\u3002','warning')


class ManagementDependencyBanner(QObject):
    def __init__(self,page,layout,*,show_checking=True):
        super().__init__(page)
        self.page=page
        self.show_checking=show_checking
        self.readiness=getattr(page.service,'management_readiness',None)
        self.row=QWidget(page)
        row=QHBoxLayout(self.row)
        UiScaleManager.for_widget(page).bind_layout(row,margins=(12,4,12,4),spacing=8)
        self.label=QLabel(self.row);self.label.setWordWrap(True);self.label.setObjectName('captureDescription')
        self.button=create_page_text_button(self.row,'\u5b89\u88c5\u7f3a\u5c11\u4f9d\u8d56','\u5b89\u88c5\u540e\u4f1a\u81ea\u52a8\u590d\u6d4b\u4f9d\u8d56\u3002')
        row.addWidget(self.label,1);row.addWidget(self.button)
        layout.addWidget(self.row)
        self.button.clicked.connect(self._clicked)
        if self.readiness is not None:self.readiness.changed.connect(page._sync)
        self.sync()

    def enter(self):
        if self.readiness is not None:self.readiness.request()

    def sync(self):
        state=self.readiness
        if state is None:self.row.hide();return
        connected=state.binding() is not None
        missing=state.missing
        visible=connected and ((self.show_checking and state.checking) or bool(missing) or bool(state.error) or state.probe is not None and not state.ready)
        self.row.setVisible(visible)
        install=bool(missing)
        self.button.setText('\u5b89\u88c5\u7f3a\u5c11\u4f9d\u8d56' if install else '\u91cd\u65b0\u68c0\u67e5\u4f9d\u8d56')
        self.button.setVisible(not state.checking and bool(missing or state.error))
        can_install=not install or state.probe is not None and state.probe.get('root',False)
        set_page_action_enabled(self.button,connected and not self.page.capture_service.is_busy and can_install)
        if self.button.property('needsDependencyInstall')!=install:
            self.button.setProperty('needsDependencyInstall',install)
            self.button.style().unpolish(self.button);self.button.style().polish(self.button)
        if state.install_session is not None:text='\u6b63\u5728\u5b89\u88c5\u7f3a\u5c11\u4f9d\u8d56\uff0c\u5b8c\u6210\u540e\u81ea\u52a8\u68c0\u67e5\u3002'
        elif state.checking:text='\u6b63\u5728\u68c0\u67e5\u5e38\u7528\u8bbe\u7f6e\u6240\u9700\u4f9d\u8d56\u2026'
        elif missing:text='\u7f3a\u5c11\u4f9d\u8d56\uff1a'+'\u3001'.join(missing)
        else:text=state.error or '\u914d\u7f6e\u7ba1\u7406\u73af\u5883\u672a\u5c31\u7eea\uff0c\u8bf7\u68c0\u67e5 root \u6743\u9650\u548c sha256sum \u5de5\u5177\u3002'
        if state.probe is not None and not state.probe.get('root',False):text+='\u3002\u5f53\u524d\u767b\u5f55\u7f3a\u5c11 root \u7ba1\u7406\u6743\u9650\u3002'
        self.label.setText(text)

    @property
    def blocked(self):
        return self.readiness is not None and self.readiness.probe is not None and not self.readiness.ready

    def _clicked(self):
        if self.readiness is None:return
        if self.readiness.missing:install_management_dependencies(self.page,self.page.service,self.page._message)
        else:self.readiness.request()

    def dispose(self):
        if self.readiness is not None:self.readiness.changed.disconnect(self.page._sync)
