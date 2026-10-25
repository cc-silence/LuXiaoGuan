"""Regression of the actual owner contract and main-window chrome."""
import os
import sys
import threading
import time
import unittest
from multiprocessing import Pipe
from unittest.mock import Mock,patch
from pathlib import Path
from tests.test_standalone import APP
from PySide6.QtCore import Qt,QEvent,QCoreApplication
from PySide6.QtTest import QTest
from modules.platform_services.local_gpo_firewall import STARTUP_OWNER
from modules.platform_services.firewall_session_worker import validate_request,serve_connection
from modules.platform_services.firewall_session import FirewallSession


class FirewallOwnerTests(unittest.TestCase):
    def request(self):
        return dict(operation='ensure',owner=STARTUP_OWNER,rules=[dict(program=str(Path(sys.executable).resolve()),name='RouterManager test')])

    def test_production_owner_passes_validation_and_foreign_owner_is_rejected(self):
        request=self.request()
        self.assertIs(validate_request(request),request)
        for owner in ('startup','foreign','routermanager-startup|invalid'):
            with self.assertRaises(ValueError):validate_request(dict(request,owner=owner))

    def test_actual_client_and_worker_both_accept_the_same_request(self):
        client,worker=Pipe(duplex=True)
        apply=Mock(return_value={'success':True,'count':1})
        thread=threading.Thread(target=serve_connection,args=(worker,),kwargs={'apply':apply},daemon=True)
        thread.start()
        endpoint=Mock(address='fixture');endpoint.accept.return_value=client
        host=Mock(pid=os.getpid());host.alive.return_value=True
        session=FirewallSession(launcher=Mock(return_value=host),endpoint_factory=lambda:endpoint)
        try:
            request=self.request()
            self.assertTrue(session.request(request,5000)['success'])
            apply.assert_called_once_with(request)
        finally:
            session.close();client.close();thread.join(timeout=3);worker.close()
        self.assertFalse(thread.is_alive())


class ChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from router_app.runtime import RouterRuntime
        from router_app.window import RouterWindow
        import tempfile
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {"ROUTERMANAGER_USER_CONFIG_DIR": str(Path(cls.temp.name)/"user")})
        cls.mirror = patch("modules.config_manager.SETTINGS_FILE", Path(cls.temp.name)/"mirror/settings.ini")
        cls.env.start();cls.mirror.start()
        cls.runtime=RouterRuntime('1.0.0_alpha',APP)
        for key,value in [('theme','dark.qss'),('scale','100'),('font_family','default')]:cls.runtime.config.set('ui',key,value)
        cls.window=RouterWindow(cls.runtime,'1.0.0_alpha')
        cls.window.show();APP.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.runtime.exit_application();deadline=time.monotonic()+10
        while not cls.runtime._ready_to_close and time.monotonic()<deadline:APP.processEvents();time.sleep(.01)
        assert cls.runtime._ready_to_close
        cls.window.deleteLater();cls.runtime.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        cls.mirror.stop();cls.env.stop();cls.temp.cleanup()

    def test_frameless_window_and_original_icon_buttons(self):
        self.assertTrue(self.window.windowFlags() & Qt.FramelessWindowHint)
        self.assertEqual(self.window.windowType(),Qt.Window)
        chrome=self.window.chrome
        for button in (chrome.privacy_button,chrome.theme_button,chrome.settings_button,chrome.minimize_button,chrome.maximize_button):
            self.assertEqual(button.text(),'');self.assertFalse(button.icon().isNull())
            self.assertEqual(button.width(),28);self.assertTrue(button.toolTip())
        initial=chrome.privacy_button.icon().cacheKey()
        chrome.privacy_button.click();self.assertTrue(self.runtime.display_privacy.enabled)
        self.assertNotEqual(initial,chrome.privacy_button.icon().cacheKey())
        chrome.privacy_button.click();self.assertFalse(self.runtime.display_privacy.enabled)
        with patch.object(self.window.navigation,'show_settings') as settings:
            chrome.settings_button.click();settings.assert_called_once_with(update=False)

    def test_left_navigation_and_dialog_settings_immediate_save(self):
        nav=self.window.navigation
        self.assertEqual(nav.sidebar.objectName(),'leftMenuBg')
        self.assertTrue(nav.sidebar.isHidden())
        self.assertIs(nav.stack.currentWidget(),self.window.packet_capture_page)
        nav.toggle_button.click();self.assertTrue(nav.expanded)
        self.assertTrue(nav.settings_button.text())
        nav.toggle_button.click();self.assertFalse(nav.expanded)
        self.window.chrome.settings_button.click();APP.processEvents()
        controller=self.window.settings_window
        self.assertIsNotNone(controller)
        self.assertIs(nav.stack.currentWidget(),self.window.packet_capture_page)
        self.assertTrue(controller.isWindow())
        self.assertTrue(controller.isVisible())
        self.assertTrue(controller.title_bar.isVisible())
        self.assertIs(nav.router_page,self.window.packet_capture_page)
        controller.ui_scale_combo.setCurrentIndex(controller.ui_scale_combo.findData(125))
        self.assertEqual(self.window.scale_manager.global_scale,125)
        self.window.chrome.settings_button.click();APP.processEvents()
        self.assertIs(self.window.settings_window,controller)
        controller.close_btn.click();APP.processEvents()
        self.assertEqual(self.window.scale_manager.global_scale,125)
        self.assertIs(nav.stack.currentWidget(),self.window.packet_capture_page)
        self.assertIsNone(self.window.settings_window)
        self.window.openSettingsWindow(update=True)
        self.assertEqual(self.window.settings_window.pages.currentIndex(),2)
        QTest.keyClick(self.window.settings_window,Qt.Key_Escape);self.assertIsNone(self.window.settings_window)

    def test_maximize_restore_grips_and_escape_do_not_exit(self):
        self.window.showNormal();APP.processEvents()
        chrome=self.window.chrome
        chrome.maximize_button.click();APP.processEvents();self.assertTrue(self.window.isMaximized())
        self.assertTrue(all(grip.isHidden() for grip in self.window._all_grips()))
        chrome.maximize_button.click();APP.processEvents();self.assertFalse(self.window.isMaximized())
        self.assertTrue(all(not grip.isHidden() for grip in self.window._all_grips()))
        QTest.keyClick(self.window,Qt.Key_Escape);QTest.keyClick(self.window,Qt.Key_Return)
        self.assertFalse(self.runtime._exiting)

    def test_dark_light_icons_and_preview(self):
        for theme in ('dark.qss','light.qss'):
            self.window.apply_appearance(100,'default',theme);APP.processEvents()
            for button in (self.window.chrome.privacy_button,self.window.chrome.theme_button,self.window.chrome.settings_button):
                image=button.icon().pixmap(20,20).toImage()
                values=[image.pixelColor(x,y).lightness() for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x,y).alpha()>128]
                self.assertTrue(values)
                self.assertLess(max(values),180) if theme=='light.qss' else self.assertGreater(max(values),200)
            from PySide6.QtGui import QImageWriter
            writer=QImageWriter(str(Path(__file__).parent/('fixed-chrome-'+theme+'.png')),b'png');writer.setCompression(0)
            self.assertTrue(writer.write(self.window.grab().toImage()),writer.errorString())

if __name__=='__main__':unittest.main()
