"""Actual tray menu popup colors and live theme synchronization."""
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QPoint
from PySide6.QtGui import QImageWriter
from tests import test_standalone as standalone
from modules.dialog_theme import context_menu_stylesheet
APP=standalone.APP


class TrayMenuThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window=standalone.StandaloneTests.window
        with patch('router_app.close_behavior.QSystemTrayIcon') as tray:
            tray.isSystemTrayAvailable.return_value=True
            cls.window.close_behavior.hide_to_tray()
        cls.menu=cls.window.close_behavior.menu
        cls.window.close_behavior.restore()

    @classmethod
    def tearDownClass(cls):
        cls.menu.hide()
        standalone.StandaloneTests.tearDownClass()

    def test_rendered_light_dark_normal_and_selected_text(self):
        for light in (True,False,True):
            theme='light.qss' if light else 'dark.qss'
            self.window.apply_appearance(100,'default',theme)
            for selected in (False,True):
                self.menu.popup(QPoint(80,80))
                self.menu.setActiveAction(self.menu.actions()[0] if selected else None)
                APP.processEvents()
                image=self.menu.grab().toImage()
                rect=self.menu.actionGeometry(self.menu.actions()[0])
                pixels=[image.pixelColor(x,y).lightness()
                        for x in range(rect.left()+12,rect.right()-24)
                        for y in range(rect.top()+3,rect.bottom()-3)]
                count=sum(value<90 if light else value>180 for value in pixels)
                self.assertGreater(count,8,(theme,selected))
                writer=QImageWriter(str(Path(__file__).parent/('tray-'+theme+'-'+str(selected)+'.png')),b'png')
                writer.setCompression(0)
                self.assertTrue(writer.write(image))
                self.menu.hide()

    def test_popup_refresh_and_scale_use_current_appearance(self):
        self.window.apply_appearance(125,'default','light.qss')
        expected=self.window.scale_manager.scale_stylesheet(context_menu_stylesheet(True))
        self.assertEqual(self.menu.styleSheet(),expected)
        self.menu.setStyleSheet(context_menu_stylesheet(False))
        self.menu.popup(QPoint(80,80))
        APP.processEvents()
        self.assertEqual(self.menu.styleSheet(),expected)
        self.menu.hide()
        self.window.hide()
        self.menu.actions()[0].trigger()
        self.assertTrue(self.window.isVisible())
        self.window.apply_appearance(100,'default','dark.qss')

if __name__=='__main__':unittest.main()
