"""Boot admission honors actual netifd and shell boolean spellings."""
import unittest
from tests.test_router_policy import PolicyDevice
from modules.capture_services.openwrt.router_policy_plan import build_plan
from modules.capture_services.openwrt.uci_booleans import netifd_bool,shell_bool


class BootBooleanTests(unittest.TestCase):
    def test_each_supported_boolean_consumer(self):
        for value in ("1","true"):self.assertTrue(netifd_bool(value,default=False))
        for value in ("0","false"):self.assertFalse(netifd_bool(value,default=True))
        for value in ("1","on","true","yes","enabled"):self.assertTrue(shell_bool(value,default=False))
        for value in ("0","off","false","no","disabled"):self.assertFalse(shell_bool(value,default=True))
        self.assertTrue(netifd_bool(None,default=True));self.assertFalse(shell_bool(None,default=False))
        for value in ("False","no",[],True):
            with self.assertRaises(ValueError):netifd_bool(value,default=True)

    def test_disabled_auto_and_ignored_dhcp_are_blocked_in_real_snapshots(self):
        for legacy in (True,False):
            for config,field,values in (("network","auto",("0","false")),("dhcp","ignore",("1","true","yes","on","enabled"))):
                for value in values:
                    device=PolicyDevice(legacy);device.config[config]["lan"][field]=value
                    snapshot=device.policy.read(device.cancel)
                    with self.assertRaises(ValueError):build_plan(snapshot,"boot",dict(enabled=False))
                    self.assertTrue(build_plan(snapshot,"boot",dict(enabled=True))["boot_enabled"])
                    self.assertFalse(device.commits)

    def test_supported_enabled_variants_remain_available(self):
        device=PolicyDevice()
        for auto in ("1","true"):
            for ignore in ("0","false","no","off","disabled"):
                device.config["network"]["lan"]["auto"]=auto;device.config["dhcp"]["lan"]["ignore"]=ignore
                self.assertFalse(build_plan(device.policy.read(device.cancel),"boot",dict(enabled=False))["boot_enabled"])

    def test_lan_editor_shows_false_without_enabling_it(self):
        from tests.test_standalone import APP
        from tests.test_common_settings_ui import NETWORK
        from modules.main_pages.packet_capture.openwrt.router_config_dialog import RouterConfigDialog
        from copy import deepcopy
        for value,checked in (("false",False),("0",False),("true",True),("1",True)):
            review=deepcopy(NETWORK);review["values"]["auto"]=value
            dialog=RouterConfigDialog(review)
            self.assertEqual(dialog.controls["auto"].isChecked(),checked)
            dialog.close();dialog.deleteLater();APP.processEvents()
