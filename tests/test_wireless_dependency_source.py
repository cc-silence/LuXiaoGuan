"""AP and relay install approved helpers from the common offline bundle."""
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.bundle_specs import COMMON_ROOTS
from modules.capture_services.openwrt.wireless_dependencies import WIRELESS_HELPER_PACKAGES


class WirelessDependencySourceTests(unittest.TestCase):
    def setUp(self):
        self.manager = WirelessSetupManager(Mock())
        self.device = SimpleNamespace(version="22.03.7", target="ramips/mt7621",
            package_architecture="mipsel_24kc", package_manager="opkg", distribution="OpenWrt")
        self.event = Event()
        self.message = Mock()
        self.ready = WirelessEnvironment(data={"radios": []})
        self.manager.inspect = Mock(side_effect=[WirelessEnvironment(missing=("wpa-cli",)), self.ready])

    def test_all_wireless_helpers_are_explicit_common_roots(self):
        self.assertTrue(set(WIRELESS_HELPER_PACKAGES).issubset(COMMON_ROOTS))
        self.assertTrue({"rpcd", "rpcd-mod-iwinfo", "wpa-cli"}.issubset(COMMON_ROOTS))
        self.assertNotIn("wpad-full", COMMON_ROOTS)

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=True)
    @patch("modules.capture_services.openwrt.dependency_manager.OpenWrtDependencyManager._install_online")
    def test_local_bundle_prevents_network_install(self, online, local):
        result = self.manager.install(("wpa-cli",), self.device, self.event, self.message)
        self.assertTrue(result.ready)
        self.assertEqual(local.call_args.args[1], ("wpa-cli",))
        online.assert_not_called()

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=False)
    @patch("modules.capture_services.openwrt.dependency_manager.OpenWrtDependencyManager._install_online")
    def test_missing_bundle_retains_router_network_fallback(self, online, local):
        self.manager.install(("wpa-cli",), self.device, self.event, self.message)
        online.assert_called_once_with(("wpa-cli",), self.event, self.message)

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=False)
    @patch("modules.capture_services.openwrt.dependency_manager.OpenWrtDependencyManager._install_offline")
    @patch("modules.capture_services.openwrt.dependency_manager.OpenWrtDependencyManager._install_online")
    def test_router_offline_can_download_on_pc(self, online, offline, local):
        online.side_effect = SignalError("offline_retry", "offline")
        self.manager.install(("wpa-cli",), self.device, self.event, self.message)
        offline.assert_called_once()

    def test_wireless_stack_replacement_is_rejected(self):
        self.manager.inspect = Mock(return_value=WirelessEnvironment(missing=("wpad-full",)))
        with self.assertRaises(SignalError):
            self.manager.install(("wpad-full",), self.device, self.event, self.message)

    def test_new_unapproved_missing_component_stops_install(self):
        self.manager.inspect = Mock(return_value=WirelessEnvironment(missing=("wpa-cli", "iw")))
        with self.assertRaises(SignalError):
            self.manager.install(("wpa-cli",), self.device, self.event, self.message)

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=True)
    def test_new_rpc_plugin_is_reloaded_only_after_install(self, local):
        self.manager.inspect = Mock(side_effect=[WirelessEnvironment(missing=("rpcd-mod-iwinfo",)),
            WirelessEnvironment(data={"reload_needed": True}), self.ready])
        self.manager.signal._execute = Mock()
        self.manager.install(("rpcd-mod-iwinfo",), self.device, self.event, self.message)
        self.manager.signal._execute.assert_called_once()
        self.assertIn("/etc/init.d/rpcd restart", self.manager.signal._execute.call_args.args[1])

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=False)
    @patch("modules.capture_services.openwrt.dependency_manager.OpenWrtDependencyManager._install_online")
    def test_optional_relayd_is_installed_only_after_approval_and_reprobed(self, online, local):
        before=WirelessEnvironment(data={"features":{"relayd":False}})
        after=WirelessEnvironment(data={"features":{"relayd":True}})
        self.manager.inspect=Mock(side_effect=[before,after])
        self.assertIs(self.manager.install(("relayd",),self.device,self.event,self.message),after)
        online.assert_called_once_with(("relayd",),self.event,self.message)

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=True)
    def test_optional_relayd_missing_after_install_never_reports_ready(self, local):
        self.manager.inspect=Mock(return_value=WirelessEnvironment(data={"features":{"relayd":False}}))
        with self.assertRaises(SignalError):self.manager.install(("relayd",),self.device,self.event,self.message)
