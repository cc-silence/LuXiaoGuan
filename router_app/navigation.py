"""主窗口左侧导航和内嵌设置页面的所有者。"""
from .identity import DISPLAY_NAME
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QLabel
from modules.common_widgets.navigation_button import NavigationButton
from modules.config_manager import BASE_DIR
from modules.app_settings import is_light_theme
from modules.icon_utils import get_theme_icon


NAV_ITEMS = (
    ("login", "btn_packet_capture", "路由器登录", str(BASE_DIR / 'images/icons/cil-wifi-signal-4.png')),
    ("common", "btn_common_settings", "常用设置", str(BASE_DIR / 'images/icons/cil-options.png')),
    ("signal", "btn_signal", "信号管理", str(BASE_DIR / 'images/icons/cil-rss.png')),
    ("files", "btn_files", "文件管理", str(BASE_DIR / 'images/icons/cil-folder.png')),
    ("terminal", "btn_terminal", "路由器终端", str(BASE_DIR / 'images/icons/cil-terminal.png')),
    ("auto_capture", "btn_auto_capture", "自动抓包", str(BASE_DIR / 'images/icons/cil-media-play.png')),
    ("capture", "btn_capture", "常规抓包", str(BASE_DIR / 'images/icons/cil-wifi-signal-2.png')),
)


