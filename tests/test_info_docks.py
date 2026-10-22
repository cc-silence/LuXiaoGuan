"""Rendered integration checks for all resident router information docks."""
import unittest
from pathlib import Path
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from tests import test_standalone as standalone
from modules.config_manager import BASE_DIR
APP = standalone.APP


class InfoDockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window = standalone.StandaloneTests.window

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def test_all_docks_hide_restore_retain_logs_and_follow_themes(self):
        window = self.window
        host = window.packet_capture_page.openwrt_tab
        pages = [("login", host.login_page, 0)]
        for name in ("signal", "capture", "files"):
            pages.append((name, host._ensure_subpage(name), host._page_indices[name]))
        output = BASE_DIR / "build" / "info-dock-review"
        output.mkdir(parents=True, exist_ok=True)
        for theme, scale in (("dark.qss",100), ("light.qss",150)):
            window.apply_appearance(scale,"default",theme)
            for name, page, index in pages:
                with self.subTest(theme=theme, page=name):
                    host.tabs.setCurrentIndex(index)
                    APP.processEvents()
                    dock = page.info_dock
                    panel = dock.panel
                    self.assertTrue(panel.isVisible())
                    self.assertTrue(dock.toggle_button.isVisible())
                    self.assertEqual(panel.maximumWidth(),16777215)
                    old = dock.splitter.sizes()
                    content = dock.splitter.widget(0)
                    extent = content.width() if dock.orientation == Qt.Horizontal else content.height()
                    expected = "right" if dock.orientation == Qt.Horizontal else "bottom"
                    self.assertIn("chevron-"+expected, dock.toggle_button.property("iconPath"))
                    expanded_position = dock.toggle_button.mapTo(dock, QPoint())
                    dock.toggle_button.click()
                    APP.processEvents()
                    self.assertTrue(panel.isHidden())
                    collapsed_position = dock.toggle_button.mapTo(dock, QPoint())
                    self.assertLessEqual(abs(collapsed_position.x() - expanded_position.x()), 1)
                    self.assertLessEqual(abs(collapsed_position.y() - expanded_position.y()), 1)
                    self.assertEqual(dock.splitter.sizes()[1],0)
                    self.assertGreater(content.width() if dock.orientation == Qt.Horizontal else content.height(),extent)
                    self.assertTrue(dock.toggle_button.isVisible())
                    self.assertFalse(dock.toggle_button.icon().isNull())
                    panel.append_info("hidden log retained", "info")
                    image = window.grab()
                    self.assertTrue(image.save(str(output/(name+"-"+theme+"-collapsed.png"))))
                    host.tabs.setCurrentIndex((index+1)%host.tabs.count())
                    host.tabs.setCurrentIndex(index)
                    APP.processEvents()
                    self.assertTrue(panel.isHidden())
                    dock.toggle_button.click()
                    APP.processEvents()
                    self.assertTrue(panel.isVisible())
                    self.assertIn("hidden log retained",panel.info_box.toPlainText())
                    self.assertLessEqual(abs(dock.splitter.sizes()[1]-old[1]),2)
                    self.assertTrue(window.grab().save(str(output/(name+"-"+theme+"-expanded.png"))))

    def test_login_handle_drag_changes_width(self):
        self.window.apply_appearance(100,"default","dark.qss")
        host = self.window.packet_capture_page.openwrt_tab
        host.tabs.setCurrentIndex(0)
        APP.processEvents()
        dock = host.login_page.info_dock
        before = dock.splitter.sizes()
        handle = dock.splitter.handle(1)
        self.assertEqual(handle.cursor().shape(),Qt.SplitHCursor)
        start = handle.rect().center()
        QTest.mousePress(handle,Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(handle,start-QPoint(60,0))
        QTest.mouseRelease(handle,Qt.LeftButton,Qt.NoModifier,start-QPoint(60,0))
        APP.processEvents()
        self.assertGreater(dock.splitter.sizes()[1],before[1])
        dock.toggle_button.click()
        dock.toggle_button.click()
        APP.processEvents()
        self.assertGreater(dock.splitter.sizes()[1],before[1])


if __name__ == "__main__":
    unittest.main()
