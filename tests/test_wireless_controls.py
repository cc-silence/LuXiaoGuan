"""Contracts for OpenWrt-style controls and untouched device configuration."""
from types import SimpleNamespace
import unittest
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QToolButton
from shiboken6 import isValid
from tests.test_wireless_ui import APP, environment
from modules.common_widgets.config_fields import ConfigCheckBox, ConfigValueList
from modules.capture_services.openwrt.wireless_options import AP_ADVANCED
from modules.capture_services.openwrt.wireless_setup_models import WirelessNetwork
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog, RelaySetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_ap, configure_relay, ap_request, relay_request


class WirelessControlTests(unittest.TestCase):
    def test_checkbox_preserves_known_and_unknown_values_until_changed(self):
        box = ConfigCheckBox()
        self.addCleanup(box.deleteLater)
        self.assertEqual(box.checkState(), Qt.PartiallyChecked)
        self.assertIsNone(box.pending_value())
        box.click()
        self.assertEqual(box.pending_value(), "1")
        box.click()
        self.assertEqual(box.pending_value(), "0")
        for value in ("0", "1"):
            box.configure(value)
            self.assertEqual(box.isChecked(), value == "1")
            self.assertIsNone(box.pending_value())
            box.click()
            self.assertNotEqual(box.pending_value(), value)
            box.click()
            self.assertIsNone(box.pending_value())

    def test_ap_controls_submit_only_explicit_changes_and_reopen(self):
        env = environment()
        env.data["rows"][0].update(skip_inactivity_poll="0", disassoc_low_ack="1", wmm="1", ft_over_ds="0")
        row = SimpleNamespace(section="home", ssid="Home")
        dialog = AccessPointSetupDialog("radio7", "5G", row=row)
        self.addCleanup(dialog.reject)
        configure_ap(dialog, env)
        for spec in AP_ADVANCED:
            if spec.kind == "bool":
                self.assertIsInstance(dialog.expert_fields.controls[spec.key], QCheckBox)
        self.assertIsInstance(dialog.expert_fields.controls["ft_over_ds"], QComboBox)
        self.assertEqual(dialog.expert_fields.controls["ft_over_ds"].currentData(), "0")
        self.assertNotIn("ft_over_ds", ap_request(dialog).options)
        for field in (dialog.dtim, dialog.beacon, dialog.client_limit):
            self.assertIsInstance(field, QLineEdit)
        original = ap_request(dialog)
        self.assertNotIn("wmm", original.options)
        self.assertNotIn("skip_inactivity_poll", original.options)
        controls = dialog.expert_fields.controls
        controls["skip_inactivity_poll"].click()
        controls["disassoc_low_ack"].click()
        controls["max_inactivity"].setText("86400")
        request = ap_request(dialog)
        self.assertEqual(request.options["skip_inactivity_poll"], "1")
        self.assertEqual(request.options["disassoc_low_ack"], "0")
        self.assertEqual(request.options["max_inactivity"], "86400")
        env.data["rows"][0].update(request.options)
        reopened = AccessPointSetupDialog("radio7", "5G", row=row)
        self.addCleanup(reopened.reject)
        configure_ap(reopened, env)
        self.assertTrue(reopened.expert_fields.controls["skip_inactivity_poll"].isChecked())
        self.assertFalse(reopened.expert_fields.controls["disassoc_low_ack"].isChecked())
        self.assertNotIn("skip_inactivity_poll", ap_request(reopened).options)
        self.assertIn("86400", reopened.expert_fields.controls["max_inactivity"].placeholderText())
        dialog.dtim.setText("256")
        with self.assertRaises(ValueError):
            ap_request(dialog)

    def test_relay_list_metric_and_bssid_submit_existing_native_values(self):
        env = environment()
        env.data["networks"][2].update(dns=["1.1.1.1", "8.8.8.8"], metric="37")
        env.data["rows"].append(dict(section="upper", ssid="Upstream", device="radio7",
            mode="sta", network="wan", encryption="psk2", has_key=True,
            macaddr="02:11:22:33:44:55"))
        row = SimpleNamespace(section="upper", ssid="Upstream", running="active")
        dialog = RelaySetupDialog("Upstream", radio_name="radio7", row=row)
        self.addCleanup(dialog.reject)
        configure_relay(dialog, env)
        self.assertIsInstance(dialog.lock_bssid, QCheckBox)
        self.assertIsInstance(dialog.metric_edit, QLineEdit)
        self.assertEqual(dialog.dns.values(), ["1.1.1.1", "8.8.8.8"])
        target = WirelessNetwork("radio7", "02:11:22:33:44:55", "Upstream", 36, "5G", -40, "psk2")
        dialog.lock_bssid.setChecked(False)
        request = relay_request(dialog, target, "proof")
        self.assertEqual(request.options["bssid"], "")
        self.assertEqual(request.options["metric"], "37")
        self.assertEqual(request.options["dns"], "1.1.1.1 8.8.8.8")
        self.assertNotIn("identity_action", request.options)
        dialog.bridge_mode.setCurrentIndex(dialog.bridge_mode.findData("wds"))
        request = relay_request(dialog, target, "proof")
        self.assertNotIn("dns", request.options)
        self.assertNotIn("metric", request.options)

    def test_dynamic_list_removal_releases_widgets_and_limits_growth(self):
        editor = ConfigValueList()
        self.addCleanup(editor.deleteLater)
        editor.set_values(["1.1.1.1", "8.8.8.8"])
        removed = editor._rows[0][0]
        editor._rows[0][0].findChild(QToolButton).click()
        self.assertEqual(editor.values(), ["8.8.8.8"])
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(removed))
        editor.set_values(["1.1.1.1"] * editor.MAX_ENTRIES)
        self.assertFalse(editor.add_button.isEnabled())
        editor.add_value("8.8.8.8")
        self.assertEqual(len(editor._rows), editor.MAX_ENTRIES)
        with self.assertRaises(ValueError):
            editor.set_values(["1.1.1.1"] * (editor.MAX_ENTRIES + 1))

    def test_auto_wrap_measures_the_styled_label_font(self):
        from PySide6.QtWidgets import QWidget
        from modules.settings_pages.dialog_form_helpers import form_row
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        parent.setStyleSheet("QLabel { font-size: 24pt; }")
        text = "W W W W W W"
        self.assertLess(parent.fontMetrics().horizontalAdvance(text), 160)
        row = form_row(parent, text, QLineEdit(parent), wrap_label="auto")
        label = row.itemAt(0).widget()
        self.assertGreater(label.fontMetrics().horizontalAdvance(text), 160)
        self.assertTrue(label.wordWrap())
        standard = form_row(parent, text, QLineEdit(parent), wrap_label=False)
        self.assertFalse(standard.itemAt(0).widget().wordWrap())
