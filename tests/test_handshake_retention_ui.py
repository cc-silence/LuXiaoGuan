"""Settings rendering and reopen regression without router access."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from modules.config_services.capture_settings_manager import CaptureSettingsManager
from modules.main_pages.packet_capture.openwrt.capture_settings_page import CaptureSettingsPage
from modules.dialog_theme import build_base_stylesheet
from modules.config_manager import ConfigManager
APP = QApplication.instance() or QApplication([])

class Service(QObject):
    openwrtSnapshotChanged = Signal(object)
    openwrt_snapshot = None
    def verified_history_snapshot(self): return {}

class RetentionUiTests(unittest.TestCase):
    def test_obsolete_option_is_not_present(self):
        service = Service()
        with TemporaryDirectory() as folder, patch.object(CaptureSettingsPage, "_build_password_options"), patch.object(CaptureSettingsPage, "_dictionary_paths", return_value=()):
            manager = CaptureSettingsManager(Path(folder) / "capture.json")
            page = CaptureSettingsPage(service, manager.load()["openwrt"])
            try:
                self.assertFalse(hasattr(page, "save_usable_only"))
                self.assertNotIn("handshake_save_usable_only", page.collect_settings())
                for theme in ("dark.qss", "light.qss"):
                    page.setStyleSheet(build_base_stylesheet(ConfigManager(), theme_name=theme))
                    page.resize(740, 700)
                    page.show()
                    APP.processEvents()
                    self.assertFalse(page.grab().isNull())
            finally:
                page.close()
                page.deleteLater()
                APP.processEvents()
