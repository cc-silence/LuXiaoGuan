"""Window scaling preserves user intent and fits logical screen coordinates."""
import configparser
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from tests.test_standalone import APP
from PySide6.QtCore import QRect, QSize, QPoint, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout
from modules.ui_scale_manager import UiScaleManager
from modules.desktop_shell.initial_ui_scale import initial_ui_scale, recommended_ui_scale
from router_app.window_geometry import WindowGeometry, fitted_geometry


class GeometryTests(unittest.TestCase):
    def window(self, area=QRect(0, 0, 1920, 1040)):
        w = QWidget()
        w.setWindowFlag(Qt.FramelessWindowHint)
        w.setLayout(QVBoxLayout())
        w.scale_manager = UiScaleManager(100)
        w.resize(1100, 680)
        with patch.object(WindowGeometry, "area", return_value=area):
            policy = WindowGeometry(w)
        policy.area = lambda: area
        self.addCleanup(w.deleteLater)
        self.addCleanup(policy.dispose)
        return w, policy

    def change(self, w, p, value):
        reference = p.before_scale()
        w.scale_manager = UiScaleManager(value)
        p.apply_scale(reference)
        APP.processEvents()

    def test_grow_and_return_without_accumulated_rounding(self):
        w, p = self.window()
        original = w.size()
        for value in (125, 100, 150, 100, 125, 100):
            self.change(w, p, value)
            self.assertEqual(w.width(), round(original.width()*value/100))
            self.assertEqual(w.height(), round(original.height()*value/100))
        self.assertEqual(w.size(), original)

    def test_screen_limit_maximizes_and_smaller_scale_restores(self):
        w, p = self.window(QRect(0, 0, 1280, 720))
        original = w.size()
        self.change(w, p, 175)
        self.assertTrue(w.isMaximized())
        self.assertTrue(p._auto_maximized)
        self.assertLessEqual(w.minimumHeight(), 720)
        self.change(w, p, 100)
        self.assertFalse(w.isMaximized())
        self.assertEqual(w.size(), original)

    def test_manual_maximize_stays_maximized(self):
        w, p = self.window()
        w.setWindowState(w.windowState() | Qt.WindowMaximized)
        self.change(w, p, 125)
        self.assertTrue(w.isMaximized())
        self.assertFalse(p._auto_maximized)

    def test_negative_screen_origin_and_oversized_dimensions(self):
        area = QRect(-1920, -100, 1920, 1040)
        result = fitted_geometry(area, QSize(3000, 1500), QPoint(800, 900))
        self.assertEqual(result, area)
        result = fitted_geometry(area, QSize(960, 600), QPoint(800, 900))
        self.assertTrue(area.contains(result))

    def test_high_saved_scale_initial_minimum_is_screen_bounded(self):
        w = QWidget()
        w.setWindowFlag(Qt.FramelessWindowHint)
        w.setLayout(QVBoxLayout())
        w.scale_manager = UiScaleManager(175)
        w.resize(1000, 650)
        area = QRect(0, 0, 1366, 728)
        with patch.object(WindowGeometry, "area", return_value=area):
            policy = WindowGeometry(w)
        self.assertTrue(area.contains(w.geometry()))
        self.assertEqual(w.minimumSize(), QSize(1366, 728))
        policy.dispose()
        w.deleteLater()


class ScreenLifecycleTests(unittest.TestCase):
    def test_destroyed_native_objects_are_released_before_rebind_or_disposal(self):
        from PySide6.QtCore import QObject, Signal
        from shiboken6 import delete
        class Screen(QObject):
            availableGeometryChanged = Signal()
        class Handle(QObject):
            screenChanged = Signal()
        window = QWidget()
        window.setLayout(QVBoxLayout())
        window.scale_manager = UiScaleManager(100)
        policy = WindowGeometry(window)
        screen, handle = Screen(), Handle()
        with patch.object(window, "screen", return_value=screen), patch.object(window, "windowHandle", return_value=handle):
            policy._bind_screen()
        delete(screen)
        delete(handle)
        self.assertIsNone(policy._screen)
        self.assertIsNone(policy._handle)
        with patch.object(window, "screen", return_value=screen), patch.object(window, "windowHandle", return_value=handle):
            policy._bind_screen()
            self.assertEqual(policy.area(), QRect(0, 0, 1280, 720))
            self.assertIsNone(policy._screen)
            self.assertIsNone(policy._handle)
        policy._screen = screen
        policy._handle = handle
        policy._bind_screen()
        self.assertIs(policy._screen, window.screen())
        policy._release_screen()
        policy._screen = screen
        policy._handle = handle
        policy.dispose()
        self.assertIsNone(policy._screen)
        self.assertIsNone(policy._handle)
        window.deleteLater()


