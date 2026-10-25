"""\u590d\u7528\u516c\u5171\u65e0\u8fb9\u6846\u5916\u58f3\u548c\u539f\u6807\u9898\u680f\u56fe\u6807\u3002"""
from PySide6.QtCore import QObject, QEvent, Qt, QSignalBlocker
from PySide6.QtWidgets import QPushButton, QLabel
from modules.config_manager import BASE_DIR
from modules.app_settings import is_light_theme
from modules.icon_utils import get_theme_icon


class RouterChrome(QObject):
    """\u7ba1\u7406\u6807\u9898\u680f\u6309\u94ae\u548c\u7a97\u53e3\u72b6\u6001\uff0c\u4e0d\u6301\u6709\u4e1a\u52a1\u4efb\u52a1\u3002"""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.logo = QLabel(window.title_bar)
        self.logo.setObjectName('routerMainLogo')
        self.logo.setAlignment(Qt.AlignCenter)
        self.logo.setAttribute(Qt.WA_TransparentForMouseEvents)
        window.title_bar_layout.insertWidget(0, self.logo)
        window.scale_manager.bind_layout(window.title_bar_layout, margins=(0,0,8,0), spacing=10)
        self.settings_button = self._button('settingsAppBtn', self._toggle_settings)
        self.settings_button.setToolTip('\u8bbe\u7f6e')
        self.settings_button.setAccessibleName('\u8bbe\u7f6e')
        self.privacy_button = self._button('privacyToggleBtn', self._toggle_privacy)
        self.privacy_button.setCheckable(True)
        self.theme_button = self._button('themeToggleBtn', window.toggle_theme)
        self.minimize_button = self._button('minimizeAppBtn', window.showMinimized)
        self.maximize_button = self._button('maximizeRestoreAppBtn', self.toggle_maximized)
        self.minimize_button.setToolTip('\u6700\u5c0f\u5316')
        window.close_btn.setToolTip('\u5173\u95ed')
        window.close_btn.setAutoDefault(False)
        window.close_btn.setDefault(False)
        window.display_privacy.changed.connect(self.refresh)
        window.installEventFilter(self)
        window.title_bar.installEventFilter(self)
        window.title_label.installEventFilter(self)
        self.corner_edges = {
            window.top_left_grip: Qt.TopEdge | Qt.LeftEdge,
            window.top_right_grip: Qt.TopEdge | Qt.RightEdge,
            window.bottom_left_grip: Qt.BottomEdge | Qt.LeftEdge,
            window.bottom_right_grip: Qt.BottomEdge | Qt.RightEdge,
        }
        for grip in self.corner_edges:
            grip.installEventFilter(self)
        self.refresh()

    def _button(self, name, action):
        """\u4f7f\u7528\u539f\u6807\u9898\u680f\u6309\u94ae\u540d\u79f0\uff0c\u8fde\u63a5\u4e00\u6b21\u52a8\u4f5c\u3002"""
        window = self.window
        button = QPushButton(window.title_button_box)
        button.setObjectName(name)
        button.setText('')
        button.setCursor(Qt.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(action)
        window.title_button_box_layout.insertWidget(window.title_button_box_layout.count()-1, button)
        window.scale_manager.bind_metrics(button, fixed_size=(28,28), icon_size=20)
        return button

    def _toggle_settings(self):
        self.window.openSettingsWindow()

    def _toggle_privacy(self, checked):
        """\u5c06\u9690\u79c1\u72b6\u6001\u4ea4\u7ed9\u539f\u516c\u5171\u63a7\u5236\u5668\u3002"""
        self.window.display_privacy.set_enabled(checked)

    def toggle_maximized(self):
        """\u4ee5\u771f\u5b9e\u7a97\u53e3\u72b6\u6001\u5207\u6362\u6700\u5927\u5316\u548c\u8fd8\u539f\u3002"""
        window = self.window
        window.showNormal() if window.isMaximized() else window.showMaximized()

    def refresh(self):
        """\u540c\u6b65\u660e\u6697\u989c\u8272\u3001\u9501\u548c\u592a\u9633\u6708\u4eae\u56fe\u6807\u4ee5\u53ca\u7f29\u653e\u5c3a\u5bf8\u3002"""
        window = self.window
        window.scale_manager.bind_metrics(window.close_btn,fixed_size=(28,28),icon_size=20)
        window.scale_manager.bind_metrics(window.title_bar,fixed_height=56)
        window.scale_manager.bind_metrics(self.logo,fixed_size=(56,56))
        size = window.scale_manager.px(36)
        self.logo.setPixmap(window.windowIcon().pixmap(size,size))
        light = is_light_theme(window.theme_name)
        private = window.display_privacy.enabled
        maximized = window.isMaximized()
        icons = (
            (self.settings_button, ':/icons/images/icons/icon_settings.png'),
            (self.privacy_button, str(BASE_DIR/'images/icons'/('cil-lock-locked.png' if private else 'cil-lock-unlocked.png'))),
            (self.theme_button, str(BASE_DIR/'images/icons'/('cil-sun.png' if light else 'cil-moon.png'))),
            (self.minimize_button, ':/icons/images/icons/icon_minimize.png'),
            (self.maximize_button, ':/icons/images/icons/icon_restore.png' if maximized else ':/icons/images/icons/icon_maximize.png'),
        )
        for button, icon in icons:
            button.setIcon(get_theme_icon(icon, light))
            window.scale_manager.bind_metrics(button, fixed_size=(28,28), icon_size=20)
        with QSignalBlocker(self.privacy_button):
            self.privacy_button.setChecked(private)
        self.privacy_button.setToolTip('\u5173\u95ed\u9690\u79c1\u6a21\u5f0f' if private else '\u5f00\u542f\u9690\u79c1\u6a21\u5f0f\uff08\u9690\u85cf\u7f51\u7edc\u6807\u8bc6\uff09')
        self.theme_button.setToolTip('\u5207\u6362\u5230\u6697\u8272\u4e3b\u9898' if light else '\u5207\u6362\u5230\u4eae\u8272\u4e3b\u9898')
        self.maximize_button.setToolTip('\u8fd8\u539f' if maximized else '\u6700\u5927\u5316')
        for grip in window._all_grips():
            grip.setVisible(not maximized)
            grip.raise_()
        window.size_grip.hide()
        if hasattr(window, 'navigation'):
            window.navigation.footer_grip.setVisible(not maximized)
        edge = 0
        window.root_layout.setContentsMargins(edge,edge,edge,edge)

    def eventFilter(self, watched, event):
        """\u7531\u72b6\u6001\u4e8b\u4ef6\u5237\u65b0\u8fd8\u539f\u56fe\u6807\uff0c\u6807\u9898\u680f\u53cc\u51fb\u6700\u5927\u5316\u3002"""
        if watched in getattr(self, 'corner_edges', {}) and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                handle = self.window.windowHandle()
                if handle is not None and handle.startSystemResize(self.corner_edges[watched]):
                    return True
        if watched is self.window and event.type() == QEvent.WindowStateChange:
            self.refresh()
        elif watched in (self.window.title_bar,self.window.title_label):
            if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                self.toggle_maximized()
                return True
        return False
