"""Only recovery identities, never wireless secrets, are stored locally."""
import json
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.recovery_identity_snapshot import recovery_identity_snapshot
from modules.capture_services.openwrt.radio_takeover import OpenWrtRadioTakeover, _parse_iwdev_interfaces
from modules.capture_services.openwrt.radio_identity import snapshot_radio_mapping
from tests.test_radio_identity import snapshot


class RecoveryIdentitySnapshotTests(unittest.TestCase):
    def mapping(self, text):
        rows = _parse_iwdev_interfaces(text)
        return snapshot_radio_mapping(text, {phy: tuple(name for name, _ in items)
                                              for phy, items in rows.items()})

    def test_secret_values_and_status_payload_are_not_saved(self):
        status = {"radio7":{"interfaces":[{"section":"home","ifname":"wlan2",
                                             "config":{"key":"JSON-SECRET", "ssid":"PRIVATE-SSID"}}]}}
        original = snapshot(status, {"radio7":"phy2"})
        original = original.replace("__WINBOX_SNAP_IWDEV__", "wireless.home.key='FIRST'\\''SECOND'\n"
                                    "wireless.home.password='THIRD\nFOURTH'\n__WINBOX_SNAP_IWDEV__")
        safe = recovery_identity_snapshot(original)
        for secret in ("FIRST", "SECOND", "THIRD", "FOURTH", "JSON-SECRET", "PRIVATE-SSID"):
            self.assertNotIn(secret, safe)
        radio = OpenWrtRadioTakeover(Mock())
        self.assertEqual(self.mapping(safe), self.mapping(original))
        self.assertEqual(radio.resolve_phy("radio7", safe), radio.resolve_phy("radio7", original))

    def test_ambiguous_mapping_stays_ambiguous(self):
        cases = [snapshot({"radio7":{"interfaces":[{"ifname":"wlan2"}]}}, {"radio7":"phy9"}, phys=["phy2","phy9"]),
                 snapshot(),
                 snapshot(identities={"radio7":"phy2"}).replace("phy#2\n Interface wlan2\n type managed\n", "")]
        for original in cases:
            with self.subTest(original=original):
                safe = recovery_identity_snapshot(original)
                self.assertEqual(self.mapping(safe), self.mapping(original))
                if "radio7" in self.mapping(original):
                    radio = OpenWrtRadioTakeover(Mock())
                    self.assertEqual(radio.resolve_phy("radio7", safe), radio.resolve_phy("radio7", original))

    def test_official_identity_response_fixtures_keep_same_mapping(self):
        from pathlib import Path
        from modules.capture_services.openwrt.radio_identity_probe import enrich_radio_identity
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        fixtures = json.loads((Path(__file__).parent / "fixtures/official_radio_identity_contracts.json").read_text(encoding="utf-8"))
        for row in fixtures:
            with self.subTest(series=row["series"]):
                remote = Mock()
                remote.run.return_value = RemoteCommandResult("probe", 0,
                    "__RM_PHYS__\nphy7\n__RM_PHYS_OK__\n__RM_RADIO__wifi_a\n"
                    + json.dumps(row["reply"]) + "\n__RM_END__\n")
                original = enrich_radio_identity(remote, snapshot(bands={"wifi_a":"5g"}))
                self.assertEqual(self.mapping(recovery_identity_snapshot(original)), self.mapping(original))

    def test_valid_non_ascii_interface_name_is_preserved(self):
        name = "\u65e0\u7ebf0"
        original = snapshot(identities={"radio7":"phy2"}).replace("wlan2", name)
        safe = recovery_identity_snapshot(original)
        self.assertEqual(OpenWrtRadioTakeover(Mock()).resolve_phy("radio7", safe),
                         OpenWrtRadioTakeover(Mock()).resolve_phy("radio7", original))

    def test_invalid_interface_is_rejected_before_saving(self):
        original = snapshot(identities={"radio7":"phy2"}).replace("Interface wlan2", "Interface bad name")
        with self.assertRaises(ValueError): recovery_identity_snapshot(original)


if __name__ == "__main__": unittest.main()
