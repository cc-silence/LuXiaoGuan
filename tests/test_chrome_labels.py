"""Main-window identity, persistent scale and cleanup-row rendered contracts."""
from pathlib import Path
import unittest
from unittest.mock import Mock
from PySide6.QtWidgets import QLabel
from tests import test_standalone as standalone
from tests import test_signal_actions_ui as signal_ui
from modules.config_manager import ConfigManager,BASE_DIR
from modules.desktop_shell.initial_ui_scale import initial_ui_scale
from router_app.identity import DISPLAY_NAME,WINDOW_TITLE
from router_app.settings import RouterSettings

class ChromeLabelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window=standalone.StandaloneTests.window
    @classmethod
    def tearDownClass(cls):standalone.StandaloneTests.tearDownClass()

    def test_title_version_and_render_both_themes_and_scales(self):
        window=self.window
        output=BASE_DIR/"build/chrome-label-review";output.mkdir(parents=True,exist_ok=True)
        for theme in ("dark.qss","light.qss"):
            for scale in (100,150):
                window.apply_appearance(scale,"default",theme)
                window.resize(round(1280*scale/100),round(720*scale/100))
                standalone.APP.processEvents()
                self.assertEqual(window.title_label.text(),WINDOW_TITLE)
                self.assertEqual(window.windowTitle(),DISPLAY_NAME)
                self.assertEqual(window.findChild(QLabel,"labelVersion").text(),"v1.0.0_alpha")
                self.assertGreaterEqual(window.title_label.font().pointSize(),round(12*scale/100))
                self.assertGreaterEqual(window.title_label.width(),window.title_label.fontMetrics().horizontalAdvance(WINDOW_TITLE))
                window.grab().save(str(output/(theme+"-"+str(scale)+".png")))
                host=window.packet_capture_page.openwrt_tab
                host.tabs.setCurrentIndex(1)
                standalone.APP.processEvents()
                signal_page=host._ensure_subpage("signal")
                first,second=signal_page.relocation_actions.button,signal_page.orphan_actions.button
                self.assertEqual(first.y(),second.y())
                self.assertEqual(first.height(),second.height())
                self.assertLessEqual(abs(first.width()-second.width()),1)
                window.grab().save(str(output/("cleanup-"+theme+"-"+str(scale)+".png")))
                host.tabs.setCurrentIndex(0)


    def test_scale_100_persists_and_all_test_writes_are_isolated(self):
        self.window.apply_appearance(125,"default","dark.qss")
        dialog=RouterSettings(self.window)
        dialog.ui_scale_combo.setCurrentIndex(dialog.ui_scale_combo.findData(100))
        dialog.reject();dialog.deleteLater()
        standalone.APP.processEvents()
        manager=ConfigManager()
        root=Path(standalone.StandaloneTests.temp.name)
        self.assertTrue(manager._pair.primary.is_relative_to(root))
        self.assertTrue(manager._pair.mirror.is_relative_to(root))
        self.assertEqual(manager.load().get("ui","scale"),"100")
        self.assertEqual(initial_ui_scale(ConfigManager(),None),100)

class CleanupRowTests(unittest.TestCase):
    create_page=signal_ui.SignalActionTests.create_page

    def test_both_buttons_share_row_and_equal_size_in_both_themes(self):
        output=BASE_DIR/"build/cleanup-row-review";output.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            setup=signal_ui.PendingSetup()
            setup.orphan_preview=Mock(return_value=True)
            setup.cleanup_orphans=Mock(return_value=True)
            setup.relocation_preview=Mock(return_value=True)
            page,_=self.create_page(light,setup)
            for percent in (100,150):
                from modules.ui_scale_manager import UiScaleManager
                page.parentWidget().scale_manager=UiScaleManager(percent)
                page.parentWidget().scale_manager.apply_bound_metrics(page)
                for width in (850,1250):
                    page.resize(width,680);standalone.APP.processEvents()
                    first,second=page.relocation_actions.button,page.orphan_actions.button
                    self.assertEqual(first.parentWidget(),second.parentWidget())
                    self.assertEqual(first.y(),second.y())
                    self.assertEqual(first.height(),second.height())
                    self.assertLessEqual(abs(first.width()-second.width()),1)
                    self.assertGreater(second.x(),first.x()+first.width())
                    self.assertGreaterEqual(second.width(),second.fontMetrics().horizontalAdvance(second.text()))
                page.grab().save(str(output/(str(light)+"-"+str(percent)+".png")))
