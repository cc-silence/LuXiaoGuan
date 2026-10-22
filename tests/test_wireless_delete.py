"""Actual Lua deletion, ownership protection and four-config rollback."""
import copy
import unittest
from unittest.mock import patch
from modules.capture_services.openwrt.signal_manager import SignalError
from tests import test_wireless_bridges as fixture

@unittest.skipUnless(fixture.LuaRuntime, "Lua runtime unavailable")
class WirelessDeleteTests(unittest.TestCase):
    setUp=fixture.BridgeTests.setUp
    create=fixture.BridgeTests.create

    def delete(self,section):
        revision=self.manager.inspect(self.cancel).data["revision"]
        return self.manager.delete(section,revision,self.cancel,self.message)

    def test_ap_delete_preserves_radio_network_and_other_signals(self):
        self.device.config["wireless"]["guest"]=dict(self.device.config["wireless"]["home"],ssid="Guest")
        before=copy.deepcopy(self.device.config)
        self.delete("home")
        del before["wireless"]["home"]
        self.assertEqual(self.device.config,before)

    def test_managed_relay_delete_restores_dhcp_and_removes_owned_networks(self):
        request,value=self.create("relayd")
        helper=self.device.config["wireless"][value["section"]]["rm_bridge"]
        self.delete(value["section"])
        self.assertNotIn(value["section"],self.device.config["wireless"])
        self.assertNotIn(helper,self.device.config["network"])
        self.assertNotIn(value["network"],self.device.config["network"])
        self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"0")
        self.assertNotIn(value["network"],self.device.config["firewall"]["zone_lan"]["network"])

    def test_wds_delete_restores_dhcp_and_keeps_lan(self):
        request,value=self.create("wds")
        self.delete(value["section"])
        self.assertIn("lan",self.device.config["network"])
        self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"0")

    def test_suspended_bridge_delete_keeps_restored_dhcp(self):
        request,value=self.create("relayd")
        self.manager.toggle(value["section"],False,self.cancel)
        self.delete(value["section"])
        self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"0")

    def test_shared_upstream_is_kept_even_when_owned(self):
        request,value=self.create("relayd")
        self.device.config["wireless"]["other"]={".type":"wifi-iface","device":"radio7","mode":"ap","ssid":"Other","network":value["network"]}
        result=self.delete(value["section"])
        self.assertTrue(result["retained_network"])
        self.assertIn(value["network"],self.device.config["network"])

    def test_unowned_legacy_network_is_preserved(self):
        self.device.config["wireless"]["upstream"]={".type":"wifi-iface","device":"radio7","mode":"sta","ssid":"Legacy","network":"wan"}
        before=copy.deepcopy(self.device.config["network"])
        result=self.delete("upstream")
        self.assertTrue(result["retained_network"])
        self.assertEqual(self.device.config["network"],before)

    def test_cancel_after_apply_restores_all_configs(self):
        request,value=self.create("relayd")
        before=copy.deepcopy(self.device.config)
        original=self.manager._call
        def call(action,*args,**kwargs):
            result=original(action,*args,**kwargs)
            if action=="delete_status":self.cancel.set()
            return result
        with patch.object(self.manager,"_call",side_effect=call):
            with self.assertRaises((InterruptedError, SignalError)) as failure:self.delete(value["section"])
        if isinstance(failure.exception, SignalError): self.assertEqual(failure.exception.code,"cancelled")
        self.assertEqual(self.device.config,before)
        self.assertFalse(self.device.sessions)

    def test_stale_revision_never_deletes(self):
        before=copy.deepcopy(self.device.config)
        with self.assertRaises(Exception):self.manager.delete("home","0"*64,self.cancel,self.message)
        self.assertEqual(self.device.config,before)


class DependencyRefreshTests(unittest.TestCase):
    def test_changed_dependency_invalidates_once_without_synchronous_probe(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from modules.capture_services.wireless_setup_service import WirelessSetupService
        from modules.capture_services.openwrt.wireless_setup_models import WirelessState,WirelessEnvironment
        from modules.capture_services.openwrt.models import DependencyInfo,DependencyState
        identity=object()
        context=SimpleNamespace(_identity=identity,_dependency_key=(),_connection_identity=lambda:identity,
            active=False,_enabled=True,_pending=False,snapshot=WirelessState(environment=WirelessEnvironment(data={"radios":[]})),
            snapshotChanged=Mock(),_checkRequested=Mock())
        state=SimpleNamespace(connection_state=SimpleNamespace(value="connected"),dependencies=(DependencyInfo("wpa_cli","CLI",DependencyState.READY),))
        WirelessSetupService._connection_changed(context,state)
        self.assertTrue(context._pending)
        self.assertIsNone(context.snapshot.environment)
        WirelessSetupService._connection_changed(context,state)
        context._checkRequested.emit.assert_called_once()
