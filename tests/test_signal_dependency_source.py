"""Signal installation uses the common bundle and bounded network fallback."""
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
from modules.capture_services.openwrt.signal_commands import SIGNAL_PACKAGES
from modules.capture_services.openwrt.bundle_specs import COMMON_ROOTS


class SignalDependencySourceTests(unittest.TestCase):
    def setUp(self):
        self.manager = RouterSignalManager(Mock())
        self.device = SimpleNamespace(version="22.03.7", target="ramips/mt7621",
            package_architecture="mipsel_24kc", package_manager="opkg", distribution="OpenWrt")
        self.ready = dict(lua=True, uci=True, ubus=True, jsonc=True)
        self.manager.probe_dependencies = Mock(return_value=self.ready)
        self.manager.install_dependencies = Mock(return_value=self.ready)
        self.manager.install_offline = Mock(return_value=self.ready)
        self.event = Event()

    def test_signal_roots_are_part_of_common_bundle(self):
        self.assertTrue(set(SIGNAL_PACKAGES).issubset(COMMON_ROOTS))

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=True)
    def test_common_bundle_success_does_not_access_network(self, local):
        self.assertEqual(self.manager.install_auto(self.device, None, self.event), self.ready)
        self.assertEqual(local.call_args.args[1], SIGNAL_PACKAGES)
        self.manager.install_dependencies.assert_not_called()
        self.manager.install_offline.assert_not_called()

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=True)
    def test_incomplete_local_install_is_not_reported_as_success(self, local):
        self.manager.probe_dependencies.return_value = dict(self.ready, uci=False)
        with self.assertRaises(SignalError):
            self.manager.install_auto(self.device, None, self.event)
        self.manager.install_dependencies.assert_not_called()

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=False)
    def test_missing_bundle_uses_router_network(self, local):
        self.manager.install_auto(self.device, None, self.event)
        self.manager.install_dependencies.assert_called_once()
        self.manager.install_offline.assert_not_called()

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=False)
    def test_router_network_failure_falls_back_to_pc_download(self, local):
        self.manager.install_dependencies.side_effect = SignalError("offline_retry", "offline")
        self.assertEqual(self.manager.install_auto(self.device, None, self.event), self.ready)
        self.manager.install_offline.assert_called_once()

    @patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local", return_value=False)
    def test_cancellation_does_not_start_pc_download(self, local):
        self.manager.install_dependencies.side_effect = SignalError("cancelled", "cancelled")
        with self.assertRaises(SignalError):
            self.manager.install_auto(self.device, None, self.event)
        self.manager.install_offline.assert_not_called()
