"""Band-specific radio capabilities, linked selectors and AP isolation defaults."""
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from tests import test_standalone as standalone
from tests.test_wireless_ui import environment
from tests.test_wireless_setup import LuaDevice, LuaRuntime, CAPABILITIES
from modules.capture_services.openwrt.wireless_radio_modes import supported_htmodes, radio_band, default_htmode
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_ap, ap_request
from modules.ui_scale_manager import UiScaleManager
from PySide6.QtWidgets import QWidget

FEATURES = dict.fromkeys(("ap_11ac", "ap_11ax", "ap_11be"), True)
MODES = ["NOHT", "HT20", "HT40", "VHT20", "VHT40", "VHT80", "VHT160", "HE20", "HE40", "HE80", "HE160", "EHT20", "EHT40", "EHT80", "EHT160", "EHT320"]


class RadioModePolicyTests(unittest.TestCase):
    def test_24ghz_never_offers_ac_or_wider_than_40mhz(self):
        modes=supported_htmodes(dict(band="2g",htmodes=MODES),FEATURES)
        self.assertEqual(modes,("HT20","HT40","HE20","HE40","EHT20","EHT40","NOHT"))

    def test_5ghz_excludes_320mhz_and_6ghz_excludes_old_standards(self):
        self.assertNotIn("EHT320",supported_htmodes(dict(band="5g",htmodes=MODES),FEATURES))
        modes=supported_htmodes(dict(band="6g",htmodes=MODES),FEATURES)
        self.assertTrue(all(m.startswith(("HE","EHT")) for m in modes))
        self.assertIn("EHT320",modes)

    def test_unknown_conflicting_and_legacy_bands_are_not_guessed_from_radio_number(self):
        self.assertEqual(supported_htmodes(dict(name="radio0",htmodes=MODES),FEATURES),())
        self.assertIsNone(radio_band(dict(band="2g",channels=[dict(mhz=5180)])))
        self.assertEqual(radio_band(dict(hwmode="11g")),"2g")
        self.assertEqual(radio_band(dict(channels=[dict(mhz=5975)])),"6g")

    def test_missing_firmware_features_hide_unavailable_standards(self):
        modes=supported_htmodes(dict(band="5g",htmodes=MODES),{})
        self.assertEqual(modes,("HT20","HT40","NOHT"))


class RadioDefaultTests(unittest.TestCase):
    def test_default_modes_use_safe_widths_and_supported_families(self):
        self.assertEqual(default_htmode(dict(band="2g",htmodes=MODES),FEATURES),"HE20")
        self.assertEqual(default_htmode(dict(band="5g",htmodes=MODES),FEATURES),"HE80")
        self.assertEqual(default_htmode(dict(band="6g",htmodes=MODES),FEATURES),"HE80")
        self.assertEqual(default_htmode(dict(band="5g",htmodes=["VHT20","VHT40","VHT80"]),FEATURES),"VHT80")
        self.assertEqual(default_htmode(dict(band="2g",htmodes=["HT20","HT40"]),FEATURES),"HT20")
        self.assertEqual(default_htmode(dict(band="2g",htmodes=["HE40","HT20"]),FEATURES),"HT20")
        self.assertEqual(default_htmode(dict(band="5g",htmodes=["HE20","VHT80"]),FEATURES),"VHT80")
        self.assertEqual(default_htmode(dict(band="5g",htmodes=["HT20","HT40"]),FEATURES),"HT40")
        self.assertEqual(default_htmode(dict(band="6g",htmodes=["EHT20","EHT80","EHT320"]),FEATURES),"EHT80")

    def test_default_never_fabricates_missing_capabilities_or_selects_extreme_width(self):
        self.assertIsNone(default_htmode(dict(band="5g",htmodes=[]),FEATURES))
        self.assertIsNone(default_htmode(dict(htmodes=MODES),FEATURES))
        self.assertIsNone(default_htmode(dict(band="5g",htmodes=["HE160"]),FEATURES))


