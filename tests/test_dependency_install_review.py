"""Three-pass dependency installation review regressions."""
from threading import Event
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
from types import SimpleNamespace

from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.package_manager import PackageManager, package_error_reason
from modules.capture_services.openwrt.signal_manager import SignalError


class OnlineRouteReviewTests(unittest.TestCase):
    def test_network_failure_during_complete_plan_allows_pc_retry(self):
        manager = OpenWrtDependencyManager(Mock())
        manager._preflight = Mock(return_value=PackageManager("opkg"))
        manager._execute_stage = Mock(return_value="")
        original = SignalError("transport", package_error_reason("Failed to download index, wget returned 4"))
        original.package_diagnostic = "dependency_package_plan exit=2\noriginal output"
        manager._package_change = Mock(side_effect=original)
        with self.assertRaises(SignalError) as caught:
            manager._install_online(("wpa-cli",), Event(), Mock())
        self.assertEqual(caught.exception.code, "offline_retry")
        self.assertEqual(caught.exception.package_diagnostic, original.package_diagnostic)
        self.assertEqual(manager._execute_stage.call_count, 1)

    def test_update_retry_keeps_original_package_diagnostic(self):
        manager = OpenWrtDependencyManager(Mock())
        manager._preflight = Mock(return_value=PackageManager("opkg"))
        original = SignalError("transport", package_error_reason("Failed to download index"))
        original.package_diagnostic = "original update output"
        manager._execute_stage = Mock(side_effect=original)
        with self.assertRaises(SignalError) as caught:
            manager._install_online(("wpa-cli",), Event(), Mock())
        self.assertEqual(caught.exception.code, "offline_retry")
        self.assertEqual(getattr(caught.exception, "package_diagnostic", None), original.package_diagnostic)


class ApkPlanReviewTests(unittest.TestCase):
    def test_index_version_must_match_native_plan(self):
        from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
        from modules.capture_services.openwrt.apk_package_feed import ApkPackage
        with self.assertRaises(ValueError):
            ApkOfflineInstaller._resolve([("tool", "1-r1")], {"tool": ApkPackage("tool", "2-r1", "https://fixture/", "base")})

    def test_native_simulation_rejects_replacement_with_changed_spacing(self):
        from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
        installer = ApkOfflineInstaller(lambda *_: "(1/2) Installing tool (1-r1)\n(2/2)  Upgrading libc (1 -> 2)")
        with self.assertRaises(ValueError):
            installer._simulate(("tool",))


class LocalIpkReviewTests(unittest.TestCase):
    def manager(self, plan):
        manager = OpenWrtDependencyManager(Mock())
        manager._preflight = Mock(return_value=PackageManager("opkg"))
        manager._execute = Mock(return_value="")
        manager._execute_stage = Mock(side_effect=lambda name, *_: plan if name == "dependency_local_plan" else "")
        return manager

    def test_pc_downloads_use_no_feed_config_and_verified_versions(self):
        paths = [Path("wpa-cli_1_all.ipk")]
        manager = self.manager("Installing wpa-cli (1) to root...")
        device = SimpleNamespace(version="23.05.4", package_architecture="aarch64_cortex-a53", package_manager="opkg")
        with patch("modules.capture_services.openwrt.dependency_manager.download_profile_packages", return_value=paths), patch("modules.capture_services.openwrt.package_space.ipk_installed_size", return_value=1), patch("modules.capture_services.openwrt.package_space.ipk_package_versions", return_value={"wpa-cli":"1"}) as versions:
            manager._install_offline(("wpa-cli",), device, None, False, Event(), Mock(), uploader=Mock())
        versions.assert_called_once_with(paths)
        plans = [c.args[1] for c in manager._execute_stage.call_args_list if c.args[0] == "dependency_local_plan"]
        self.assertIn("OPKG_CONF_DIR=", plans[0])
        self.assertIn("local.conf", plans[0])

    def test_pc_install_rejects_a_package_outside_verified_downloads(self):
        paths = [Path("wpa-cli_1_all.ipk")]
        manager = self.manager("Installing foreign (9) to root...")
        device = SimpleNamespace(version="23.05.4", package_architecture="aarch64_cortex-a53", package_manager="opkg")
        with patch("modules.capture_services.openwrt.dependency_manager.download_profile_packages", return_value=paths), patch("modules.capture_services.openwrt.package_space.ipk_installed_size", return_value=1), patch("modules.capture_services.openwrt.package_space.ipk_package_versions", return_value={"wpa-cli":"1"}):
            with self.assertRaises(SignalError):
                manager._install_offline(("wpa-cli",), device, None, False, Event(), Mock(), uploader=Mock())
        self.assertFalse(any(c.args[0] == "dependency_offline_install" for c in manager._execute_stage.call_args_list))


