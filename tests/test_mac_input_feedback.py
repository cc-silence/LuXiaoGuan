"""MAC feedback through real editor buttons, without router operations."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from PySide6.QtWidgets import QWidget
from tests.test_wireless_ui import APP, environment
from tests.test_signal_actions_ui import PendingSetup
from modules.capture_services.openwrt.wireless_setup_models import WirelessState
from modules.main_pages.packet_capture.openwrt.signal_setup_actions import SignalSetupActions
from modules.ui_scale_manager import UiScaleManager


class MacInputFeedbackTests(unittest.TestCase):
    def editor(self, light=False, existing=None, mode="allow"):
        parent = QWidget()
        parent.scale_manager = UiScaleManager(100)
        from modules.config_manager import ConfigManager
        from modules.dialog_theme import build_base_stylesheet
        parent.setStyleSheet(build_base_stylesheet(ConfigManager(), light_theme=light,
                                                   scale_manager=parent.scale_manager))
        env = environment()
        env.data["rows"][0]["macfilter"] = mode
        if existing is not None:
            env.data["rows"][0]["maclist"] = existing
        service = PendingSetup()
        service.snapshot = WirelessState(environment=env)
        service.cancel = Mock()
        panels = SimpleNamespace(page=parent, _show=Mock())
        actions = SignalSetupActions(panels, service)
        actions._save = Mock()
        actions.access_point("radio7", "5G", SimpleNamespace(section="home", ssid="Home"))
        dialog = panels._show.call_args.args[0]
        dialog.expert_section.set_expanded(True, animate=False)
        dialog.show()
        APP.processEvents()
        self.addCleanup(parent.deleteLater)
        self.addCleanup(dialog.reject)
        return dialog, dialog.expert_fields.controls["maclist"], actions, service

    def test_both_buttons_block_invalid_rows_and_recover_after_correction(self):
        for light in (False, True):
            with self.subTest(light=light):
                dialog, listing, actions, service = self.editor(light)
                listing.set_values(["wrong", "aa:bb:cc:dd:ee:ff", "bad"])
                for button in (dialog.save_button, dialog.save_only_button):
                    button.click()
                    actions._save.assert_not_called()
                    self.assertEqual([bool(edit.property("inputError")) for _, edit in listing._rows],
                                     [True, False, True])
                    self.assertEqual(dialog.status_label.objectName(), "settingsValidationError")
                    self.assertIn("AA:BB:CC:DD:EE:FF", dialog.status_label.text())
                    APP.processEvents()
                    self.assertEqual(dialog.property("settingsLightTheme"), light)
                    color = dialog.status_label.palette().color(dialog.status_label.foregroundRole())
                    self.assertEqual(color.name(), "#c62828" if light else "#ff8585")
                listing._rows[0][1].setText("02:11:22:33:44:55")
                self.assertFalse(listing._rows[0][1].property("inputError"))
                self.assertTrue(listing._rows[2][1].property("inputError"))
                listing._remove(listing._rows[2][0])
                self.assertTrue(dialog.status_label.isHidden())
                dialog.save_only_button.click()
                self.assertFalse(actions._save.call_args.kwargs["apply"])
                dialog.save_button.click()
                self.assertTrue(actions._save.call_args.kwargs["apply"])
                self.assertEqual(actions._save.call_args.args[1].options["maclist"],
                                 "02:11:22:33:44:55 aa:bb:cc:dd:ee:ff")

    def test_empty_optional_list_and_required_existing_list(self):
        dialog, listing, actions, service = self.editor(existing=["02:11:22:33:44:55"])
        listing._rows[0][1].clear()
        dialog.save_button.click()
        actions._save.assert_not_called()
        self.assertTrue(listing._rows[0][1].property("inputError"))
        self.assertEqual(dialog.status_label.objectName(), "settingsValidationError")
        listing._rows[0][1].setText("02:11:22:33:44:55")
        self.assertTrue(dialog.status_label.isHidden())
        dialog.save_button.click()
        self.assertNotIn("maclist", actions._save.call_args.args[1].options)
        dialog2, listing2, actions2, service2 = self.editor()
        dialog2.save_button.click()
        actions2._save.assert_called_once()
        self.assertFalse(bool(listing2._rows[0][1].property("inputError")))

    def test_multiple_addresses_in_one_row_keep_existing_format_rules(self):
        dialog, listing, actions, service = self.editor()
        listing._rows[0][1].setText("02:11:22:33:44:55 aa:bb:cc:dd:ee:ff")
        self.assertFalse(bool(listing._rows[0][1].property("inputError")))
        dialog.save_button.click()
        actions._save.assert_called_once()
        listing._rows[0][1].setText("02-11-22-33-44-55")
        self.assertTrue(listing._rows[0][1].property("inputError"))
        self.assertEqual(dialog.status_label.objectName(), "settingsValidationError")


    def test_disabled_filter_grays_all_list_controls_and_preserves_contents(self):
        from PySide6.QtWidgets import QPushButton
        from pathlib import Path
        for light in (False, True):
            dialog, listing, actions, _ = self.editor(light, existing=["02:11:22:33:44:55"], mode="disable")
            mode=dialog.expert_fields.controls["macfilter"]
            self.assertFalse(listing.isEnabled())
            self.assertFalse(listing._rows[0][1].isEnabled())
            self.assertTrue(all(not button.isEnabled() for button in listing.findChildren(QPushButton)))
            original=listing.values()
            for value in ("allow", "deny"):
                mode.setCurrentIndex(mode.findData(value))
                self.assertTrue(listing.isEnabled())
                self.assertTrue(listing.add_button.isEnabled())
                self.assertEqual(listing.values(),original)
            mode.setCurrentIndex(mode.findData("disable"))
            self.assertFalse(listing.isEnabled())
            self.assertEqual(listing.values(),original)
            self.assertNotIn("maclist",dialog.expert_fields.values())
            dialog.save_only_button.click()
            actions._save.assert_called_once()
            output=Path("build/mac-filter-disabled");output.mkdir(parents=True,exist_ok=True)
            dialog.scroll_area.ensureWidgetVisible(listing)
            APP.processEvents()
            dialog.grab().save(str(output/("light.png" if light else "dark.png")))

    def test_inherited_mode_tracks_original_and_disabled_list_does_not_block_save(self):
        for original in (None, "disable", "allow", "deny"):
            dialog, listing, actions, _ = self.editor(mode=original)
            mode=dialog.expert_fields.controls["macfilter"]
            mode.setCurrentIndex(0)
            self.assertEqual(listing.isEnabled(),original in ("allow","deny"))
            mode.setCurrentIndex(mode.findData("allow"))
            listing._rows[0][1].setText("invalid")
            self.assertTrue(listing.validation_message)
            mode.setCurrentIndex(mode.findData("disable"))
            dialog.save_only_button.click()
            actions._save.assert_called_once()
            self.assertNotIn("maclist",actions._save.call_args.args[1].options)


if __name__ == "__main__":
    unittest.main()
