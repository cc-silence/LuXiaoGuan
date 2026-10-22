"""Rendered regression for button alignment and checkbox background inheritance."""
import os
import unittest
from pathlib import Path
from tests import test_standalone as standalone
APP = standalone.APP
from PySide6.QtGui import QImageWriter
from PySide6.QtWidgets import QPushButton, QCheckBox, QScrollArea


class ThemeScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window = standalone.StandaloneTests.window
        if os.environ.get("ROUTERMANAGER_OLD_HOST"):
            from modules.dialog_theme import build_base_stylesheet
            cls.window._shell_stylesheet = lambda theme: build_base_stylesheet(
                cls.window.config_manager, theme == "light.qss", theme, cls.window.scale_manager)

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def test_rendered_buttons_and_checkbox_rows_in_both_themes(self):
        window = self.window
        tabs = window.packet_capture_page.openwrt_tab.tabs
        for theme in ("dark.qss", "light.qss"):
            window.apply_appearance(100, "default", theme)
            for index in range(tabs.count()):
                tabs.setCurrentIndex(index)
                APP.processEvents()
                page = tabs.currentWidget()
                for scroll in page.findChildren(QScrollArea):
                    if scroll.isVisible() and scroll.objectName() == "captureScrollArea":
                        content = scroll.widget()
                        self.assertFalse(content.autoFillBackground(), (theme,index,content.objectName()))
                buttons = [b for b in page.findChildren(QPushButton)
                           if b.isVisible() and b.text() and b.width() > 300]
                for button in buttons:
                    original = button.styleSheet()
                    actual = button.grab().toImage()
                    try:
                        button.setStyleSheet(original + "\nQPushButton { text-align: center; }")
                        APP.processEvents()
                        self.assertEqual(actual, button.grab().toImage(), (theme,index,button.text()))
                    finally:
                        button.setStyleSheet(original)
                for box in page.findChildren(QCheckBox):
                    if not box.isVisible() or box.width() < 300:
                        continue
                    parent = box.parentWidget()
                    point = box.pos()
                    if point.y() < 3:
                        continue
                    image = parent.grab().toImage()
                    x = point.x() + box.width() - 4
                    self.assertEqual(image.pixelColor(x, point.y()+box.height()//2),
                                     image.pixelColor(x, point.y()-2), (theme,index,box.text()))
                writer = QImageWriter(str(Path(__file__).parent / ("scope-"+theme+"-"+str(index)+".png")), b"png")
                writer.setCompression(0)
                self.assertTrue(writer.write(window.grab().toImage()), writer.errorString())

if __name__ == "__main__":
    unittest.main()
