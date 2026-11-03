"""Ordinary relay identity is editable; blocked layouts explain the exact cause."""
import copy
import unittest
from unittest.mock import patch
from tests.test_router_policy import PolicyDevice
from modules.capture_services.openwrt.router_policy_plan import build_plan


class StationBatchTests(unittest.TestCase):
    def test_plain_relay_can_change_identity_without_changing_bridge_or_password(self):
        for legacy in (True,False):
            with self.subTest(legacy=legacy):
                d=PolicyDevice(legacy)
                d.config["wireless"]["station"]["rm_bridge"]="oldrelay"
                d.config["network"]["oldrelay"]={".type":"interface","proto":"relay","network":["lan","wwan"],"rm_owner":"station"}
                before=copy.deepcopy(d.config)
                snapshot=d.policy.read(d.cancel)
                plan=build_plan(snapshot,"station")
                self.assertFalse(plan["skipped"])
                d.policy._transaction(snapshot,plan["operations"],d.cancel)
                self.assertEqual(d.config["network"]["oldrelay"],before["network"]["oldrelay"])
                self.assertEqual(d.config["wireless"]["station"]["key"],before["wireless"]["station"]["key"])
                self.assertNotEqual(d.config["network"]["wwan"]["hostname"],before["network"]["wwan"]["hostname"])
                self.assertNotIn("rm_identity_backup",d.config["wireless"]["station"])

    def test_shared_network_with_non_station_peer_lists_actual_configuration(self):
        d=PolicyDevice()
        d.config["wireless"]["second"]={".type":"wifi-iface","mode":"ap","network":"wwan","ssid":"Another saved uplink"}
        with self.assertRaises(ValueError) as error:build_plan(d.policy.read(d.cancel),"station")
        self.assertIn("wwan",str(error.exception))
        self.assertIn("second",str(error.exception))

    def test_device_binding_is_named_and_backend_rechecks_the_condition(self):
        d=PolicyDevice()
        snapshot=d.policy.read(d.cancel)
        plan=build_plan(snapshot,"station")
        d.config["network"]["wwan"]["device"]="eth9"
        changed=d.policy.read(d.cancel)
        with self.assertRaises(ValueError) as error:build_plan(changed,"station")
        self.assertIn("eth9",str(error.exception))
        with self.assertRaises(RuntimeError):d.policy._transaction(changed,plan["operations"],d.cancel)

    def test_matching_uplinks_share_name_across_networks_and_keep_distinct_macs(self):
        pairs = (("202", "202"), ("202", "202_5G"),
                 ("202_2.4G", "202_5g"), ("202-2.4GHz", "202-5GHz"))
        for legacy in (True, False):
            for first, second in pairs:
                with self.subTest(legacy=legacy, first=first, second=second):
                    d=PolicyDevice(legacy)
                    d.config["wireless"]["station"]["ssid"]=first
                    d.config["wireless"]["second"] = dict(d.config["wireless"]["station"],ssid=second,network="wwan2")
                    d.config["network"]["wwan2"] = dict(d.config["network"]["wwan"])
                    before=copy.deepcopy(d.config)
                    with patch("modules.capture_services.openwrt.station_identity_plan.generate_relocation_hostname",return_value="Grouped-Name") as generate:
                        snapshot=d.policy.read(d.cancel)
                        plan=build_plan(snapshot,"station")
                    self.assertEqual(generate.call_count,1)
                    self.assertEqual({row["name"] for row in plan["identities"]},{"Grouped-Name"})
                    self.assertEqual(len({row["mac"] for row in plan["identities"]}),2)
                    d.policy._transaction(snapshot,plan["operations"],d.cancel)
                    for section in ("station","second"):
                        for key in ("ssid","key","network","device"):
                            self.assertEqual(d.config["wireless"][section][key],before["wireless"][section][key])
                    for network in ("wwan","wwan2"):
                        self.assertEqual(d.config["network"][network]["hostname"],"Grouped-Name")

    def test_similar_but_distinct_names_are_not_grouped(self):
        from modules.capture_services.openwrt.station_identity_plan import station_name_groups
        names=("202", "2020", "202_5G_guest", "Home", "home", "202_5")
        groups=[(str(i),[(str(i),dict(ssid=name))],{}) for i,name in enumerate(names)]
        self.assertEqual(len(set(station_name_groups(groups).values())),len(names))

    def test_shared_interface_groups_are_joined_transitively(self):
        from modules.capture_services.openwrt.station_identity_plan import station_name_groups
        groups=[("one",[("a",dict(ssid="202")),("b",dict(ssid="303"))],{}),
                ("two",[("c",dict(ssid="202_5G"))],{}),
                ("three",[("d",dict(ssid="303_5G"))],{})]
        self.assertEqual(len(set(station_name_groups(groups).values())),1)
