"""\u72ec\u7acb\u4e3b\u7a97\u53e3\uff1b\u516d\u4e2a\u4e1a\u52a1\u9875\u9762\u76f4\u63a5\u4f7f\u7528\u5b8c\u6574\u526f\u672c\u3002"""
from .identity import DISPLAY_NAME, WINDOW_TITLE
from modules.release_identity import APPLICATION_ICON
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget
from modules.common_widgets.frameless_dialog import FramelessDialog
from .chrome import RouterChrome
from .navigation import RouterNavigation
from modules.config_manager import BASE_DIR
from .appearance import build_main_stylesheet
from modules.ui_scale_manager import UiScaleManager
from modules.main_pages.packet_capture_page import PacketCapturePage


class RouterWindow(FramelessDialog):
    """\u4e3a\u539f\u8def\u7531\u9875\u63d0\u4f9b\u8bbe\u7f6e\u3001\u4e3b\u9898\u548c\u9000\u51fa\u5165\u53e3\u3002"""
    def __init__(self, runtime, version, *, initial_screen=None):
        super().__init__(DISPLAY_NAME, inherit_parent_stylesheet=False, window_controls=False)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint | Qt.WindowSystemMenuHint)
        from .window_geometry import startup_screen
        screen = initial_screen or startup_screen()
        if screen is not None:
            self.setScreen(screen)
        self.app_version = version
        self.runtime = runtime
        runtime.main_window = self
        self.display_privacy = runtime.display_privacy
        self.config_manager = runtime.config_manager
        self.settings_window = None
        self._disposed = False
        config = runtime.config
        self.theme_name = config.get("ui", "theme", fallback="dark.qss")
        self.scale_manager = UiScaleManager(config.get("ui", "scale", fallback="100"),
                                            config.get("ui", "font_family", fallback="default"))
        self._apply_font()
        self.setWindowTitle(DISPLAY_NAME)
        self.title_label.setText(WINDOW_TITLE)
        self.setWindowIcon(QIcon(str(BASE_DIR / APPLICATION_ICON)))
        self.scale_manager.publish(self)
        self.resize(1280, 850)
        self.setMinimumSize(960, 600)
        self.frame = self.window_frame
        layout = self.content_layout
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(6)
        self.button_layout.setContentsMargins(0,0,0,0)
        self.scale_manager.bind_layout(layout,margins=(0,0,0,0),spacing=6)
        self.scale_manager.bind_layout(self.root_layout,margins=(0,0,0,0),spacing=0)
        self.scale_manager.bind_layout(self.button_layout,margins=(0,0,0,0),spacing=0)
        self.chrome = RouterChrome(self)
        self.setProperty("settingsLightTheme", self.theme_name == "light.qss")
        self.frame.setProperty("settingsLightTheme", self.theme_name == "light.qss")
        self.setStyleSheet(self._shell_stylesheet(self.theme_name))
        self.chrome.refresh()
        self.packet_capture_page = PacketCapturePage(runtime.capture_service, self.frame,
            self.scale_manager, light_theme=self.theme_name == "light.qss")
        self.navigation = RouterNavigation(self,self.packet_capture_page)
        layout.addWidget(self.navigation,1)
        self.scale_manager.apply_bound_metrics(self)
        from PySide6.QtGui import QShortcut, QKeySequence
        from modules.platform_services.window_shell import show_system_menu
        self.system_menu_shortcut = QShortcut(QKeySequence("Alt+Space"), self)
        self.system_menu_shortcut.activated.connect(lambda: show_system_menu(self))
        self.chrome.refresh()
        self._appearance_key = (self.scale_manager.global_scale, self.scale_manager.font_family, self.theme_name)
        from .close_behavior import CloseBehavior
        self.close_behavior = CloseBehavior(self)
        from modules.desktop_shell.initial_window_geometry import apply_initial_window_geometry
        apply_initial_window_geometry(self)
        from .window_geometry import WindowGeometry
        self.geometry_policy = WindowGeometry(self)

    def apply_appearance(self, scale, family, theme):
        key = (UiScaleManager.clamp_global_scale(scale), family, theme)
        if getattr(self, "_appearance_key", None) == key:
            return
        updates = self.updatesEnabled()
        self.setUpdatesEnabled(False)
        try:
            with self.packet_capture_page.style_mgr.appearance_update():
                self._apply_appearance(scale, family, theme)
        finally:
            self.setUpdatesEnabled(updates)

    def _apply_appearance(self, scale, family, theme):
        """\u6240\u6709\u9875\u9762\u4ece\u539f\u59cb\u5c3a\u5bf8\u540c\u6b65\u7f29\u653e\u548c\u5b57\u4f53\uff0c\u4e0d\u91cd\u5efa\u670d\u52a1\u3002"""
        from PySide6.QtWidgets import QApplication
        key = (UiScaleManager.clamp_global_scale(scale), family, theme)
        if getattr(self, "_appearance_key", None) == key:
            return
        geometry_reference = self.geometry_policy.before_scale() if key[0] != self.scale_manager.global_scale else None
        self._appearance_key = key
        self.theme_name = theme
        metrics_changed = key[:2] != (self.scale_manager.global_scale, self.scale_manager.font_family)
        if metrics_changed:
            self.scale_manager = UiScaleManager(scale, family)
            self.scale_manager.publish(self)
            self._apply_font()
        self.setProperty("settingsLightTheme", theme == "light.qss")
        self.frame.setProperty("settingsLightTheme", theme == "light.qss")
        self.setStyleSheet(self._shell_stylesheet(theme))
        if metrics_changed:
            self.scale_manager.apply_bound_metrics(self)
        self.packet_capture_page.scale_manager = self.scale_manager
        for panel in self.packet_capture_page.info_panels:
            panel._scale_manager = self.scale_manager
        self.packet_capture_page.set_light_theme(theme == "light.qss", force=True)
        self.chrome.refresh()
        self.navigation.refresh()
        self.close_behavior.refresh_menu_theme()
        if geometry_reference is not None:
            self.geometry_policy.apply_scale(geometry_reference)

    def _shell_stylesheet(self, theme):
        """\u4ece\u539f\u4e3b\u9898\u548c\u6807\u9898\u680f\u7247\u6bb5\u751f\u6210\u6837\u5f0f\uff0c\u65e0\u754c\u9762\u91cd\u8bbe\u8ba1\u3002"""
        key = (self.scale_manager.global_scale, self.scale_manager.font_family, theme)
        cache = getattr(self, "_shell_style_cache", None)
        if cache is None:
            cache = self._shell_style_cache = {}
        if key not in cache:
            if len(cache) >= 8:
                cache.pop(next(iter(cache)))
            cache[key] = build_main_stylesheet(self.config_manager, theme, self.scale_manager)
        return cache[key]

    def accept(self):
        """\u4e3b\u7a97\u53e3\u5173\u95ed\u7edf\u4e00\u7ecf\u8fc7\u4efb\u52a1\u6536\u5c3e\u534f\u8bae\u3002"""
        self.close()

    def reject(self):
        """\u6807\u9898\u5173\u95ed\u6309\u94ae\u4e0d\u8df3\u8fc7\u4efb\u52a1\u6e05\u7406\u3002"""
        self.close()

    def showEvent(self, event):
        super().showEvent(event)
        from modules.platform_services.window_shell import enable_window_commands
        enable_window_commands(self)

    def keyPressEvent(self, event):
        """\u4fdd\u7559\u4e3b\u7a97\u53e3\u6309\u952e\u8bed\u4e49\uff0cEnter \u548c Esc \u4e0d\u89e6\u53d1\u5bf9\u8bdd\u6846\u63d0\u4ea4\u6216\u5173\u95ed\u3002"""
        QWidget.keyPressEvent(self,event)

    def _apply_font(self):
        """\u6784\u9020\u9875\u9762\u524d\u5e94\u7528\u5b57\u4f53\uff0c\u76f8\u540c\u5916\u89c2\u4e0d\u91cd\u590d\u89e6\u53d1\u5168\u5c40\u5237\u65b0\u3002"""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        key = (self.scale_manager.global_scale, self.scale_manager.font_family)
        if getattr(app, "_router_font_key", None) != key:
            self.scale_manager.apply_application_font(app)
            app._router_font_key = key

    def toggle_theme(self):
        """\u5207\u6362\u5e76\u4fdd\u5b58\u660e\u6697\u4e3b\u9898\u3002"""
        theme = "light.qss" if self.theme_name != "light.qss" else "dark.qss"
        self.apply_appearance(self.scale_manager.global_scale, self.scale_manager.font_family, theme)
        self.runtime.config.set("ui", "theme", theme)
        try:
            self.config_manager.save(self.runtime.config)
        except OSError as exc:
            from modules.common_widgets.common_dialogs import MessageDialog
            MessageDialog("\u4fdd\u5b58\u5931\u8d25", str(exc), self).exec()

    def openSettingsWindow(self, update=False):
        """\u5ef6\u8fdf\u521b\u5efa\u8bbe\u7f6e\u7a97\u53e3\uff0c\u5173\u95ed\u540e\u91ca\u653e\u63a7\u4ef6\u548c\u8ba2\u9605\u3002"""
        self.navigation.show_settings(update=update)

    def dispose(self):
        """\u4ec5\u5728\u540e\u53f0\u6536\u5c3e\u5b8c\u6210\u540e\u65ad\u5f00\u9875\u9762\u8ba2\u9605\u3002"""
        if not self._disposed:
            self._disposed = True
            if self.settings_window is not None:
                self.settings_window.reject()
            self.geometry_policy.dispose()
            self.close_behavior.dispose()
            self.packet_capture_page.dispose()

    def closeEvent(self, event):
        """\u6709\u4efb\u52a1\u65f6\u5148\u786e\u8ba4\u53d6\u6d88\uff0c\u5f02\u6b65\u6536\u5c3e\u5b8c\u6210\u624d\u9000\u51fa\u3002"""
        if self.runtime._ready_to_close:
            event.accept()
            return
        event.ignore()
        if self.runtime._exiting:
            return
        self.close_behavior.request_close()

    def request_exit(self):
        """显式退出先确认活动任务，再由运行时异步收尾。"""
        if self.runtime._exiting:
            return
        tasks = self.runtime.exit_tasks()
        if tasks:
            from modules.common_widgets.common_dialogs import ConfirmDialog
            message = "以下任务仍在进行：\n\n" + "\n".join("• " + label for label in tasks)
            message += "\n\n是否关闭软件？本机任务将停止并在清理完成后退出；路由器内保留的终端会话可能继续运行。"
            dialog = ConfirmDialog("退出软件", message, self)
            try:
                if dialog.exec() != ConfirmDialog.Accepted:
                    return
            finally:
                dialog.deleteLater()
        self.runtime.exit_application()
