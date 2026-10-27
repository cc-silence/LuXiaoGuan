"""Default/missing theme rendering must match a full built-in shell."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication, QWidget, QFrame, QVBoxLayout, QPushButton, QTreeWidget, QTreeWidgetItem
from PySide6.QtCore import Qt
from modules.config_manager import ConfigManager
from modules.ui_scale_manager import UiScaleManager
from router_app.appearance import build_main_stylesheet

APP = QApplication.instance() or QApplication([])


class MainThemeFallbackTests(unittest.TestCase):
    def test_default_missing_and_empty_themes_match_dark_rendering(self):
        manager = ConfigManager()
        host = QWidget()
        layout = QVBoxLayout(host)
        menu = QFrame(host)
        menu.setObjectName("topMenu")
        side = QVBoxLayout(menu)
        button = QPushButton(menu)
        button.setObjectName("toggleButton")
        side.addWidget(button)
        layout.addWidget(menu)
        tree = QTreeWidget(host)
        tree.setHeaderLabels(["Files"])
        for i in range(100):
            QTreeWidgetItem(tree, [str(i)])
        tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        layout.addWidget(tree)
        host.resize(300, 300)
        host.show()
        try:
            with tempfile.TemporaryDirectory() as folder:
                empty = Path(folder) / "empty.qss"
                empty.write_text("", encoding="utf-8")
                unavailable = Mock()
                unavailable.theme_path.return_value = empty
                for scale in (100, 125, 150, 200):
                    metrics = UiScaleManager(scale)
                    reference = build_main_stylesheet(manager, "dark.qss", metrics)
                    host.setStyleSheet(reference)
                    APP.processEvents()
                    expected_button = button.grab().toImage()
                    expected_scroll = tree.verticalScrollBar().grab().toImage()
                    for config, name in ((manager, manager.DEFAULT_THEME_LABEL),
                                         (manager, "missing-theme.qss"), (unavailable, "empty.qss")):
                        actual = build_main_stylesheet(config, name, metrics)
                        self.assertEqual(actual, reference)
                        host.setStyleSheet(actual)
                        APP.processEvents()
                        self.assertEqual(button.grab().toImage(), expected_button)
                        self.assertEqual(tree.verticalScrollBar().grab().toImage(), expected_scroll)
        finally:
            host.close()
            host.deleteLater()

    def test_valid_custom_theme_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom.qss"
            path.write_text("QWidget { color: #123456; }", encoding="utf-8")
            config = Mock()
            config.theme_path.return_value = path
            self.assertIn("#123456", build_main_stylesheet(config, "custom.qss", UiScaleManager(100)))


if __name__ == "__main__":
    unittest.main()
