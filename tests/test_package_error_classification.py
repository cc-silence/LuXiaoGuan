"""Package status messages must not hide real download failures."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.installation_sources import install_with_sources
from modules.capture_services.openwrt.package_manager import PackageManager, package_error_reason
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError


NETWORK_FAILURE = "Collected errors:\n * opkg_download: Failed to download Packages.gz, wget returned 4."
SUCCESS = "Signature check passed.\n"


class PackageErrorClassificationTests(unittest.TestCase):
    def test_passed_signature_does_not_hide_network_failure(self):
        self.assertEqual(package_error_reason(SUCCESS + NETWORK_FAILURE), package_error_reason(NETWORK_FAILURE))
        self.assertNotEqual(package_error_reason(SUCCESS), package_error_reason("Signature check failed."))

    def test_real_signature_failure_blocks_fallback_even_with_later_success(self):
        for failure in ("Signature check failed.", "Failed to verify the signature of feed", "UNTRUSTED signature", "bad checksum"):
            with self.subTest(failure=failure):
                self.assertEqual(package_error_reason(failure + "\n" + SUCCESS + NETWORK_FAILURE),
                                 package_error_reason(failure))

    def test_early_signature_failure_is_not_lost_in_long_update_output(self):
        text = "Signature check failed.\n" + SUCCESS * 300 + NETWORK_FAILURE
        self.assertEqual(package_error_reason(text), package_error_reason("Signature check failed."))

    def test_pass_status_does_not_hide_space_or_package_errors(self):
        for error in ("No space left on device", "Unknown package wpa-cli", "Cannot satisfy dependencies"):
            with self.subTest(error=error):
                self.assertEqual(package_error_reason(SUCCESS + error), package_error_reason(error))

    def test_real_update_failure_uses_pc_route_only_for_network_errors(self):
        for output, fallback in ((SUCCESS + NETWORK_FAILURE, True),
                                 ("Signature check failed.\n" + NETWORK_FAILURE, False)):
            with self.subTest(fallback=fallback):
                runner = Mock()
                runner.run.return_value = SimpleNamespace(stdout=output, stderr="", exit_code=1,
                                                          cancelled=False, timed_out=False)
                manager = OpenWrtDependencyManager(runner)
                manager._preflight = Mock(return_value=PackageManager("opkg"))
                computer = Mock()
                action = lambda: install_with_sources(lambda: False,
                    lambda: manager._install_online(("wpa-cli",), None, Mock()), computer, Mock())
                if fallback:
                    action()
                    computer.assert_called_once()
                else:
                    with self.assertRaises(SignalError):
                        action()
                    computer.assert_not_called()
                runner.run.assert_called_once()

    def test_signal_install_preserves_failure_in_stdout_when_stderr_is_network_error(self):
        manager = RouterSignalManager(Mock())
        manager._run_command = Mock(return_value=SimpleNamespace(stdout="Signature check failed.",
            stderr=NETWORK_FAILURE, exit_code=1, cancelled=False, timed_out=False))
        with self.assertRaises(SignalError) as caught:
            manager._execute_stage("signal_update", "opkg update", 30, None, "update")
        self.assertIn(package_error_reason("Signature check failed."), str(caught.exception))
