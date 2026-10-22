"""\u4e3b\u7a97\u53e3\u5de6\u4fa7\u5bfc\u822a\u548c\u5185\u5d4c\u8bbe\u7f6e\u9875\u9762\u7684\u6240\u6709\u8005\u3002"""
from .identity import DISPLAY_NAME
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame,QWidget,QPushButton,QVBoxLayout,QHBoxLayout,QStackedWidget,QLabel,QSizeGrip
from modules.common_widgets.navigation_button import NavigationButton
from modules.config_manager import BASE_DIR
from modules.app_settings import is_light_theme
from modules.icon_utils import get_theme_icon


class RouterNavigation(QWidget):
    """Lazy embedded settings navigation; completed edits stay saved on leaving."""
    def __init__(self, owner, router_page):
        super().__init__(owner.window_frame)
        self.owner=owner
        self.router_page=router_page
        self.expanded=False
        self.settings_frame=None
        self.settings_controller=None
        layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        self.sidebar=QFrame(self);self.sidebar.setObjectName('leftMenuBg')
        side=QVBoxLayout(self.sidebar);side.setContentsMargins(0,4,0,4);side.setSpacing(0)
        self.toggle_button=self._button(self.sidebar,'toggleButton','\u5c55\u5f00\u6216\u6536\u8d77\u5bfc\u822a',self.toggle)
        side.addWidget(self.toggle_button)
        top=QWidget(self.sidebar);top.setObjectName('topMenu')
        top_layout=QVBoxLayout(top);top_layout.setContentsMargins(0,0,0,0);top_layout.setSpacing(0)
        self.router_button=self._button(top,'btn_packet_capture',DISPLAY_NAME,self.show_router)
        top_layout.addWidget(self.router_button);side.addWidget(top);side.addStretch()
        bottom=QWidget(self.sidebar);bottom.setObjectName('bottomMenu')
        bottom_layout=QVBoxLayout(bottom);bottom_layout.setContentsMargins(0,0,0,0)
        self.settings_button=self._button(bottom,'settingBtn','\u8bbe\u7f6e',owner.openSettingsWindow)
        bottom_layout.addWidget(self.settings_button);side.addWidget(bottom)
        self.stack=QStackedWidget(self);self.stack.addWidget(router_page)
        self.stack.setContentsMargins(12,12,12,12)
        layout.addWidget(self.sidebar)
        right=QWidget(self)
        right_layout=QVBoxLayout(right);right_layout.setContentsMargins(0,0,0,0);right_layout.setSpacing(0)
        right_layout.addWidget(self.stack,1)
        self.footer=QFrame(right);self.footer.setObjectName('bottomBar')
        footer_layout=QHBoxLayout(self.footer);footer_layout.setContentsMargins(4,0,0,0);footer_layout.setSpacing(4)
        footer_layout.addStretch()
        version=QLabel('v'+owner.app_version,self.footer);version.setObjectName('labelVersion')
        footer_layout.addWidget(version)
        self.footer_grip=QSizeGrip(self.footer);self.footer_grip.setFixedSize(20,20)
        footer_layout.addWidget(self.footer_grip)
        right_layout.addWidget(self.footer)
        layout.addWidget(right,1)
        self.refresh()

    def _button(self,parent,name,label,action):
        """\u6309\u539f\u5bfc\u822a\u6837\u5f0f\u521b\u5efa\u5165\u53e3\uff0c\u4e0d\u521b\u5efa\u9690\u85cf\u4e1a\u52a1\u9875\u9762\u3002"""
        button=NavigationButton(parent);button.setObjectName(name)
        button.setProperty('navigationLabel',label);button.setToolTip(label)
        button.setCursor(Qt.PointingHandCursor);button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False:action())
        return button

    def toggle(self):
        """\u5c55\u5f00\u6216\u6536\u8d77\u5bfc\u822a\uff0c\u4e0d\u91cd\u5efa\u9875\u9762\u3002"""
        self.expanded=not self.expanded;self.refresh()

    def refresh(self):
        """\u4ece\u539f\u5c3a\u5bf8\u540c\u6b65\u7f29\u653e\u3001\u660e\u6697\u56fe\u6807\u548c\u5f53\u524d\u9009\u4e2d\u9875\u9762\u3002"""
        scale=self.owner.scale_manager
        width=scale.NAV_EXPANDED_WIDTH if self.expanded else scale.CHROME_EDGE
        self.sidebar.setFixedWidth(scale.px(width))
        scale.bind_metrics(self.footer, fixed_height=26)
        scale.bind_metrics(self.footer_grip, fixed_size=(20,20))
        light=is_light_theme(self.owner.theme_name)
        specs=((self.toggle_button,':/icons/images/icons/icon_menu.png'),
               (self.router_button,str(BASE_DIR/'images/icons/cil-wifi-signal-4.png')),
               (self.settings_button,':/icons/images/icons/icon_settings.png'))
        for button,icon in specs:
            scale.bind_metrics(button,fixed_height=44,fixed_width=width,icon_size=20)
            button.setText(button.property('navigationLabel') if self.expanded and button is not self.toggle_button else '')
            button.setIcon(get_theme_icon(icon,light))
        for button,selected in ((self.router_button,self.stack.currentWidget() is self.router_page),
                                (self.settings_button,self.settings_controller is not None)):
            if button.property('sidebarSelected') != selected:
                button.setProperty('sidebarSelected',selected);button.style().unpolish(button);button.style().polish(button)
        if self.settings_controller is not None:self.settings_controller.sync_embedded_style()

    def show_router(self):
        """Flush settings before returning to the resident router page."""
        if self.settings_controller is not None:
            self.settings_controller.reject()
        else:
            self.stack.setCurrentWidget(self.router_page);self.refresh()

    def show_settings(self, update=False):
        """\u50cf\u539f\u8bbe\u7f6e\u9875\u5bbf\u4e3b\u4e00\u6837\u5d4c\u5165\u5185\u5bb9\u6846\uff0c\u4e0d\u663e\u793a\u72ec\u7acb\u5f39\u7a97\u3002"""
        if self.settings_controller is None:
            from .settings import RouterSettings
            controller=RouterSettings(self.owner)
            controller.title_bar.hide();controller.size_grip.hide()
            frame=controller.window_frame;frame.setObjectName("routerEmbeddedSettings");controller.root_layout.removeWidget(frame)
            frame.setParent(self.stack);controller._embedded_frame=frame
            self.settings_controller=controller;self.settings_frame=frame
            self.owner.settings_window=controller
            controller.finished.connect(self._settings_finished)
            self.stack.addWidget(frame)
        if update:self.settings_controller.pages.setCurrentIndex(2)
        self.stack.setCurrentWidget(self.settings_frame);self.settings_frame.show();self.refresh()

    def _settings_finished(self, result):
        """Release the settings view after a successful close."""
        controller,frame=self.settings_controller,self.settings_frame
        self.settings_controller=None;self.settings_frame=None;self.owner.settings_window=None
        self.stack.setCurrentWidget(self.router_page);self.stack.removeWidget(frame)
        frame.deleteLater();controller.deleteLater();self.refresh()
