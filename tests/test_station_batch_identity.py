"""Ordinary relay identity is editable; blocked layouts explain the exact cause."""
import copy
import unittest
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
