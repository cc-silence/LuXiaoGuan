"""LAN form display and real persistence retain old and CIDR contracts."""
import copy
import unittest
from unittest.mock import patch
from pathlib import Path
from tests.test_standalone import APP
from tests.test_common_settings_ui import NETWORK
from modules.main_pages.packet_capture.openwrt.router_config_dialog import RouterConfigDialog
from modules.capture_services.openwrt.common_settings import validate_values, read_settings, save_settings


class LanIpv4FormTests(unittest.TestCase):
    def dialog(self, address, mask=None):
        review = copy.deepcopy(NETWORK)
        review["values"]["ipaddr"] = address
        review["values"].pop("netmask", None)
        if mask is not None:
            review["values"]["netmask"] = mask
        dialog = RouterConfigDialog(review)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_old_scalar_and_list_are_unchanged(self):
        for raw in ("192.168.1.1", ["192.168.1.1"]):
            d = self.dialog(raw, "255.255.255.0")
            self.assertEqual(d.controls["ipaddr"].values(), ["192.168.1.1"])
            self.assertEqual(d.values()["ipaddr"], raw)
            self.assertEqual(d.values()["netmask"], "255.255.255.0")

    def test_cidr_scalar_and_list_split_and_round_trip_exactly(self):
        for raw in ("192.168.1.1/24", ["192.168.1.1/24"], ["192.168.1.1/24", "192.168.2.1/24"]):
            for mask in (None, "", "255.255.255.0"):
                with self.subTest(raw=raw, mask=mask):
                    d = self.dialog(raw, mask)
                    before = copy.deepcopy(d.review)
                    self.assertTrue(all("/" not in v for v in d.controls["ipaddr"].values()))
                    self.assertEqual(d.controls["netmask"].currentText(), "255.255.255.0")
                    self.assertEqual(d.values()["ipaddr"], raw)
                    self.assertEqual(d.values().get("netmask"), mask)
                    self.assertEqual(d.review, before)
                    validate_values(d.review, d.values())

    def test_edit_address_and_mask_keeps_cidr_storage_and_reopens(self):
        for raw in ("192.168.1.1/24", ["192.168.1.1/24"]):
            d = self.dialog(raw)
            d.controls["ipaddr"].set_values(["192.168.8.1"])
            d.controls["netmask"].setEditText("255.255.255.128")
            values = d.values()
            self.assertEqual(values["ipaddr"], ["192.168.8.1/25"] if isinstance(raw, list) else "192.168.8.1/25")
            self.assertNotIn("netmask", values)
            validate_values(d.review, values)
            reopened = self.dialog(values["ipaddr"])
            self.assertEqual(reopened.controls["ipaddr"].values(), ["192.168.8.1"])
            self.assertEqual(reopened.controls["netmask"].currentText(), "255.255.255.128")

    def test_mask_only_change_updates_prefix_and_add_remove_retains_list(self):
        d = self.dialog(["192.168.1.1/24"])
        d.controls["netmask"].setEditText("255.255.0.0")
        self.assertEqual(d.values()["ipaddr"], ["192.168.1.1/16"])
        d.controls["ipaddr"].set_values(["192.168.1.1", "192.168.2.1"])
        self.assertEqual(d.values()["ipaddr"], ["192.168.1.1/16", "192.168.2.1/16"])
        d.controls["ipaddr"].set_values([])
        with self.assertRaises(ValueError):
            validate_values(d.review, d.values())

    def test_invalid_inputs_are_not_silently_repaired(self):
        for ip, mask in (("invalid", "255.255.255.0"), ("192.168.1.1", ""), ("192.168.1.1", "255.0.255.0")):
            d = self.dialog(["192.168.1.1/24"])
            d.controls["ipaddr"].set_values([ip])
            d.controls["netmask"].setEditText(mask)
            with self.assertRaises(ValueError):
                validate_values(d.review, d.values())

    def test_different_prefixes_and_invalid_original_are_not_lost(self):
        for raw in (["192.168.1.1/24", "10.0.0.1/8"], ["invalid/24"], ["192.168.1.1", "10.0.0.1/8"]):
            d = self.dialog(raw)
            self.assertEqual(d.controls["ipaddr"].values(), raw)
            self.assertEqual(d.values()["ipaddr"], raw)

    def test_real_lua_save_retains_format_and_updated_prefix(self):
        from tests.test_lan_cidr_policy import LanCidrPolicyTests
        for raw, mask in ((["192.168.8.1/24"], None), ("192.168.8.1", "255.255.255.0")):
            for legacy in (False, True):
                device = LanCidrPolicyTests().device(raw, mask, legacy)
                review = read_settings(device, "network", device.cancel)
                d = RouterConfigDialog(review)
                self.addCleanup(d.deleteLater)
                d.controls["ipaddr"].set_values(["192.168.9.1"])
                d.controls["netmask"].setEditText("255.255.255.128")
                values = d.values()
                before = copy.deepcopy(device.config)
                result = save_settings(device, review, values, device.cancel)
                self.assertTrue(result["saved"])
                for key, value in values.items():
                    if value in ("", []):
                        before["network"]["lan"].pop(key, None)
                    else:
                        before["network"]["lan"][key] = value
                self.assertEqual(device.config, before)
                expected = ["192.168.9.1/25"] if isinstance(raw, list) else "192.168.9.1"
                self.assertEqual(device.config["network"]["lan"]["ipaddr"], expected)
                self.assertEqual(device.config["network"]["lan"].get("netmask"), None if mask is None else "255.255.255.128")

    def test_render_separate_fields_in_both_themes(self):
        from tests.common_settings_fixture import create_page
        output = Path("build/review/lan-ipv4-form")
        output.mkdir(parents=True, exist_ok=True)
        for light in (False, True):
            page, _ = create_page(self, light=light)
            review = copy.deepcopy(NETWORK)
            review["values"]["ipaddr"] = ["192.168.1.1/24"]
            review["values"].pop("netmask")
            from modules.dialog_theme import build_base_stylesheet
            with patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet",
                       side_effect=lambda config_manager, scale_manager: build_base_stylesheet(
                           config_manager, light_theme=light,
                           theme_name="light.qss" if light else "dark.qss", scale_manager=scale_manager)):
                d = RouterConfigDialog(review, page)
            self.addCleanup(d.deleteLater)
            d.show()
            APP.processEvents()
            self.assertEqual(d.controls["ipaddr"].values(), ["192.168.1.1"])
            self.assertEqual(d.controls["netmask"].currentText(), "255.255.255.0")
            self.assertTrue(d.grab().save(str(output / ("light.png" if light else "dark.png"))))
            d.close()
