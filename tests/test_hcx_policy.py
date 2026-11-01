"""Shared eligibility and firmware-native fallback, without weakening capture checks."""
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.hcx_policy import supports_modern_hcx, installation_satisfied
from modules.capture_services.openwrt.hcx_primary_installer import install_primary
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState
from modules.main_pages.packet_capture.openwrt.auto_capture_page import is_modern_hcx_supported_on_device


class HcxPolicyTests(unittest.TestCase):
    def test_page_and_installer_share_existing_eligibility(self):
        for version, kernel, expected in (
                ("19.07.10", "4.14.275", False), ("21.02-SNAPSHOT", "5.4.188", False), ("21.02.7", "5.4.238", False),
                ("22.03.7", "5.10.221", False), ("23.05.6", "5.15.189", True),
                ("24.10.8", "6.6.1", True), ("25.12.6", "6.12.1", True),
                ("24.10.8", "5.10.1", False), ("custom", "5.15.1", True),
                ("unknown", "", False)):
            device = NS(version=version, kernel_version=kernel)
            with self.subTest(version=version, kernel=kernel):
                self.assertEqual(supports_modern_hcx(device), expected)
                self.assertEqual(is_modern_hcx_supported_on_device(device), expected)
                self.assertEqual(OpenWrtDependencyManager(Mock())._is_modern_hcx_supported(device), expected)

    def test_unsupported_device_never_opens_or_uploads_designated_634(self):
        manager = Mock()
        device = NS(version="22.03.7", kernel_version="5.10.221",
                    package_manager="opkg", package_architecture="mipsel_24kc")
        with patch("modules.capture_services.openwrt.hcx_bundle.primary_package",
                   side_effect=AssertionError("must use firmware-native package")):
            self.assertFalse(install_primary(manager, device, True, Event(), Mock(), None, None))
        manager.command_runner.run.assert_not_called()

    def test_unsupported_missing_hcx_installs_official_package_and_retains_limited_state(self):
        manager = OpenWrtDependencyManager(Mock())
        before = DependencyInfo("pmkid", "PMKID", DependencyState.MISSING, ("hcxdumptool",))
        after = DependencyInfo("pmkid", "PMKID", DependencyState.OUTDATED,
                               detail="Official 6.2.4 installed; automatic capture unavailable")
        manager.check = Mock(side_effect=[(before,), (after,)])
        tell = Mock()
        device = NS(version="22.03.7", kernel_version="5.10.221",
                    package_manager="opkg", package_architecture="mipsel_24kc")
        with patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local",
                   return_value=True) as official:
            result = manager.install_all({"pmkid": True}, True, device, None,
                                         Event(), tell, include_sftp=False)
        official.assert_called_once()
        self.assertEqual(official.call_args.args[1], ("hcxdumptool",))
        self.assertEqual(result[0].state, DependencyState.OUTDATED)
        self.assertTrue(installation_satisfied(result[0]))
        self.assertIn(unittest.mock.call(after.detail, "warning"), tell.call_args_list)

    def test_reported_r6220_snapshot_does_not_request_openssl3_or_upload_634(self):
        runner = Mock()
        manager = OpenWrtDependencyManager(runner)
        before = DependencyInfo("pmkid", "PMKID", DependencyState.MISSING, ("hcxdumptool",))
        after = DependencyInfo("pmkid", "PMKID", DependencyState.OUTDATED,
                               detail="Official 6.0.1 installed; automatic capture unavailable")
        manager.check = Mock(side_effect=[(before,), (after,)])
        manager._install_online = Mock()
        manager._install_offline = Mock()
        device = NS(version="21.02-SNAPSHOT", kernel_version="5.4.188",
                    package_manager="opkg", package_architecture="mipsel_24kc")
        with patch("modules.capture_services.openwrt.hcx_bundle.primary_package",
                   side_effect=AssertionError("Must not read the 6.3.4 payload")), \
             patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local",
                   return_value=True) as local:
            result = manager.install_all({"pmkid": True}, True, device, None,
                                         Event(), Mock(), include_sftp=False)
        self.assertEqual(local.call_args.args[1], ("hcxdumptool",))
        local.assert_called_once()
        runner.run.assert_not_called()
        manager._install_online.assert_not_called()
        manager._install_offline.assert_not_called()
        self.assertEqual(result[0].state, DependencyState.OUTDATED)
        self.assertTrue(installation_satisfied(result[0]))

    def test_install_success_does_not_mark_old_tool_ready_for_automatic_capture(self):
        old = DependencyInfo("pmkid", "PMKID", DependencyState.OUTDATED)
        self.assertTrue(installation_satisfied(old))
        self.assertNotEqual(old.state, DependencyState.READY)
        for state in (DependencyState.MISSING, DependencyState.UNKNOWN, DependencyState.UPGRADABLE):
            self.assertFalse(installation_satisfied(DependencyInfo("pmkid", "PMKID", state)))
        self.assertFalse(installation_satisfied(DependencyInfo("wps", "WPS", DependencyState.OUTDATED)))


if __name__ == "__main__":
    unittest.main()
