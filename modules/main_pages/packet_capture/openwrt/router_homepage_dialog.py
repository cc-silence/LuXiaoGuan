"""Standalone selection of the router web root landing page."""
from PySide6.QtWidgets import QWidget,QVBoxLayout
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons
from modules.capture_services.openwrt.router_policy_plan import PAGES
from .signal_setup_forms import choice,add_field,hint


def available_pages(snapshot):
    rows=[(PAGES[key][0],key) for key in snapshot.get('pages',[]) if key in PAGES]
    rows.sort(key=lambda row:row[1]!='wireless')
    return rows


class RouterHomepageDialog(FramelessDialog):
    def __init__(self,snapshot,parent=None):
        super().__init__('设置默认打开窗口',parent,inherit_parent_stylesheet=False)
        apply_common_dialog_theme(self,parent);self.set_scaled_size((540,260))
        self.content_layout.addWidget(hint('选择浏览器打开路由器地址后进入的管理页面。',self))
        body=QWidget(self);layout=QVBoxLayout(body)
        options=available_pages(snapshot)
        self.homepage=choice(body,options)
        add_field(layout,body,'默认打开的网页',self.homepage,'')
        self.content_layout.addWidget(body)
        if not options:self.content_layout.addWidget(hint('当前路由器未检测到可用的管理网页。',self))
        self.continue_button,cancel=create_dialog_ok_cancel_buttons(self,'保存','取消')
        self.continue_button.setEnabled(bool(options))
        self.continue_button.clicked.connect(self.accept);cancel.clicked.connect(self.reject)
        self.button_layout.addWidget(self.continue_button);self.button_layout.addWidget(cancel)
    def values(self):return dict(homepage=self.homepage.currentData())