class RadioEditorTests(unittest.TestCase):
    def dialog(self,band="2g",current="HE20",row=None,old_isolation=None,light=False,scale=100):
        parent=QWidget();parent.setProperty("settingsLightTheme",light)
        parent.setStyleSheet("QWidget {background: #f5f6fa;}" if light else "QWidget {background: #181b1f;}")
        parent.scale_manager=UiScaleManager(scale)
        dialog=AccessPointSetupDialog("radio7",band,parent,row=row)
        env=environment()
        env.data["features"].update(FEATURES)
        env.data["radios"][0].update(band=band,htmode=current,htmodes=MODES,channel="1" if band=="2g" else "36")
        if old_isolation is not None:env.data["rows"][0]["isolate"]=old_isolation
        configure_ap(dialog,env)
        dialog.name_edit.setText("TestAP")
        if row is None:dialog.password.edit.setText("test-password")
        self.addCleanup(parent.deleteLater)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_standard_and_bandwidth_default_to_preserving_current_setting(self):
        dialog=self.dialog()
        self.assertIsNone(dialog.standard_combo.currentData())
        self.assertIsNone(dialog.bandwidth_combo.currentData())
        self.assertNotIn("htmode",ap_request(dialog).options)
        values=[dialog.bandwidth_combo.itemData(i) for i in range(dialog.bandwidth_combo.count())]
        self.assertEqual(values,[None,"HE40"])
        self.assertEqual([dialog.bandwidth_combo.itemText(i) for i in range(dialog.bandwidth_combo.count())],["20 MHz","40 MHz"])
        self.assertEqual(dialog.standard_combo.findData("VHT"),-1)

    def test_change_standard_retains_width_and_updates_encoded_mode(self):
        dialog=self.dialog(current="HE40")
        dialog.standard_combo.setCurrentIndex(dialog.standard_combo.findData("HT"))
        self.assertEqual(dialog.bandwidth_combo.currentData(),"HT40")
        self.assertEqual(ap_request(dialog).options["htmode"],"HT40")
        dialog.bandwidth_combo.setCurrentIndex(dialog.bandwidth_combo.findData("HT20"))
        self.assertEqual(ap_request(dialog).options["htmode"],"HT20")
        dialog.standard_combo.setCurrentIndex(0)
        self.assertNotIn("htmode",ap_request(dialog).options)

    def test_missing_radio_setting_selects_explicit_defaults_in_the_editor(self):
        for band, mode in (("2g","HE20"),("5g","HE80"),("6g","HE80")):
            for current in (None,"", "   "):
                dialog=self.dialog(band=band,current=current)
                self.assertEqual(dialog.standard_combo.currentData(),"HE")
                self.assertEqual(dialog.bandwidth_combo.currentData(),mode)
                if band == "6g":
                    dialog.encryption.setCurrentIndex(dialog.encryption.findData("sae"))
                self.assertEqual(ap_request(dialog).options["htmode"],mode)

    def test_unknown_current_mode_requires_explicit_standard_before_bandwidth(self):
        dialog=self.dialog(current="vendor-mode")
        self.assertFalse(dialog.bandwidth_combo.isEnabled())
        self.assertNotIn("htmode",ap_request(dialog).options)
        dialog.standard_combo.setCurrentIndex(dialog.standard_combo.findData("HE"))
        self.assertTrue(dialog.bandwidth_combo.isEnabled())
        self.assertEqual(ap_request(dialog).options["htmode"],"HE20")

    def test_new_ap_isolated_but_existing_ap_keeps_original_setting(self):
        self.assertEqual(ap_request(self.dialog()).options["isolate"],"1")
        row=SimpleNamespace(section="home",ssid="Home")
        for value in (None,"0","1"):
            dialog=self.dialog(row=row,old_isolation=value)
            self.assertEqual(ap_request(dialog).options["isolate"],value or "0")
        dialog=self.dialog()
        dialog.common_section.set_expanded(True,animate=False)
        dialog.common_tabs.buttons[1].click()
        dialog.show();standalone.APP.processEvents()
        self.assertTrue(dialog.isolation.isVisible())
        self.assertIs(dialog.isolation.parentWidget(),dialog.common_tabs.stack.currentWidget())

    def test_actual_forms_render_with_linked_fields_in_both_themes(self):
        output=Path("tests/signal-actions-preview");output.mkdir(exist_ok=True)
        for light in (False,True):
            for scale in (100,175):
                dialog=self.dialog(light=light,scale=scale)
                dialog.common_section.set_expanded(True,animate=False)
                dialog.show();standalone.APP.processEvents()
                color=dialog.title_bar.grab().toImage().pixelColor(300,20)
                self.assertGreater(color.lightness(),200) if light else self.assertLess(color.lightness(),70)
                self.assertTrue(dialog.standard_combo.isVisible())
                self.assertTrue(dialog.bandwidth_combo.isVisible())
                self.assertTrue(dialog.grab().save(str(output/f"radio-modes-{light}-{scale}.png")))
                dialog.common_tabs.buttons[1].click();standalone.APP.processEvents()
                self.assertTrue(dialog.isolation.isVisible())
                self.assertTrue(dialog.grab().save(str(output/f"ap-isolation-{light}-{scale}.png")))
                dialog.reject()


@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class RadioSaveTests(unittest.TestCase):
    def test_backend_refuses_80mhz_on_24ghz_even_if_raw_driver_list_contains_it(self):
        manager=WirelessSetupManager(LuaDevice())
        env=environment();env.data["features"].update(FEATURES)
        env.data["radios"][0].update(band="2g",phy="phy2",channel="1",channels=[dict(channel=1,mhz=2412)],htmodes=MODES,capabilities=CAPABILITIES)
        manager.inspect=Mock(return_value=env);manager._transaction=Mock()
        with self.assertRaises(ValueError):
            manager.save(WirelessRequest("ap","radio7","TestAP","test-password",options={"htmode":"HE80"}),threading.Event(),Mock())
        manager._transaction.assert_not_called()

    def test_isolation_reaches_saved_router_config(self):
        device=LuaDevice();manager=WirelessSetupManager(device)
        result=manager.save(WirelessRequest("ap","radio7","TestAP","test-password",options={"network":"lan","isolate":"1"}),threading.Event(),Mock())
        self.assertEqual(device.config["wireless"][result["section"]]["isolate"],"1")

    def test_missing_radio_mode_is_filled_at_save_boundary_and_reaches_native_config(self):
        device=LuaDevice()
        device.config["wireless"]["radio7"].pop("htmode")
        manager=WirelessSetupManager(device)
        result=manager.save(WirelessRequest("ap","radio7","DefaultAP","test-password",options={"network":"lan"}),threading.Event(),Mock())
        self.assertEqual(device.config["wireless"]["radio7"]["htmode"],"HE80")
        self.assertEqual(device.config["wireless"][result["section"]]["ssid"],"DefaultAP")