class RouterNavigation(QWidget):
    """Lazy embedded settings navigation; completed edits stay saved on leaving."""
    def __init__(self, owner, router_page):
        super().__init__(owner.window_frame)
        self.owner = owner
        self.router_page = router_page
        self.openwrt_tab = getattr(router_page, "openwrt_tab", None)
        config = getattr(getattr(owner, "runtime", None), "config", None)
        if config is not None:
            val = config.get("ui", "sidebar_expanded", fallback="true")
            self.expanded = str(val).strip().lower() in ("true", "1", "yes", "on")
        else:
            self.expanded = True
        self.settings_controller = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = QFrame(self)
        self.sidebar.setObjectName('leftMenuBg')
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(0, 4, 0, 4)
        side.setSpacing(0)

        self.toggle_button = self._button(self.sidebar, 'toggleButton', '展开或收起导航', self.toggle)
        side.addWidget(self.toggle_button)

        top = QWidget(self.sidebar)
        top.setObjectName('topMenu')
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        self.page_buttons = {}
        for key, btn_name, label, icon in NAV_ITEMS:
            btn = self._button(top, btn_name, label, lambda k=key: self.show_page(k))
            self.page_buttons[key] = (btn, icon)
            top_layout.addWidget(btn)

        self.router_button = self.page_buttons["login"][0]
        top_layout.addStretch()
        side.addWidget(top)

        bottom = QWidget(self.sidebar)
        bottom.setObjectName('bottomMenu')
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_button = self._button(bottom, 'settingBtn', '设置', owner.openSettingsWindow)
        bottom_layout.addWidget(self.settings_button)
        side.addWidget(bottom)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(router_page)
        self.stack.setContentsMargins(12, 12, 12, 12)

        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)

        if self.openwrt_tab is not None and hasattr(self.openwrt_tab, "pageChanged"):
            self.openwrt_tab.pageChanged.connect(lambda _p: self.refresh())

        self.refresh()

    def _button(self, parent, name, label, action):
        """按原导航样式创建入口，不创建隐藏业务页面。"""
        button = NavigationButton(parent)
        button.setObjectName(name)
        button.setProperty('navigationLabel', label)
        button.setToolTip(label)
        button.setCursor(Qt.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False: action())
        return button

    def toggle(self):
        """展开或收起导航，不重建页面。"""
        self.expanded = not self.expanded
        self.refresh()
        self._save_expanded_state()

    def _save_expanded_state(self):
        """将侧边栏展开状态持久化至配置文件。"""
        runtime = getattr(self.owner, "runtime", None)
        config_mgr = getattr(self.owner, "config_manager", None)
        if runtime is not None and config_mgr is not None and hasattr(runtime, "config"):
            runtime.config.set("ui", "sidebar_expanded", "true" if self.expanded else "false")
            config_mgr.save(runtime.config)

    def refresh(self):
        """从原尺寸同步缩放、明暗图标和当前选中页面。"""
        scale = self.owner.scale_manager
        width = scale.NAV_EXPANDED_WIDTH if self.expanded else scale.CHROME_EDGE
        self.sidebar.setFixedWidth(scale.px(width))
        light = is_light_theme(self.owner.theme_name)

        btn_height = scale.NAV_BUTTON_HEIGHT if self.expanded else scale.NAV_BUTTON_SIZE

        # 展开/收起按钮
        scale.bind_metrics(self.toggle_button, fixed_height=scale.NAV_BUTTON_SIZE, fixed_width=width, icon_size=20)
        self.toggle_button.setIcon(get_theme_icon(':/icons/images/icons/icon_menu.png', light))

        # 功能导航按钮
        openwrt_tab = getattr(self.router_page, "openwrt_tab", None)
        current_page = getattr(openwrt_tab, "current_page_name", "login") if openwrt_tab else "login"
        in_settings = (self.settings_controller is not None and
                       self.stack.currentWidget() is self.settings_controller)

        for key, (button, icon) in self.page_buttons.items():
            scale.bind_metrics(button, fixed_height=btn_height, fixed_width=width, icon_size=20)
            button.setText(button.property('navigationLabel') if self.expanded else '')
            button.setIcon(get_theme_icon(icon, light))
            selected = (key == current_page) and not in_settings
            if button.property('sidebarSelected') != selected:
                button.setProperty('sidebarSelected', selected)
                button.style().unpolish(button)
                button.style().polish(button)

        # 设置按钮
        scale.bind_metrics(self.settings_button, fixed_height=btn_height, fixed_width=width, icon_size=20)
        self.settings_button.setText(self.settings_button.property('navigationLabel') if self.expanded else '')
        self.settings_button.setIcon(get_theme_icon(':/icons/images/icons/icon_settings.png', light))
        if self.settings_button.property('sidebarSelected') != in_settings:
            self.settings_button.setProperty('sidebarSelected', in_settings)
            self.settings_button.style().unpolish(self.settings_button)
            self.settings_button.style().polish(self.settings_button)

        if self.settings_controller is not None:
            self.settings_controller.sync_appearance()

    def show_router(self):
        """Flush settings before returning to the resident router page."""
        self.show_page("login")

    def show_page(self, key):
        """Switch to a specific functional subpage on the resident router page."""
        if self.settings_controller is not None:
            self.settings_controller.reject()
        else:
            self.stack.setCurrentWidget(self.router_page)
        openwrt_tab = getattr(self.router_page, "openwrt_tab", None)
        if openwrt_tab is not None and hasattr(openwrt_tab, "show_page"):
            openwrt_tab.show_page(key)
        self.refresh()

    def show_settings(self, update=False):
        """Switch to one embedded settings page; keep the router page resident."""
        if self.settings_controller is None:
            from .settings import RouterSettings
            controller = RouterSettings(self.owner)
            self.settings_controller = controller
            self.owner.settings_window = controller
            controller.finished.connect(self._settings_finished)
            self.stack.addWidget(controller)
        controller = self.settings_controller
        if update:
            controller.pages.setCurrentIndex(2)
        controller.sync_appearance()
        self.stack.setCurrentWidget(controller)
        self.refresh()

    def _settings_finished(self, result):
        """Release the settings page after its edits have been flushed."""
        controller = self.settings_controller
        self.settings_controller = None
        self.owner.settings_window = None
        self.stack.setCurrentWidget(self.router_page)
        self.stack.removeWidget(controller)
        controller.deleteLater()
        self.refresh()
