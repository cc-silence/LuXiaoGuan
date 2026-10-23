"""Refreshing detected firmware capabilities keeps one live UI update path."""
import unittest
from unittest.mock import Mock
from tests.test_wireless_ui import APP, environment
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog, RelaySetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_ap, configure_relay
from modules.main_pages.packet_capture.openwrt.signal_radio_fields import configure_radio_fields


class WirelessEditorRefreshTests(unittest.TestCase):

    def test_edit_summary_is_replaced_on_reload(self):
        from types import SimpleNamespace
        row = SimpleNamespace(section="home", ssid="Home")
        dialog = AccessPointSetupDialog("radio7", "5G", row=row)
        self.addCleanup(dialog.reject)
        self.addCleanup(dialog.deleteLater)
        env = environment()
        configure_ap(dialog, env)
        first = dialog.summary.text()
        configure_ap(dialog, env)
        self.assertEqual(dialog.summary.text(), first)
        env.data["radios"][0]["channel"] = "40"
        configure_ap(dialog, env)
        self.assertTrue(dialog.summary.text().endswith("40"))
        self.assertNotIn("36", dialog.summary.text())


    def test_ap_refresh_restores_newly_available_security_choices(self):
        dialog = AccessPointSetupDialog("radio7", "5G")
        self.addCleanup(dialog.reject)
        self.addCleanup(dialog.deleteLater)
        env = environment()
        env.data["features"].update(ap_sae=False, ap_owe=False)
        configure_ap(dialog, env)
        self.assertEqual(dialog.encryption.findData("sae"), -1)
        configure_ap(dialog, environment())
        for value in ("sae", "sae-mixed", "owe"):
            self.assertGreaterEqual(dialog.encryption.findData(value), 0)

    def test_advanced_reload_discards_previous_unsaved_text(self):
        dialog = AccessPointSetupDialog("radio7", "5G")
        self.addCleanup(dialog.reject)
        self.addCleanup(dialog.deleteLater)
        env = environment()
        configure_ap(dialog, env)
        control = dialog.expert_fields.controls["max_inactivity"]
        control.setText("86400")
        configure_ap(dialog, env)
        self.assertEqual(control.text(), "")
        self.assertNotIn("max_inactivity", dialog.expert_fields.values())

    def test_radio_refresh_keeps_one_bandwidth_updater(self):
        dialog = AccessPointSetupDialog("radio7", "5G")
        self.addCleanup(dialog.reject)
        self.addCleanup(dialog.deleteLater)
        env = environment()
        radio = env.data["radios"][0]
        radio["htmode"] = "HE80"
        features = dict(ap_11ax=True, ap_11ac=True, ap_11be=False)
        for _ in range(3):
            configure_radio_fields(dialog, radio, features)
        dialog.bandwidth_combo.clear = Mock(wraps=dialog.bandwidth_combo.clear)
        dialog.standard_combo.setCurrentIndex(dialog.standard_combo.findData("HE"))
        self.assertEqual(dialog.bandwidth_combo.clear.call_count, 1)

    def test_ap_refresh_keeps_one_encryption_updater(self):
        dialog = AccessPointSetupDialog("radio7", "5G")
        self.addCleanup(dialog.reject)
        self.addCleanup(dialog.deleteLater)
        for _ in range(3):
            configure_ap(dialog, environment())
        dialog.password.setEnabled = Mock(wraps=dialog.password.setEnabled)
        dialog.encryption.setCurrentIndex(dialog.encryption.findData("none"))
        self.assertEqual(dialog.password.setEnabled.call_count, 1)
        self.assertFalse(dialog.password.isEnabled())

    def test_relay_refresh_keeps_one_zone_updater(self):
        dialog = RelaySetupDialog("Upstream", radio_name="radio7")
        self.addCleanup(dialog.reject)
        self.addCleanup(dialog.deleteLater)
        for _ in range(3):
            configure_relay(dialog, environment())
        dialog.zone.setCurrentIndex = Mock(wraps=dialog.zone.setCurrentIndex)
        dialog.bridge_mode.setCurrentIndex(dialog.bridge_mode.findData("relayd"))
        self.assertEqual(dialog.zone.setCurrentIndex.call_count, 1)
        self.assertEqual(dialog.zone.currentData(), "lan")
