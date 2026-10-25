"""Use official luci.jsonc empty-table semantics at the Lua/Python boundary."""
import json
import threading
import unittest
from tests.test_relocation import MovingDevice
from modules.capture_services.openwrt.relocation_manager import RelocationManager
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager


def jsonc_tables(value):
    if isinstance(value,dict):
        return {key:jsonc_tables(item) for key,item in value.items()} if value else []
    if isinstance(value,list):
        return [jsonc_tables(item) for item in value]
    return value


class JsoncMovingDevice(MovingDevice):
    def __init__(self):
        super().__init__()
        self.lua.globals().bridge_json_stringify=lambda value:json.dumps(jsonc_tables(self.unwrap(value)))


class RelocationJsonTests(unittest.TestCase):
    def test_no_skipped_mac_targets_still_opens_valid_preview(self):
        device=JsoncMovingDevice()
        manager=RelocationManager(WirelessSetupManager(device))
        review=manager.preview(threading.Event())
        self.assertEqual(review["skipped"],[])
        self.assertEqual(review["skip_reasons"],{})
        self.assertEqual(len(review["targets"]),2)

    def test_nonempty_skip_reasons_preserve_the_real_device_reason(self):
        device=JsoncMovingDevice()
        device.config["network"]["tunnel"]={".type":"interface","proto":"wireguard"}
        manager=RelocationManager(WirelessSetupManager(device))
        review=manager.preview(threading.Event())
        self.assertEqual(review["skip_reasons"],{"tunnel":"protocol"})
