"""Offline application integration checks using isolated configuration."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from modules import resources_rc
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
from modules.config_manager import ConfigManager, BASE_DIR
from modules.config_services.config_store import user_config_dir
from modules.config_services.capture_settings_manager import CaptureSettingsManager
from modules.desktop_shell.single_instance import SingleInstanceController
from modules.platform_services import local_gpo_firewall
from router_app.runtime import RouterRuntime
from router_app.window import RouterWindow
from router_app.settings import RouterSettings
APP = QApplication.instance() or QApplication([])
from PySide6.QtGui import QFontDatabase
for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
    font = Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"/name
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))


class StandaloneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {'ROUTERMANAGER_USER_CONFIG_DIR': str(Path(cls.temp.name)/'user')})
        cls.env.start()
        cls.mirror = patch("modules.config_manager.SETTINGS_FILE", Path(cls.temp.name)/"mirror/settings.ini")
        cls.mirror.start()
        cls.runtime = RouterRuntime('1.0.0_alpha', APP)
        cls.window = RouterWindow(cls.runtime, '1.0.0_alpha')
        cls.window.show()
        APP.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.runtime.exit_application()
        deadline = time.monotonic()+12
        while not cls.runtime._ready_to_close and time.monotonic()<deadline:
            APP.processEvents();time.sleep(.01)
        assert cls.runtime._ready_to_close, 'Background cleanup did not finish'
        cls.window.deleteLater();cls.runtime.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        cls.mirror.stop();cls.env.stop();cls.temp.cleanup()

    def test_01_identity_and_config_isolation(self):
        self.assertEqual(BASE_DIR, Path(__file__).resolve().parents[1])
        self.assertTrue(SingleInstanceController._build_server_name().startswith('RouterManager.'))
        self.assertEqual(local_gpo_firewall.STARTUP_OWNER,'routermanager-startup')
        self.assertIn('RouterManager',local_gpo_firewall.OWNER_DESCRIPTION_PREFIX)
        self.assertEqual(user_config_dir(),Path(self.temp.name)/'user')
        self.assertTrue(self.runtime.capture_service._openwrt_credential_target({'host':'router','port':22,'username':'root'}).startswith('RouterManager:'))

    def test_02_seven_pages_theme_and_scale(self):
        tabs=self.window.packet_capture_page.openwrt_tab.tabs
        self.assertEqual(tabs.count(),7)
        self.assertEqual([tabs.tabText(i) for i in range(7)], ["路由器登录", "常用设置", "信号管理", "文件管理", "路由器终端", "抓包", "抓包设置"])
        theme=os.environ.get('ROUTERMANAGER_TEST_THEME','dark.qss')
        for index in range(7):
            tabs.setCurrentIndex(index);APP.processEvents()
            self.assertIsNotNone(tabs.currentWidget())
        container = self.window.packet_capture_page.openwrt_tab
        self.assertIs(tabs.widget(1), container.common_page)
        self.assertIs(tabs.widget(3), container.files_page)
        self.assertIs(tabs.widget(4), container.terminal_page)
        self.assertIs(tabs.widget(5), container.capture_page)
        self.assertIs(tabs.widget(6), container.settings_page)
        self.window.hide()
        for scale in (100,125,150,175,100):
            self.window.apply_appearance(scale,'default',theme)
            APP.processEvents()
            self.assertEqual(self.window.packet_capture_page.scale_manager.global_scale,scale)
            from modules.ui_scale_manager import UiScaleManager
            for index in range(7):
                self.assertEqual(UiScaleManager.for_widget(tabs.widget(index)).global_scale,scale)
        tabs.setCurrentIndex(0);self.window.show();APP.processEvents()
        if os.environ.get('ROUTERMANAGER_CAPTURE_PREVIEW') == '1':
            self.window.grab().save(str(BASE_DIR/'tests'/('router-'+theme+'.png')))

    def test_03_immediate_settings_survive_close(self):
        self.window.apply_appearance(100,'default','dark.qss')
        dialog=RouterSettings(self.window)
        dialog.ui_scale_combo.setCurrentIndex(dialog.ui_scale_combo.findData(150))
        dialog.ui_font_combo.setCurrentIndex(dialog.ui_font_combo.findData('Consolas'))
        self.assertEqual(self.window.scale_manager.global_scale,150)
        self.assertEqual(self.window.scale_manager.font_family,'Consolas')
        dialog.reject()
        self.assertEqual(self.window.scale_manager.global_scale,150)
        self.assertEqual(self.window.scale_manager.font_family,'Consolas')
        saved=ConfigManager().load()
        self.assertEqual(saved.get('ui','scale'),'150')
        self.assertEqual(saved.get('ui','font_family'),'Consolas')
        dialog.deleteLater();APP.processEvents()

    def test_04_save_reopen_and_actual_page_scale(self):
        dialog=RouterSettings(self.window)
        dialog.ui_scale_combo.setCurrentIndex(dialog.ui_scale_combo.findData(125))
        dialog.ui_font_combo.setCurrentIndex(dialog.ui_font_combo.findData('bold'))
        dialog.accept();dialog.deleteLater();APP.processEvents()
        config=ConfigManager().load()
        self.assertEqual(config.get('ui','scale'),'125')
        self.assertEqual(config.get('ui','font_family'),'bold')
        reopened=RouterSettings(self.window)
        self.assertEqual(reopened.ui_scale_combo.currentData(),125)
        self.assertEqual(reopened.ui_font_combo.currentData(),'bold')
        self.assertEqual(self.window.packet_capture_page.scale_manager.global_scale,125)
        reopened.reject();reopened.deleteLater();APP.processEvents()

    def test_05_update_uses_independent_published_channel(self):
        config=json.loads((BASE_DIR/'config/app_update.json').read_text(encoding='utf-8'))
        from modules.app_update_services.channel import GATEWAY
        from modules.app_update_services.manifest import https_url
        self.assertTrue(config['enabled'])
        self.assertEqual(config['manifest_url'],https_url(GATEWAY+'/manifest'))
        self.assertTrue(GATEWAY.startswith('https://routermanager-updates.'))
        from modules.app_update_services.manifest import parse_payload
        with self.assertRaises(ValueError):parse_payload({'schema':1,'product':'WinBox','platform':'win-x64'})

    def test_06_firewall_gate_and_export_owner(self):
        self.assertEqual(self.runtime.capture_service.network_ready,self.runtime._startup_firewall_controller.wait_ready)
        self.assertIs(self.runtime.log_export_service.parent(),self.runtime)
        self.assertFalse(self.runtime.log_export_service.active)
        self.assertFalse(self.runtime.idle_blockers())

    def test_07_default_capture_config_is_independent(self):
        manager=CaptureSettingsManager()
        data=manager.load()
        self.assertIn('openwrt',data)
        self.assertNotIn('crack',data)
        self.assertNotIn('WinBox_python',manager.storage_hint())

if __name__=='__main__':unittest.main()
