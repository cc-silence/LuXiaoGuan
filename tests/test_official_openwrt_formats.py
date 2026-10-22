"""Official rpcd wire-format contracts, not physical-device certification."""
import copy
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.wireless_setup_models import parse_scan, WirelessEnvironment
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.openwrt.signal_manager import SignalError
from tests import test_inactive_radio_scan as inactive


def row(channel=6, **extra):
    return dict(bssid="02:11:22:33:44:55", ssid="fixture-private-name", channel=channel,
                signal=4294967254, encryption={"enabled": False}, **extra)


class ScanFormatTests(unittest.TestCase):
    def test_all_six_official_pinned_format_fixtures(self):
        import json
        from pathlib import Path
        from modules.capture_services.openwrt.package_manager import PackageManager
        contracts = json.loads((Path(__file__).parent / "fixtures/official_openwrt_contracts.json").read_text(encoding="utf-8"))
        self.assertEqual({c["series"] for c in contracts}, {"19.07", "21.02", "22.03", "23.05", "24.10", "25.12"})
        for contract in contracts:
            with self.subTest(series=contract["series"]):
                self.assertTrue(contract["rollback"])
                self.assertRegex(contract["rpcd_commit"], r"^[a-f0-9]{40}$")
                for sample in contract["samples"]:
                    records = parse_scan([sample["scan"]], "radio9", channels=sample["channels"])
                    self.assertEqual(len(records), 1)
                    self.assertIn(records[0].band, ("2.4G", "5G", "6G"))
                manager = contract["package_manager"]
                self.assertEqual(PackageManager.from_probe({manager: 1}).name, manager)

    def test_legacy_channel_only_records_use_actual_phy_frequencies(self):
        for radio, channel, mhz, band in (("radio9", 6, 2437, "2.4G"),
                ("radio0", 36, 5180, "5G"), ("radio3", 5, 5975, "6G")):
            records = parse_scan([row(channel)], radio, channels=[dict(channel=channel, mhz=mhz)])
            self.assertEqual((records[0].radio, records[0].band, records[0].power), (radio, band, -42))

    def test_modern_frequency_and_band_agree_with_phy(self):
        for reported, channel, mhz, band in ((2, 6, 2437, "2.4G"), (5, 36, 5180, "5G"), (6, 5, 5975, "6G")):
            self.assertEqual(parse_scan([row(channel, band=reported, mhz=mhz)], "radio9",
                             channels=[dict(channel=channel, mhz=mhz)])[0].band, band)

    def test_ambiguous_missing_or_conflicting_band_is_never_guessed(self):
        cases = [([row(1)], []), ([row(1)], [dict(channel=1, mhz=2412), dict(channel=1, mhz=5955)]),
                 ([row(6, mhz=2437, band=5)], []), ([row(36, band=5)], [dict(channel=36, mhz=6130)]),
                 ([row(6, mhz="2437")], []), ([row(True, mhz=2437)], [])]
        for rows, channels in cases:
            with self.subTest(rows=rows, channels=channels), self.assertRaises(ValueError):
                parse_scan(rows, "radio0", channels=channels)

    def test_valid_rows_survive_malformed_neighbors_and_evidence_has_no_names(self):
        evidence = []
        records = parse_scan([row(6, mhz=2437), row(1), {"ssid": "fixture-private-name"}], "radio8", on_evidence=evidence.append)
        self.assertEqual(len(records), 1)
        self.assertEqual(evidence[0]["accepted_rows"], 1)
        self.assertEqual(evidence[0]["rejected_rows"], 2)
        self.assertNotIn("fixture-private-name", str(evidence))
        self.assertNotIn("02:11:22:33:44:55", str(evidence))
        self.assertTrue(all(type(value) is int for value in evidence[0].values()))

    def test_unbounded_or_nonfinite_frequency_does_not_abort_valid_neighbors(self):
        for bad in (10**1000, float("inf"), float("nan"), True):
            with self.subTest(kind=type(bad).__name__):
                result = parse_scan([row(6, mhz=bad), row(6, mhz=2437)], "radio7")
                self.assertEqual(len(result), 1)

    def test_invalid_unicode_does_not_leak_name_in_error(self):
        bad = row(6, mhz=2437)
        bad["ssid"] = "private-" + chr(0xd800)
        with self.assertRaises(ValueError) as error:
            parse_scan([bad], "radio9")
        self.assertNotIn("private", str(error.exception))

    def test_empty_results_and_invalid_structure_are_distinguished(self):
        self.assertEqual(parse_scan({}, "radio7"), ())
        self.assertEqual(parse_scan([], "radio7"), ())
        for bad in (None, "bad", [row()]*1025):
            with self.assertRaises(ValueError): parse_scan(bad, "radio7")

    def test_hidden_enterprise_and_unsigned_signal_remain_supported(self):
        hidden = row(36, mhz=5180); hidden["ssid"] = ""
        enterprise = row(36, mhz=5180)
        enterprise["encryption"] = {"enabled": True, "authentication": ["802.1x"], "wpa": [2]}
        result = parse_scan([hidden, enterprise], "radio7")
        self.assertTrue(all(record.reason for record in result))
        self.assertEqual(result[0].power, -42)

    def test_manager_passes_phy_frequency_list_and_records_parse_failures(self):
        for valid in (False, True):
            journal = OperationDiagnostics()
            manager = WirelessSetupManager(NS(operation_diagnostics=journal))
            channels = [dict(channel=6, mhz=2437)] if valid else []
            env = WirelessEnvironment(data=dict(radios=[dict(name="radio9", phy="phy2", interfaces=["wlan2"], channels=channels)]))
            manager.inspect = Mock(return_value=env)
            now = [0.]
            def scan(*args, **kwargs):
                now[0] += 6
                return {"results": [row()]}
            manager._call = Mock(side_effect=scan)
            event = NS(is_set=lambda: False, wait=lambda _: False)
            with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda: now[0]):
                if valid: self.assertEqual(manager.scan(5, event, Mock(), radio="radio9")[0].band, "2.4G")
                else:
                    with self.assertRaises(SignalError): manager.scan(5, event, Mock(), radio="radio9")
            data = journal.snapshot()
            self.assertEqual(data["events"][-1]["legacy_rows"], 1)
            self.assertEqual(data["events"][-1]["status"], "completed" if valid else "failed")
            self.assertNotIn("fixture-private-name", str(data))


@unittest.skipUnless(inactive.LuaRuntime, "Lua runtime unavailable")
class LegacyNativeScanTests(unittest.TestCase):
    def test_legacy_temporary_scan_preserves_disabled_radios_and_all_configs(self):
        device = inactive.InactiveRadioDevice()
        original_call = device.call
        def call(obj, method, args):
            value, error = original_call(obj, method, args)
            if obj == "iwinfo" and method == "scan":
                value = device.wrap({"results": [row(36)]})
            return value, error
        device.lua.globals().bridge_call = call
        manager = WirelessSetupManager(device)
        before = copy.deepcopy(device.config)
        now = [0.]
        event = NS(is_set=lambda: False, wait=lambda _: now.__setitem__(0, now[0]+6) or False)
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda: now[0]):
            result = manager.scan(5, event, Mock(), radio="radio7")
        self.assertEqual(result[0].band, "5G")
        self.assertEqual(device.config, before)
        self.assertFalse(device.sessions)
        self.assertFalse(device.temporary_exists)
        self.assertNotIn(("uci", "apply"), device.calls)
