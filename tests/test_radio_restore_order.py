"""Restore diagnostics must use UCI section identities, not declaration order."""
import itertools
import unittest
from unittest.mock import Mock
from tests.test_radio_identity import snapshot
from modules.capture_services.openwrt.radio_takeover import OpenWrtRadioTakeover,RadioTakeoverRecord,_parse_uci_wireless_sections

HOME="wireless.home=wifi-iface\nwireless.home.device='radio7'\nwireless.home.mode='ap'\nwireless.home.disabled='1'\nwireless.home.macaddr='02:00:00:00:00:01'\n"
RADIO="wireless.radio7=wifi-device\nwireless.radio7.disabled='0'\nwireless.radio7.macaddr='02:00:00:00:00:02'\n"
OTHER="wireless.other=wifi-iface\nwireless.other.device='radio8'\nwireless.other.mode='sta'\nwireless.other.disabled='0'\n"

class RadioRestoreOrderTests(unittest.TestCase):
    def test_native_section_order_never_changes_signal_fields(self):
        expected={"device":"radio7","mode":"ap","disabled":True,"macaddr":"02:00:00:00:00:01"}
        for blocks in itertools.permutations((HOME,RADIO,OTHER)):
            with self.subTest(order=[b.splitlines()[0] for b in blocks]):
                rows=_parse_uci_wireless_sections("".join(blocks))
                self.assertEqual(rows["home"],expected)
                self.assertEqual(rows["other"]["device"],"radio8")
                self.assertNotIn("radio7",rows)

    def test_restore_audit_still_reports_disabled_signal_when_device_follows_it(self):
        text=HOME+snapshot(status={"radio7":{"interfaces":[{"section":"home","ifname":"wlan2"}]}},identities={"radio7":"phy2"})
        text=text.replace("wireless.radio7.band='2g'", "wireless.radio7.band='2g'\nwireless.radio7.disabled='0'")
        takeover=OpenWrtRadioTakeover(Mock());takeover.snapshot_wireless=Mock(return_value=text)
        note=takeover.audit_restore(RadioTakeoverRecord("audit","radio7","phy2",("wlan2",)))
        self.assertIn("home",note)
