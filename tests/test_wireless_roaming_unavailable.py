"""Unavailable roaming controls explain support without changing saved values."""
import unittest
from PySide6.QtWidgets import QWidget, QVBoxLayout
from tests.test_wireless_ui import APP
from modules.capture_services.openwrt.wireless_options import AP_ADVANCED
from modules.main_pages.packet_capture.openwrt.signal_advanced_fields import AdvancedFields

KEYS = ("ieee80211k", "rrm_neighbor_report", "rrm_beacon_report", "bss_transition")


class RoamingUnavailableTests(unittest.TestCase):
    def create(self):
        page = QWidget()
        self.addCleanup(page.deleteLater)
        fields = AdvancedFields(page, QVBoxLayout(page))
        radio = {"type": "mac80211", "option_support": {
            "interface": {spec.key: True for spec in AP_ADVANCED},
            "device": {spec.key: True for spec in AP_ADVANCED}}}
        return fields, radio

    def test_explicit_unsupported_feature_is_disabled_and_explained(self):
        fields, radio = self.create()
        fields.configure(radio, {}, {"ap_eap": False})
        for key in KEYS:
            control = fields.controls[key]
            self.assertFalse(control.isEnabled())
            self.assertEqual(control.objectName(), "wirelessRoamingOption")
            self.assertIn("\u5f53\u524d\u8def\u7531\u5668\u4e0d\u652f\u6301", control.text())
            control.click()
            self.assertIsNone(control.value())
            self.assertNotIn(key, fields.values())

    def test_missing_evidence_does_not_claim_unsupported(self):
        fields, radio = self.create()
        fields.configure(radio, {}, {})
        for key in KEYS:
            self.assertIn("\u5c1a\u672a\u786e\u8ba4\u652f\u6301", fields.controls[key].text())
            self.assertFalse(fields.controls[key].isEnabled())

    def test_schema_rejection_preserves_known_value(self):
        fields, radio = self.create()
        for key in KEYS:
            radio["option_support"]["interface"][key] = False
        fields.configure(radio, dict.fromkeys(KEYS, "1"), {"ap_eap": True})
        for key in KEYS:
            self.assertIn("\u5f53\u524d\u8def\u7531\u5668\u4e0d\u652f\u6301", fields.controls[key].text())
            self.assertEqual(fields.controls[key].value(), "1")
            self.assertNotIn(key, fields.values())

    def test_reconfiguration_restores_supported_controls_without_old_reason(self):
        fields, radio = self.create()
        fields.configure(radio, {}, {"ap_eap": False})
        fields.configure(radio, {}, {"ap_eap": True})
        for key in KEYS:
            control = fields.controls[key]
            self.assertTrue(control.isEnabled())
            self.assertEqual(control.text(), "\u6cbf\u7528\u7cfb\u7edf\u8bbe\u7f6e")
            control.click()
            self.assertEqual(fields.values()[key], "1")


if __name__ == "__main__":
    unittest.main()