class CleanupReviewTests(unittest.TestCase):
    def test_cleanup_warning_delivery_cannot_replace_cancellation(self):
        from modules.capture_services.openwrt.offline_cleanup import cleanup_remote
        original = SignalError("cancelled", "original cancellation")
        execute = Mock(side_effect=RuntimeError("router disconnected"))
        tell = Mock(side_effect=RuntimeError("observer closed"))
        with self.assertRaises(SignalError) as caught:
            try:
                raise original
            finally:
                cleanup_remote(execute, "cleanup", "rm -rf /tmp/owned", tell)
        self.assertIs(caught.exception, original)

    def test_local_cache_warning_delivery_cannot_turn_success_into_failure(self):
        from modules.capture_services.openwrt import signal_package_feed as feed
        @feed.offline_cache_transaction(lambda result: result == "installed")
        def install(message=None):
            feed._cache_transaction.receipts.append((Path("root"), Path("root/file.ipk"), object()))
            return "installed"
        with patch.object(feed, "delete_inventory", side_effect=OSError("cache locked")):
            self.assertEqual(install(message=Mock(side_effect=RuntimeError("observer closed"))), "installed")
        @feed.offline_cache_transaction(lambda result: True)
        def next_task(message=None):
            return "next task"
        self.assertEqual(next_task(), "next task")


class SharedIpkEntryReviewTests(unittest.TestCase):
    def test_signal_pc_install_uses_the_same_verified_no_feed_transaction(self):
        from modules.capture_services.openwrt.signal_manager import RouterSignalManager
        manager = RouterSignalManager(Mock())
        manager._execute_stage = Mock(side_effect=lambda name, *_: "Installing lua (1) to root..." if name == "router_signal_local_plan" else "")
        manager._execute = Mock(return_value="")
        manager.probe_dependencies = Mock(return_value={key: True for key in ("lua", "uci", "ubus", "jsonc")})
        with patch("modules.capture_services.openwrt.package_space.ipk_package_versions", return_value={"lua":"1"}), patch("modules.capture_services.openwrt.package_space.ipk_installed_size", return_value=1), patch.object(OpenWrtDependencyManager, "_preflight", return_value=PackageManager("opkg")):
            result = manager._install_ipk_paths([Path("lua_1_all.ipk")], Event(), Mock(), uploader=Mock())
        self.assertTrue(result["lua"])
        commands = {call.args[0]: call.args[1] for call in manager._execute_stage.call_args_list}
        self.assertIn("OPKG_CONF_DIR=/tmp/winbox-signal-pkgs/no-feeds", commands["router_signal_local_plan"])
        self.assertIn("local.conf", commands["router_signal_offline_install"])
        self.assertNotIn("--force", commands["router_signal_offline_install"])

    def test_duplicate_downloaded_package_identity_is_rejected(self):
        from modules.capture_services.openwrt.package_space import ipk_package_versions
        with patch("modules.capture_services.openwrt.package_space.ipk_metadata", return_value={"Package":"tool", "Version":"1", "Installed-Size":"1"}):
            with self.assertRaises(SignalError):
                ipk_package_versions([Path("a.ipk"), Path("b.ipk")])