class FirstScaleTests(unittest.TestCase):
    def screen(self, width, height, taskbar=40):
        return SimpleNamespace(geometry=lambda: QRect(0, 0, width, height),
                               availableGeometry=lambda: QRect(0, 0, width, height-taskbar))

    def config(self, saved=False):
        config = configparser.ConfigParser()
        config["ui"] = {"scale": "100"}
        manager = SimpleNamespace(load=lambda: config, has_saved_ui_scale=lambda: saved, save=Mock())
        return manager

    def test_logical_dimensions_do_not_double_apply_windows_dpi(self):
        self.assertEqual(recommended_ui_scale(self.screen(1920, 1080)), 100)
        self.assertEqual(recommended_ui_scale(self.screen(3840, 2160)), 175)
        self.assertEqual(recommended_ui_scale(self.screen(2560, 1440)), 125)
        self.assertEqual(recommended_ui_scale(self.screen(1280, 720)), 100)

    def test_first_run_persists_once_and_saved_choice_wins(self):
        manager = self.config()
        self.assertEqual(initial_ui_scale(manager, self.screen(3840, 2160)), 100)
        manager.save.assert_called_once()
        manager = self.config(saved=True)
        self.assertEqual(initial_ui_scale(manager, self.screen(3840, 2160)), 100)
        manager.save.assert_not_called()
        manager = self.config(saved=True)
        manager.load().set('ui', 'scale', '150')
        self.assertEqual(initial_ui_scale(manager, self.screen(3840, 2160)), 150)
        manager.save.assert_not_called()

    def test_smoke_does_not_save(self):
        manager = self.config()
        self.assertEqual(initial_ui_scale(manager, self.screen(3840, 2160), persist=False), 100)
        manager.save.assert_not_called()

class StartupGeometryTests(unittest.TestCase):
    def test_standard_size_is_independent_of_display_resolution(self):
        from modules.desktop_shell.initial_window_geometry import initial_window_geometry
        for width, height in ((1920,1080),(2560,1440),(3840,2160)):
            screen = QRect(0,0,width,height)
            area = QRect(0,0,width,height-40)
            result = initial_window_geometry(screen, area, QSize(960,600))
            self.assertEqual(result.size(), QSize(1280,720))
            self.assertTrue(area.contains(result))

    def test_saved_scale_scales_standard_size_once_and_small_screen_stays_bounded(self):
        from modules.desktop_shell.initial_window_geometry import initial_window_geometry
        screen, area = QRect(0,0,3840,2160), QRect(0,0,3840,2120)
        self.assertEqual(initial_window_geometry(screen,area,QSize(960,600),scale=150).size(),
                         QSize(1920,1080))
        small=QRect(-1280,0,1280,680)
        result=initial_window_geometry(QRect(-1280,0,1280,720),small,QSize(960,600),scale=175)
        self.assertEqual(result,small)

    def test_real_widget_ignores_large_content_hint_but_keeps_explicit_minimum(self):
        from modules.desktop_shell.initial_window_geometry import apply_initial_window_geometry
        from PySide6.QtWidgets import QLabel
        w=QWidget();w.setLayout(QVBoxLayout());w.setMinimumSize(960,600)
        label=QLabel("fixture");label.setMinimumWidth(1700);w.layout().addWidget(label)
        w.scale_manager=UiScaleManager(100)
        screen=SimpleNamespace(geometry=lambda:QRect(0,0,3840,2160),
                               availableGeometry=lambda:QRect(0,0,3840,2120))
        with patch.object(w,"screen",return_value=screen):
            apply_initial_window_geometry(w)
        self.assertEqual(w.size(),QSize(1280,720))
        w.deleteLater()

    def test_release_seed_uses_standard_scale_even_if_development_config_was_larger(self):
        import tempfile
        from pathlib import Path
        from scripts.release_layout import seed_assets
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/"source";root.mkdir()
            for name in ("images","themes","bin","config"):(root/name).mkdir()
            (root/"config/settings.ini").write_text(chr(10).join(("[ui]", "scale = 175", "")),encoding="utf-8")
            (root/"config/app_update.json").write_text("{}",encoding="utf-8")
            target=Path(folder)/"assets"
            with patch("scripts.release_layout.ROOT",root):
                seed_assets(target)
            config=configparser.ConfigParser()
            config.read(target/"config/settings.ini",encoding="utf-8")
            self.assertEqual(config.get("ui","scale"),"100")
