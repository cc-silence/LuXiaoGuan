"""Actual Qt geometry and typed form/executor contracts for wireless setup."""
from types import SimpleNamespace
import unittest
from tests.test_standalone import APP
from PySide6.QtWidgets import QWidget,QFrame
from modules.ui_scale_manager import UiScaleManager
from modules.capture_services.openwrt.wireless_capabilities import RADIO_SETTINGS, IFACE_SETTINGS
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment,WirelessNetwork
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog,RelaySetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_ap,configure_relay,ap_request,relay_request


def environment():
    return WirelessEnvironment(data=dict(revision="a"*192,features=dict.fromkeys(("ap_sae","ap_owe","ap_acs","ap_11r","ap_eap","relayd"),True),
        radios=[dict(name="radio7",type="mac80211",band="5g",channel="36",htmodes=["HE20","HE80"],channels=[dict(channel=36)],powers=[],countries=[],option_support={"device":dict.fromkeys(RADIO_SETTINGS,True),"interface":dict.fromkeys(IFACE_SETTINGS,True)})],
        rows=[dict(section="home",ssid="Home",device="radio7",mode="ap",network=["lan","guest"],encryption="psk2+ccmp")],
        networks=[dict(name="lan",proto="static"),dict(name="guest",proto="static"),dict(name="wan",proto="dhcp")],
        zones=[dict(name="lan",network=["lan"]),dict(name="wan",network=["wan"],masq="1")],active=[]))

class WirelessUiTests(unittest.TestCase):
    def test_default_forms_and_current_page_geometry_at_both_scales(self):
        for scale in (100,150):
            for light in (False,True):
                with self.subTest(scale=scale,light=light):
                    parent=QWidget();parent.scale_manager=UiScaleManager(scale);parent.setProperty("settingsLightTheme",light)
                    ap=AccessPointSetupDialog("radio7","5G",parent);configure_ap(ap,environment())
                    ap.show();APP.processEvents()
                    self.assertTrue(ap.common_section.content_frame.isHidden())
                    self.assertTrue(ap.expert_section.content_frame.isHidden())
                    relay=RelaySetupDialog("Upstream",parent,radio_name="radio7")
                    configure_relay(relay,environment());relay.show();APP.processEvents()
                    self.assertTrue(relay.password.isVisible())
                    self.assertTrue(relay.other_section.content_frame.isHidden())
                    relay.other_section.set_expanded(True,animate=False);APP.processEvents()
                    tabs=relay.other_tabs
                    nav=tabs.findChild(QFrame,"settingsSubpageNavigation")
                    self.assertLess(tabs.stack.y()-nav.geometry().bottom(),parent.scale_manager.px(24))
                    short=tabs.sizeHint().height()
                    tabs.buttons[1].click();APP.processEvents()
                    self.assertGreater(tabs.sizeHint().height(),short)
                    tabs.buttons[0].click();APP.processEvents()
                    self.assertEqual(tabs.sizeHint().height(),short)
                    relay.reject();ap.reject();parent.deleteLater();APP.processEvents()

    def test_edit_preserves_cipher_multi_network_and_optional_fields(self):
        row=SimpleNamespace(section="home",ssid="Home")
        dialog=AccessPointSetupDialog("radio7","5G",row=row)
        configure_ap(dialog,environment())
        request=ap_request(dialog)
        self.assertEqual(request.options["encryption"],"psk2+ccmp")
        self.assertNotIn("network",request.options)
        self.assertNotIn("distance",request.options)
        dialog.expert_fields.controls["macfilter"].setCurrentIndex(2)
        dialog.expert_fields.controls["maclist"].set_values(["02:11:22:33:44:55"])
        request=ap_request(dialog)
        self.assertEqual(request.options["macfilter"],"allow")
        dialog.reject()

    def test_edit_keeps_saved_identity_and_enables_only_applicable_controls(self):
        env = environment()
        env.data["rows"].append(dict(section="upper", ssid="Upstream", device="radio7", mode="sta",
            network="wan", encryption="psk2", has_key=True, macaddr="02:11:22:33:44:55"))
        row = SimpleNamespace(section="upper", ssid="Upstream", running="active")
        dialog = RelaySetupDialog("Upstream", radio_name="radio7", row=row)
        configure_relay(dialog, env)
        identity = dialog.identity_fields
        self.assertEqual(identity.mode.currentData(), "keep")
        self.assertEqual(identity.mac.text(), "02:11:22:33:44:55")
        self.assertEqual(identity.values(), {})
        self.assertFalse(identity.profile.isEnabled())
        identity.mode.setCurrentIndex(identity.mode.findData("profile"))
        self.assertTrue(identity.profile.isEnabled())
        self.assertEqual(identity.values()["identity_action"], "set")
        self.assertFalse(dialog.bridge_network.isEnabled())
        for mode in ("wds", "relayd"):
            dialog.bridge_mode.setCurrentIndex(dialog.bridge_mode.findData(mode))
            self.assertTrue(dialog.bridge_network.isEnabled())
        dialog.bridge_mode.setCurrentIndex(dialog.bridge_mode.findData("routed"))
        self.assertFalse(dialog.bridge_network.isEnabled())
        self.assertEqual(dialog.password.edit.text(), "")
        dialog.reject()

    def test_bridge_request_uses_selected_lan_and_zone(self):
        dialog=RelaySetupDialog("Upstream",radio_name="radio7",password="test-password")
        configure_relay(dialog,environment())
        target=WirelessNetwork("radio7","02:11:22:33:44:55","Upstream",36,"5G",-40,"psk2")
        dialog.bridge_mode.setCurrentIndex(dialog.bridge_mode.findData("relayd"))
        request=relay_request(dialog,target,"proof")
        self.assertEqual((request.options["network"],request.options["zone"]),("lan","lan"))
        dialog.bridge_mode.setCurrentIndex(dialog.bridge_mode.findData("wds"))
        request=relay_request(dialog,target,"proof")
        self.assertNotIn("proto",request.options)
        self.assertNotIn("zone",request.options)
        dialog.reject()
