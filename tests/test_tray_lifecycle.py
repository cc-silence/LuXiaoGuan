"""Tray exists while the window is shown, minimized or hidden; only exit removes it."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from tests.test_standalone import APP
from PySide6.QtWidgets import QWidget
from modules.ui_scale_manager import UiScaleManager
from router_app.close_behavior import CloseBehavior


class TrayLifecycleTests(unittest.TestCase):
    def test_startup_minimize_restore_reuse_single_tray_until_disposal(self):
        window = QWidget()
        window.scale_manager = UiScaleManager(100)
        window.theme_name = "dark.qss"
        window.request_exit = Mock()
        tray, menu = Mock(), Mock()
        with patch("router_app.close_behavior.QSystemTrayIcon", return_value=tray) as factory, patch(
                "router_app.close_behavior.create_themed_menu", return_value=menu):
            factory.isSystemTrayAvailable.return_value = True
            behavior = CloseBehavior(window)
            factory.assert_called_once()
            tray.show.assert_called_once()
            tray.setContextMenu.assert_called_once_with(menu)
            self.assertEqual(menu.addAction.call_count, 2)
            window.show()
            window.showMinimized()
            behavior.restore()
            tray.hide.assert_not_called()
            behavior.hide_to_tray()
            self.assertFalse(window.isVisible())
            behavior.restore()
            self.assertTrue(window.isVisible())
            factory.assert_called_once()
            behavior.dispose()
            tray.hide.assert_called_once()
            tray.deleteLater.assert_called_once()
            menu.deleteLater.assert_called_once()
            self.assertIsNone(behavior.tray)
            window.deleteLater()

    def test_no_system_tray_keeps_window_accessible_on_taskbar(self):
        window = QWidget()
        window.scale_manager = UiScaleManager(100)
        window.theme_name = "dark.qss"
        window.request_exit = Mock()
        with patch("router_app.close_behavior.QSystemTrayIcon") as factory, patch(
                "router_app.close_behavior.create_themed_menu"):
            factory.isSystemTrayAvailable.return_value = False
            behavior = CloseBehavior(window)
            behavior.hide_to_tray()
            self.assertTrue(window.isMinimized())
            self.assertTrue(window.isVisible())
            behavior.dispose()
        window.deleteLater()


class TrayMenuRenderingTests(unittest.TestCase):
    def test_real_menu_has_transparent_corners_in_both_themes_and_scales(self):
        from pathlib import Path
        from PySide6.QtCore import Qt, QPoint
        output = Path("build/tray-preview")
        output.mkdir(exist_ok=True)
        window = QWidget()
        window.scale_manager = UiScaleManager(100)
        window.theme_name = "dark.qss"
        window.request_exit = Mock()
        with patch("router_app.close_behavior.QSystemTrayIcon"):
            behavior = CloseBehavior(window)
            menu = behavior.menu
            self.assertTrue(menu.testAttribute(Qt.WA_TranslucentBackground))
            for light, scale in ((False, 100), (True, 175)):
                window.theme_name = "light.qss" if light else "dark.qss"
                window.scale_manager = UiScaleManager(scale)
                behavior.refresh_menu_theme()
                menu.popup(QPoint(50, 50))
                for _ in range(3): APP.processEvents()
                image = menu.grab().toImage()
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                self.assertEqual(image.pixelColor(image.width()-1, 0).alpha(), 0)
                self.assertGreater(image.pixelColor(image.width()//2, image.height()//2).alpha(), 0)
                self.assertTrue(image.save(str(output / ("light.png" if light else "dark.png"))))
                menu.hide()
            behavior.dispose()
        window.deleteLater()


class TrayActionTests(unittest.TestCase):
    def test_real_mouse_clicks_restore_and_exit_actions(self):
        from PySide6.QtCore import Qt, QPoint
        from PySide6.QtTest import QTest
        window = QWidget()
        window.scale_manager = UiScaleManager(100)
        window.theme_name = "dark.qss"
        window.request_exit = Mock()
        with patch("router_app.close_behavior.QSystemTrayIcon"):
            behavior = CloseBehavior(window)
            for index in (0, 1):
                window.hide()
                menu = behavior.menu
                menu.popup(QPoint(50, 50))
                APP.processEvents()
                rect = menu.actionGeometry(menu.actions()[index])
                QTest.mouseClick(menu.windowHandle(), Qt.LeftButton, Qt.NoModifier, rect.center())
                APP.processEvents()
                if index == 0:
                    self.assertTrue(window.isVisible())
                else:
                    window.request_exit.assert_called_once()
            behavior.dispose()
        window.deleteLater()


class ExitAfterScreenRemovalTests(unittest.TestCase):
    def test_runtime_exit_still_disposes_tray_after_stale_screen(self):
        import time
        from PySide6.QtCore import QObject
        from shiboken6 import delete
        from tests.test_standalone import StandaloneTests
        StandaloneTests.setUpClass()
        try:
            window, runtime = StandaloneTests.window, StandaloneTests.runtime
            screen = QObject()
            delete(screen)
            window.geometry_policy._release_screen()
            window.geometry_policy._screen = screen
            runtime.exit_application()
            deadline = time.monotonic() + 10
            while not runtime._ready_to_close and time.monotonic() < deadline:
                APP.processEvents()
            self.assertTrue(runtime._ready_to_close)
            self.assertIsNone(window.close_behavior.tray)
            self.assertIsNone(window.close_behavior.menu)
        finally:
            StandaloneTests.tearDownClass()
