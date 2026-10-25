"""Embedded settings page with immediate persistence and scoped appearance."""
from .identity import DISPLAY_NAME
from PySide6.QtCore import Qt, QSignalBlocker, Signal
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from modules.common_widgets.settings_nav_panel import SettingsNavPanel
from .close_behavior import normalize_close_action
from modules.common_widgets.common_dialogs import MessageDialog
from modules.common_widgets.combo_box import CommonComboBox
from modules.config_services.proxy_config_manager import ProxyConfigManager
from modules.settings_pages.proxy_settings_page import build_proxy_page
from modules.settings_pages.app_update_page import AppUpdatePage
from modules.settings_pages.settings_widgets import create_hint_label, create_settings_scroll_page, create_settings_section_title, create_separator
from modules.ui_scale_manager import UiScaleManager


class RouterSettings(QFrame):
    """Settings apply immediately; update work remains owned by the runtime."""
    finished = Signal(int)

    def __init__(self, owner):
        super().__init__(owner)
        self.setObjectName("settingsWindowFrame")
        self.owner = owner
        self.scale_manager = owner.scale_manager
        initial_scale = self.scale_manager.global_scale
        initial_family = self.scale_manager.font_family
        self.content_layout = QHBoxLayout(self)
        self.scale_manager.bind_layout(self.content_layout, margins=(4,4,4,0), spacing=12)
        self.nav_panel = SettingsNavPanel(self)
        self.scale_manager.bind_metrics(self.nav_panel, min_width=130)
        self.nav_panel.add_title("\u8bbe\u7f6e")
        self.nav_panel.add_section("\u8f6f\u4ef6\u8bbe\u7f6e")
        self.nav_panel.add_button("\u5168\u5c40\u8bbe\u7f6e", ":/icons/images/icons/cil-settings.png")
        self.nav_panel.add_button("\u4ee3\u7406\u8bbe\u7f6e", ":/icons/images/icons/cil-link-alt.png")
        self.nav_panel.add_spacing()
        self.nav_panel.add_section(DISPLAY_NAME)
        self.nav_panel.add_button("\u5728\u7ebf\u66f4\u65b0", ":/icons/images/icons/cil-reload.png")
        self.nav_panel.add_stretch()
        self.content_layout.addWidget(self.nav_panel)
        right = QVBoxLayout()
        right.setContentsMargins(0,0,0,0)
        right.setSpacing(0)
        self.content_layout.addLayout(right, 1)
        self.pages = QStackedWidget(self)
        right.addWidget(self.pages, 1)
        for index, button in enumerate(self.nav_panel.buttons):
            button.clicked.connect(lambda _checked=False, index=index: self.pages.setCurrentIndex(index))
        self.pages.currentChanged.connect(lambda index: self.nav_panel.set_checked(self.nav_panel.buttons[index]))
        self.nav_panel.set_checked(self.nav_panel.buttons[0])
        appearance, container, layout = create_settings_scroll_page(self.pages)
        layout.addWidget(create_settings_section_title("\u754c\u9762\u5916\u89c2", container))
        self.ui_scale_combo = CommonComboBox(container)
        for level, label in zip(UiScaleManager.GLOBAL_LEVELS, ("\u6807\u51c6", "\u8f83\u5927", "\u5927", "\u7279\u5927")):
            self.ui_scale_combo.addItem(f"{label} ({level}%)", level)
        self.ui_scale_combo.setCurrentIndex(self.ui_scale_combo.findData(initial_scale))
        self.ui_scale_combo.setToolTip("\u540c\u65f6\u8c03\u6574\u6587\u5b57\u548c\u63a7\u4ef6\u5927\u5c0f\uff0c\u9009\u62e9\u540e\u7acb\u5373\u4fdd\u5b58\u5e76\u751f\u6548\u3002")
        self.ui_font_combo = CommonComboBox(container)
        fonts = [("\u9ed8\u8ba4\u5b57\u4f53 (\u5fae\u8f6f\u96c5\u9ed1)", "default"), ("\u5fae\u8f6f\u96c5\u9ed1\uff08\u7c97\u4f53\uff09", "bold"), ("\u5b8b\u4f53", "SimSun"), ("\u9ed1\u4f53", "SimHei"), ("\u5fae\u8f6f\u96c5\u9ed1", "Microsoft YaHei"), ("\u6977\u4f53", "KaiTi"), ("\u4eff\u5b8b", "FangSong"), ("Arial", "Arial"), ("Consolas", "Consolas")]
        for label, value in fonts:
            self.ui_font_combo.addItem(label, value)
        self.ui_font_combo.setCurrentIndex(max(0, self.ui_font_combo.findData(initial_family)))
        self.ui_font_combo.setToolTip("\u9009\u62e9\u754c\u9762\u5b57\u4f53\uff0c\u6587\u5b57\u5927\u5c0f\u8ddf\u968f\u754c\u9762\u7f29\u653e\u3002")
        self._add_row(layout, container, "\u754c\u9762\u7f29\u653e", self.ui_scale_combo)
        self._add_row(layout, container, "\u754c\u9762\u5b57\u4f53", self.ui_font_combo)
        layout.addWidget(create_separator(container))
        layout.addWidget(create_settings_section_title("\u884c\u4e3a\u8bbe\u7f6e", container))
        self.close_button_action_combo = CommonComboBox(container)
        for text, value in (("\u9996\u6b21\u8be2\u95ee", "ask"), ("\u5173\u95ed\u8f6f\u4ef6", "exit"), ("\u6700\u5c0f\u5316\u5230\u6258\u76d8", "tray")):
            self.close_button_action_combo.addItem(text, value)
        action = normalize_close_action(owner.runtime.config.get("runtime", "close_button_action", fallback="tray"))
        self.close_button_action_combo.setCurrentIndex(self.close_button_action_combo.findData(action))
        self.close_button_action_combo.setToolTip("\u63a7\u5236\u6807\u9898\u680f\u5173\u95ed\u6309\u94ae\u3002\u6258\u76d8\u6a21\u5f0f\u7ee7\u7eed\u540e\u53f0\u4efb\u52a1\uff0c\u53ef\u4ece\u6258\u76d8\u6062\u590d\u6216\u9000\u51fa\u3002")
        self._add_row(layout, container, "\u5173\u95ed\u6309\u94ae\u884c\u4e3a", self.close_button_action_combo)
        layout.addWidget(create_hint_label("\u8bbe\u7f6e\u4fee\u6539\u540e\u81ea\u52a8\u4fdd\u5b58\u5e76\u751f\u6548\u3002", appearance))
        layout.addWidget(create_separator(container))
        from modules.settings_pages.desktop_shortcut_section import DesktopShortcutSection
        self.desktop_shortcut_section = DesktopShortcutSection(owner.runtime.desktop_shortcuts, container)
        layout.addWidget(self.desktop_shortcut_section)
        layout.addStretch()
        self.pages.addWidget(appearance)
        self.proxy_config_manager = ProxyConfigManager()
        self.pages.addWidget(build_proxy_page(self, immediate=True))
        self.update_page = AppUpdatePage(owner.runtime.app_updates, self.pages)
        self.pages.addWidget(self.update_page)
        for combo in (self.ui_scale_combo, self.ui_font_combo, self.close_button_action_combo):
            combo.currentIndexChanged.connect(self._apply_settings)
        self.sync_appearance()

    def _add_row(self, layout, parent, text, combo):
        """复用原全局设置的横向表单对齐和缩放尺寸。"""
        row = QHBoxLayout()
        self.scale_manager.bind_layout(row, margins=(0,0,0,0), spacing=10)
        label = QLabel(text, parent)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.scale_manager.bind_metrics(label, min_width=86)
        row.addWidget(label)
        row.addWidget(combo, 1)
        layout.addLayout(row)

    def _apply_settings(self):
        """Persist valid selections before synchronizing live appearance."""
        from copy import deepcopy
        config = deepcopy(self.owner.runtime.config)
        scale, family = self.ui_scale_combo.currentData(), self.ui_font_combo.currentData()
        action = normalize_close_action(self.close_button_action_combo.currentData())
        config.set("ui", "scale", str(scale))
        config.set("ui", "font_family", family)
        config.set("runtime", "close_button_action", action)
        try:
            self.owner.config_manager.save(config)
        except (OSError, ValueError) as exc:
            values = (self.owner.scale_manager.global_scale, self.owner.scale_manager.font_family,
                      normalize_close_action(self.owner.runtime.config.get(
                          "runtime", "close_button_action", fallback="tray")))
            for combo, value in zip((self.ui_scale_combo, self.ui_font_combo,
                                     self.close_button_action_combo), values):
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(combo.findData(value))
            MessageDialog("\u4fdd\u5b58\u5931\u8d25", str(exc), self).exec()
            return
        self.owner.runtime.config = config
        self.owner.apply_appearance(scale, family, self.owner.theme_name)
        self.sync_appearance()

    def sync_appearance(self):
        """Synchronize this page through the existing shared theme and metrics."""
        key=(self.owner.scale_manager.global_scale,self.owner.scale_manager.font_family,self.owner.theme_name)
        if getattr(self,"_appearance_key",None)==key:return
        self.scale_manager=self.owner.scale_manager
        self.scale_manager.publish(self)
        self.scale_manager.apply_bound_metrics(self)
        self.nav_panel.set_light_theme(self.owner.theme_name == "light.qss")
        from modules.dialog_theme import build_base_stylesheet
        style=build_base_stylesheet(self.owner.config_manager,self.owner.theme_name=="light.qss",self.owner.theme_name,self.scale_manager)
        if self.styleSheet()!=style:
            self.setStyleSheet(style)
        self._appearance_key=key

    def done(self, result):
        """Flush an active proxy edit before leaving; saved settings never roll back."""
        if not self._proxy_flush_fn():
            self.pages.setCurrentIndex(1)
            return
        self.finished.emit(result)

    def accept(self):
        self.done(1)

    def reject(self):
        self.done(0)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)
