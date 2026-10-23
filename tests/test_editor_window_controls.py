"""Real window-state transitions, shared editing controls and theme/scale rendering."""
from types import SimpleNamespace
import unittest
from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QWidget
from PySide6.QtTest import QTest
from tests.test_standalone import APP
from modules.config_manager import ConfigManager
from modules.dialog_theme import build_base_stylesheet
from modules.ui_scale_manager import UiScaleManager
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog, RelaySetupDialog
from modules.main_pages.packet_capture.openwrt.router_files_page import ValueDialog


class EditorWindowControlTests(unittest.TestCase):
    def test_all_editors_inherit_controls_and_short_confirmations_do_not(self):
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        dialogs = [AccessPointSetupDialog("radio7", "5G", parent),
                   RelaySetupDialog("Upstream", parent, radio_name="radio7"),
                   ValueDialog("Edit", "value", parent), FramelessDialog("Editor", parent)]
        for dialog in dialogs:
            self.addCleanup(dialog.deleteLater)
            self.assertIsNotNone(dialog.window_controls)
            self.assertTrue(dialog.windowFlags() & Qt.WindowMinimizeButtonHint)
            self.assertTrue(dialog.windowFlags() & Qt.WindowMaximizeButtonHint)
            self.assertFalse(dialog.window_controls.minimize_button.autoDefault())
            self.assertFalse(dialog.window_controls.maximize_button.autoDefault())
        for dialog in (ConfirmDialog("Confirm", "Text", parent), MessageDialog("Message", "Text", parent)):
            self.addCleanup(dialog.deleteLater)
            self.assertIsNone(dialog.window_controls)


    def test_modal_editor_minimizes_owner_group_without_hiding_a_modal_blocker(self):
        parent = QWidget()
        parent.show()
        dialog = ValueDialog("Edit", "Unsaved", parent)
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.show()
        APP.processEvents()
        try:
            dialog.window_controls.minimize_button.click()
            APP.processEvents()
            self.assertTrue(parent.isMinimized())
            self.assertFalse(dialog.isMinimized())
            self.assertEqual(dialog.edit.text(), "Unsaved")
            parent.showNormal()
            APP.processEvents()
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog.edit.text(), "Unsaved")
        finally:
            dialog.reject()
            parent.deleteLater()
            APP.processEvents()

    def test_maximize_restore_and_minimize_preserve_pending_input(self):
        dialog = AccessPointSetupDialog("radio7", "5G")
        self.addCleanup(dialog.deleteLater)
        dialog.name_edit.setText("Unsaved")
        dialog.show()
        APP.processEvents()
        original = dialog.geometry()
        controls = dialog.window_controls
        controls.maximize_button.click()
        APP.processEvents()
        self.assertTrue(dialog.isMaximized())
        self.assertTrue(all(not grip.isVisible() for grip in dialog._all_grips()))
        controls.maximize_button.click()
        APP.processEvents()
        self.assertFalse(dialog.isMaximized())
        self.assertEqual(dialog.geometry(), original)
        dialog.set_busy(True, "Waiting")
        controls.minimize_button.click()
        APP.processEvents()
        self.assertTrue(dialog.isMinimized())
        self.assertTrue(dialog._busy)
        dialog.showNormal()
        APP.processEvents()
        self.assertEqual(dialog.name_edit.text(), "Unsaved")
        self.assertTrue(dialog._busy)
        dialog.set_busy(False)
        dialog.reject()

    def test_title_double_click_toggles_maximized_state(self):
        dialog = FramelessDialog("Editor")
        self.addCleanup(dialog.deleteLater)
        dialog.resize(500, 300)
        dialog.show()
        APP.processEvents()
        QTest.mouseDClick(dialog.title_label, Qt.LeftButton)
        APP.processEvents()
        self.assertTrue(dialog.isMaximized())
        QTest.mouseDClick(dialog.title_label, Qt.LeftButton)
        APP.processEvents()
        self.assertFalse(dialog.isMaximized())
        dialog.reject()

    def test_theme_and_scale_controls_draw_without_clipping(self):
        for scale in (100, 150):
            for light in (False, True):
                with self.subTest(scale=scale, light=light):
                    parent = QWidget()
                    parent.scale_manager = UiScaleManager(scale)
                    parent.setStyleSheet(build_base_stylesheet(ConfigManager(), light_theme=light,
                                                              scale_manager=parent.scale_manager))
                    dialog = AccessPointSetupDialog("radio7", "5G", parent)
                    dialog.show()
                    APP.processEvents()
                    for button in (dialog.window_controls.minimize_button, dialog.window_controls.maximize_button):
                        self.assertTrue(button.isVisible())
                        self.assertFalse(button.icon().isNull())
                        self.assertGreaterEqual(button.width(), parent.scale_manager.px(28))
                        self.assertTrue(dialog.title_bar.rect().contains(
                            button.mapTo(dialog.title_bar, button.rect().topLeft())))
                    self.assertFalse(dialog.grab().isNull())
                    dialog.reject()
                    parent.deleteLater()
                    APP.processEvents()
