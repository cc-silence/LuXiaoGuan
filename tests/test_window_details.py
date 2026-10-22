"""Settings persistence, real corner drags and original shell geometry."""
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_standalone as standalone
from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QSize, QEvent
from PySide6.QtGui import QMouseEvent, QImageWriter
from PySide6.QtTest import QTest
from modules.desktop_shell.initial_window_geometry import initial_window_geometry
APP = standalone.APP


class WindowDetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window = standalone.StandaloneTests.window

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def test_original_geometry(self):
        rect = initial_window_geometry(QRect(0,0,1920,1080),QRect(0,0,1920,1040),QSize(960,600))
        self.assertEqual(rect.size(), QSize(1280,720))

    def test_four_corners_really_resize(self):
        window = self.window
        window.showNormal()
        for name, delta in (('top_left',QPoint(-20,-20)),('top_right',QPoint(20,-20)),
                            ('bottom_left',QPoint(-20,20)),('bottom_right',QPoint(20,20))):
            window.setGeometry(100,100,1200,700)
            APP.processEvents()
            grip = getattr(window,name+'_grip')
            point = grip.rect().center()
            self.assertIs(window.childAt(grip.mapTo(window,point)),grip)
            start = window.size()
            global_pos = grip.mapToGlobal(point)
            QTest.mousePress(grip,Qt.LeftButton,Qt.NoModifier,point)
            move = QMouseEvent(QEvent.MouseMove,QPointF(point+delta),QPointF(global_pos+delta),Qt.NoButton,Qt.LeftButton,Qt.NoModifier)
            APP.sendEvent(grip,move)
            QTest.mouseRelease(grip,Qt.LeftButton,Qt.NoModifier,point)
            self.assertGreater(window.width(),start.width(),name)
            self.assertGreater(window.height(),start.height(),name)
        self.assertTrue(window.navigation.footer_grip.isVisible())
        self.assertFalse(window.chrome.logo.pixmap().isNull())

    def test_settings_close_action_immediate_save_reopen(self):
        w=self.window
        w.navigation.show_settings()
        d=w.settings_window
        d.close_button_action_combo.setCurrentIndex(d.close_button_action_combo.findData('tray'))
        with patch.object(w.config_manager,'save') as save:
            d.close_button_action_combo.setCurrentIndex(d.close_button_action_combo.findData('exit'))
            self.assertEqual(save.call_args[0][0].get('runtime','close_button_action'),'exit')
        d.accept()
        w.navigation.show_settings()
        d=w.settings_window
        self.assertEqual(d.close_button_action_combo.currentData(),'exit')
        d.close_button_action_combo.setCurrentIndex(d.close_button_action_combo.findData('tray'))
        d.reject()
        self.assertEqual(w.runtime.config.get('runtime','close_button_action'),'tray')
        w.runtime.config.set('runtime','close_button_action','exit')
        with patch.object(w,'request_exit') as exit:
            w.close()
            exit.assert_called_once()
        w.runtime.config.set('runtime','close_button_action','ask')
        with patch.object(w.close_behavior,'_ask',return_value=None),patch.object(w,'request_exit') as exit:
            w.close()
            exit.assert_not_called()
        with patch.object(w.close_behavior,'_ask',return_value='tray'),patch.object(w.config_manager,'save'),patch.object(w.close_behavior,'hide_to_tray') as hide:
            w.close()
            hide.assert_called_once()
        with patch('router_app.close_behavior.QSystemTrayIcon.isSystemTrayAvailable',return_value=False):
            w.close_behavior.hide_to_tray()
            self.assertTrue(w.isMinimized())
        w.close_behavior.restore()
        self.assertTrue(w.isVisible())

    def test_only_top_header_corners_are_clipped(self):
        from PySide6.QtWidgets import QHeaderView
        w=self.window
        tabs=w.packet_capture_page.openwrt_tab.tabs
        for theme in ('dark.qss','light.qss'):
            w.apply_appearance(100,'default',theme)
            w.resize(1280,720)
            for index in (1,2,4):
                tabs.setCurrentIndex(index)
                APP.processEvents()
                if index == 1:
                    from modules.main_pages.packet_capture.openwrt.router_signal_page import RouterSignalPage
                    current = tabs.currentWidget()
                    signals = current if isinstance(current, RouterSignalPage) else current.findChild(RouterSignalPage)
                    self.assertIsNotNone(signals)
                    signals.stack.setCurrentIndex(2)  # The scan table lives in the bridge subpage.
                    APP.processEvents()
                headers=[h for h in tabs.currentWidget().findChildren(QHeaderView)
                         if h.orientation()==Qt.Horizontal and h.isVisible()]
                self.assertTrue(headers,(theme,index))
                for header in headers:
                    mask=header.mask()
                    self.assertFalse(mask.isEmpty(),(theme,index))
                    self.assertFalse(mask.contains(QPoint(0,0)))
                    self.assertFalse(mask.contains(QPoint(header.width()-1,0)))
                    self.assertTrue(mask.contains(QPoint(0,header.height()-1)))
                    self.assertTrue(mask.contains(QPoint(header.width()-1,header.height()-1)))
                writer=QImageWriter(str(Path(__file__).parent/('headers-'+theme+'-'+str(index)+'.png')),b'png')
                writer.setCompression(0)
                self.assertTrue(writer.write(w.grab().toImage()))

    def test_settings_and_sidebar_render_in_both_themes(self):
        w=self.window
        for theme in ('dark.qss','light.qss'):
            w.apply_appearance(100,'default',theme)
            w.resize(1280,720)
            w.navigation.show_settings()
            APP.processEvents()
            d=w.settings_window
            self.assertEqual(len(d.nav_panel.buttons),3)
            if theme=='light.qss':
                image=w.navigation.sidebar.grab().toImage()
                self.assertEqual(image.pixelColor(3,image.height()//2).name(),'#ffffff')
            writer=QImageWriter(str(Path(__file__).parent/('details-'+theme+'.png')),b'png');writer.setCompression(0)
            self.assertTrue(writer.write(w.grab().toImage()))
            d.reject()

if __name__=='__main__':unittest.main()
