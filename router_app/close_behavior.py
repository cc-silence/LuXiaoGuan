"""关闭按钮的配置策略和可恢复的托盘入口。"""
from .identity import DISPLAY_NAME
from copy import deepcopy
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QSystemTrayIcon
from modules.dialog_theme import create_themed_menu, context_menu_stylesheet
from modules.app_settings import is_light_theme
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog


def normalize_close_action(value):
    """无效配置与原版一致，回退到托盘。"""
    return value if value in ('ask', 'exit', 'tray') else 'tray'


class CloseBehavior(QObject):
    """只负责关闭决策；真正退出由窗口的任务守卫处理。"""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.tray = None
        self.menu = None
        self.show_tray()

    def request_close(self):
        """从已确认配置决定退出、隐藏或询问。"""
        window = self.window
        action = normalize_close_action(window.runtime.config.get('runtime', 'close_button_action', fallback='tray'))
        if action == 'ask':
            action = self._ask()
            if action is None:
                return
            config = deepcopy(window.runtime.config)
            config.set('runtime', 'close_button_action', action)
            try:
                window.config_manager.save(config)
            except (OSError, ValueError) as exc:
                MessageDialog('\u4fdd\u5b58\u5931\u8d25', str(exc), window).exec()
                return
            window.runtime.config = config
        if action == 'exit':
            window.request_exit()
        else:
            self.hide_to_tray()

    def _ask(self):
        """首次选择会记住；关闭询问框不执行动作。"""
        dialog = ConfirmDialog('\u5173\u95ed' + DISPLAY_NAME,
            '\u9009\u62e9\u5173\u95ed\u8f6f\u4ef6\u6216\u6700\u5c0f\u5316\u5230\u6258\u76d8\u7ee7\u7eed\u8fd0\u884c\u3002\n\u672c\u6b21\u9009\u62e9\u5c06\u4f5c\u4e3a\u4ee5\u540e\u7684\u9ed8\u8ba4\u884c\u4e3a\uff0c\u53ef\u5728\u5168\u5c40\u8bbe\u7f6e\u4e2d\u4fee\u6539\u3002', self.window)
        choice = {'value': None}
        for button, text, value in ((dialog.yes_button, '\u5173\u95ed\u8f6f\u4ef6', 'exit'),
                                    (dialog.no_button, '\u6700\u5c0f\u5316\u5230\u6258\u76d8', 'tray')):
            button.setText(text)
            button.clicked.disconnect()
            def select(_checked=False, value=value):
                choice['value'] = value
                dialog.accept()
            button.clicked.connect(select)
        dialog.exec()
        return choice['value']

    def hide_to_tray(self):
        """托盘不可用时退回任务栏，防止窗口无法找回。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.window.showMinimized()
            return
        self.show_tray()
        self.window.hide()

    def show_tray(self):
        """Keep one tray entry for the entire application lifetime."""
        if self.tray is None:
            self.tray = QSystemTrayIcon(self.window.windowIcon(), self)
            self.tray.setToolTip(DISPLAY_NAME)
            self.menu = create_themed_menu(self.window, is_light_theme(self.window.theme_name), rounded=True)
            self.menu.aboutToShow.connect(self.refresh_menu_theme)
            self.menu.addAction('\u663e\u793a' + DISPLAY_NAME, self.restore)
            self.menu.addAction('\u9000\u51fa\u8f6f\u4ef6', self.window.request_exit)
            self.tray.setContextMenu(self.menu)
            self.tray.activated.connect(self._activated)
        self.refresh_menu_theme()
        self.tray.show()

    def refresh_menu_theme(self):
        """独立菜单显式同步当前主题和缩放，不依赖隐藏窗口继承。"""
        if self.menu is None:
            return
        style = self.window.scale_manager.scale_stylesheet(
            context_menu_stylesheet(is_light_theme(self.window.theme_name)))
        if self.menu.styleSheet() != style:
            self.menu.setStyleSheet(style)

    def _activated(self, reason):
        """单击或双击托盘恢复窗口。"""
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore()

    def restore(self):
        """恢复主窗口并请求置前。"""
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def dispose(self):
        """服务收尾完成后释放托盘和菜单。"""
        if self.tray is not None:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None
        if self.menu is not None:
            self.menu.deleteLater()
            self.menu = None
