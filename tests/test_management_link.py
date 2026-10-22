"""Offline management-path policy and router report regressions."""
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.management_link import (
    parse_management_link, require_management_link, read_management_link, MANAGEMENT_LINK_COMMAND)
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics

MAC = "02:11:22:33:44:55"
LINKS = [
    "br-lan||1|bridge|| eth0.1 phy0-ap0 phy1-ap0",
    "eth0.1||1|link|0x1| eth0",
    "eth0||1|link||",
    "phy0-ap0|phy0|1|link|0x2|",
    "phy1-ap0|phy1|1|link|0x3|",
    "br-other||1|bridge|| other-port",
    "other-port||1|link|0x1|",
]


def report(dev="br-lan", port="eth0.1", route="", neigh="", fdb=None, brctl=""):
    return ("__MGLINK_ROUTE__\n" + (route or "192.168.1.2 from 192.168.1.1 dev "+dev) +
            "\n__MGLINK_NEIGH__\n" + (neigh or "192.168.1.2 dev br-lan lladdr "+MAC+" REACHABLE") +
            "\n__MGLINK_FDB__\n" + (MAC+" dev "+port+" master br-lan" if fdb is None else fdb) +
            "\n__MGLINK_BRCTL__\n"+brctl+
            "\n__MGLINK_INTERFACES__\n"+"\n".join(LINKS)+"\n__MGLINK_END__\n")


def runner(text, code=0, **kwargs):
    value = Mock()
    value.run.return_value = RemoteCommandResult("management_link_probe", code, text, **kwargs)
    original=value.run.return_value
    value.run.side_effect=lambda name,*args,**values: (
        RemoteCommandResult(name,0,"__RADIO_BUSINESS_BEGIN__\n__RADIO_BUSINESS_END__\n")
        if name=="radio_business_probe" else original)

    value.operation_diagnostics = OperationDiagnostics()
    return value


class ManagementLinkTests(unittest.TestCase):
    def test_ethernet_over_bridge_and_vlan_is_safe_regardless_of_pc_wifi(self):
        link = parse_management_link(report())
        self.assertEqual((link.status, link.interface, link.phy), ("wired","eth0",""))
        require_management_link(runner(report()),"phy0")
        require_management_link(runner(report()),"phy1")

    def test_actual_wireless_phy_blocks_even_if_task_band_or_name_matches_another_radio(self):
        text = report(port="phy0-ap0")
        self.assertEqual(parse_management_link(text).phy,"phy0")
        with self.assertRaises(RuntimeError):
            require_management_link(runner(text),"phy0")
        require_management_link(runner(text),"phy1")

    def test_direct_wireless_route_and_ipv6(self):
        text = report(route="2001:db8::2 from 2001:db8::1 dev phy1-ap0 src 2001:db8::1")
        self.assertEqual(parse_management_link(text).phy,"phy1")
        with self.assertRaises(RuntimeError):
            require_management_link(runner(text),"phy1")

    def test_gateway_mac_is_used_for_routed_management_connections(self):
        text = report(route="10.0.0.2 via 192.168.1.2 dev br-lan src 192.168.1.1",port="phy1-ap0")
        self.assertEqual(parse_management_link(text).phy,"phy1")

    def test_old_firmware_brctl_fallback_stays_within_its_bridge(self):
        link = parse_management_link(report(fdb="",brctl="1 "+MAC+" no 0.00"))
        self.assertEqual((link.status,link.interface),("wired","eth0"))

    def test_missing_ambiguous_failed_and_unknown_data_do_not_claim_same_band(self):
        cases = [
            "", report().replace("__MGLINK_END__",""), report(dev="unknown"),
            report(fdb=""), report(neigh="192.168.1.2 dev br-lan FAILED"),
            report(fdb=MAC+" dev phy0-ap0 master br-lan\n"+MAC+" dev eth0.1 master br-lan"),
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_management_link(text).status,"unknown")
                with self.assertRaises(RuntimeError):
                    require_management_link(runner(text),"phy0")

    def test_probe_timeout_cancellation_and_nonzero_never_allow_a_mutation(self):
        for kwargs in ({"timed_out":True},{"cancelled":True}):
            with self.assertRaises((InterruptedError,TimeoutError)):
                read_management_link(runner(report(), **kwargs))
        self.assertEqual(read_management_link(runner("",code=83)).status,"unknown")

    def test_scan_risk_requires_explicit_override_and_still_rejects_unknown(self):
        require_management_link(runner(report(port="phy0-ap0")),"phy0",allow_scan_risk=True)
        with self.assertRaises(RuntimeError):
            require_management_link(runner(""),"phy0",allow_scan_risk=True)

    def test_diagnostics_do_not_export_ip_mac_or_ssid(self):
        backend = runner(report())
        require_management_link(backend,"phy0")
        text = str(backend.operation_diagnostics.snapshot())
        self.assertIn("router_route_fdb",text)
        self.assertNotIn(MAC,text)
        self.assertNotIn("192.168.1.",text)
        self.assertNotIn("windows_netsh",text)

    def test_report_is_bounded_and_incomplete_topology_is_rejected(self):
        self.assertEqual(parse_management_link("x"*270000).status,"unknown")
        text = report(dev="eth0").replace("eth0||1|link||","eth0||65534|link||")
        self.assertEqual(parse_management_link(text).status,"unknown")


if __name__ == "__main__":
    unittest.main()
