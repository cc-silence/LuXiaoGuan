"""Shared title-bar minimize and maximize/restore controls for editable dialogs."""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QPushButton
from modules.dialog_theme import detect_light_theme
from modules.icon_utils import get_theme_icon
from modules.ui_scale_manager import UiScaleManager


class DialogWindowControls(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        window.setWindowFlags(window.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowSystemMenuHint)
        self.minimize_button = self._button("最小化", self.minimize)
        self.maximize_button = self._button("最大化", self.toggle_maximized)
        window.installEventFilter(self)
        window.title_bar.installEventFilter(self)
        window.title_label.installEventFilter(self)
        self.refresh()

    def _button(self, tooltip, callback):
        window = self.window
        button = QPushButton(window.title_button_box)
        button.setObjectName("settingsWindowControlBtn")
        button.setProperty("windowControl", True)
        button.setCursor(Qt.PointingHandCursor)
        button.setAutoDefault(False)
        button.setDefault(False)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.clicked.connect(callback)
        window.title_button_box_layout.insertWidget(window.title_button_box_layout.count() - 1, button)
        UiScaleManager.for_widget(window).bind_metrics(button, fixed_size=(28, 28), icon_size=20)
        return button


    def minimize(self):
        """Keep modal ownership intact by minimizing its visible owner group."""
        window = self.window
        if window.isModal():
            owner = None
            parent = window.parentWidget()
            while parent is not None:
                frame = getattr(parent, "window_frame", None)
                candidate = (frame if frame is not None else parent).window()
                if candidate is not window and candidate.isVisible():
                    owner = candidate
                parent = parent.parentWidget()
            if owner is not None:
                owner.showMinimized()
                return
        window.showMinimized()

    def toggle_maximized(self):
        window = self.window
        window.showNormal() if window.isMaximized() else window.showMaximized()

    def refresh(self):
        window = self.window
        maximized = window.isMaximized()
        light = detect_light_theme(window)
        for button, icon in (
            (self.minimize_button, "icon_minimize.png"),
            (self.maximize_button, "icon_restore.png" if maximized else "icon_maximize.png"),
        ):
            button.setIcon(get_theme_icon(":/icons/images/icons/" + icon, light))
        title = "还原" if maximized else "最大化"
        self.maximize_button.setToolTip(title)
        self.maximize_button.setAccessibleName(title)
        for grip in (*window._all_grips(), window.size_grip):
            grip.setVisible(not maximized)

    def eventFilter(self, watched, event):
        window = self.window
        if watched is window and event.type() in (QEvent.WindowStateChange, QEvent.StyleChange, QEvent.PaletteChange):
            self.refresh()
        elif watched in (window.title_bar, window.title_label) and event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
            self.toggle_maximized()
            return True
        return super().eventFilter(watched, event)
