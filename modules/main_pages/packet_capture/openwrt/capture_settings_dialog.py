"""Capture settings in the shared, resizable dialog shell."""

from PySide6.QtCore import Qt, Signal

from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.config_manager import ConfigManager
from modules.dialog_theme import build_base_stylesheet
from modules.settings_pages.settings_widgets import create_settings_button
from .capture_settings_page import CaptureSettingsPage


class CaptureSettingsDialog(FramelessDialog):
    """Keep one lazily created editor and the existing immediate-save behavior."""

    settingsChanged = Signal()

    def __init__(self, service, settings, parent):
        super().__init__("\u6293\u5305\u8bbe\u7f6e", parent, inherit_parent_stylesheet=False)
        self.setObjectName("captureSettingsDialog")
        self.setWindowModality(Qt.WindowModal)
        self.set_scaled_size((600, 420), (780, 720))
        self.page = CaptureSettingsPage(service, settings, self)
        self.content_layout.addWidget(self.page, 1)
        self.close_button = create_settings_button("\u5173\u95ed", self, "secondary")
        self.close_button.clicked.connect(self.reject)
        self.button_layout.addStretch(1)
        self.button_layout.addWidget(self.close_button)
        self._last_form = self.page.collect_settings()
        self.page.settingsChanged.connect(self._form_changed)
        self.finished.connect(self._flush_pending_edits)

    def _form_changed(self):
        self._last_form = self.page.collect_settings()
        self.settingsChanged.emit()

    def _flush_pending_edits(self, _result):
        current = self.page.collect_settings()
        if current != self._last_form:
            self._last_form = current
            self.settingsChanged.emit()

    def sync_appearance(self, style_manager):
        host = style_manager.page
        theme = "light.qss" if host.light_theme else "dark.qss"
        stylesheet = build_base_stylesheet(ConfigManager(), host.light_theme, theme, host.scale_manager)
        # Limit the settings shell's scroll background to the scroll body.
        # Its generic descendant rule would also paint every checkbox and label.
        stylesheet = stylesheet.replace("QScrollArea QWidget", "QScrollArea > QWidget > QWidget")
        stylesheet += "\n" + style_manager._capture_stylesheet(host.light_theme)
        if self.styleSheet() != stylesheet:
            self.setStyleSheet(stylesheet)
        style_manager.refresh_subtree(self)
