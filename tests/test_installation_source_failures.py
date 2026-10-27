"""Failed route handoff retains evidence without changing execution policy."""
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.installation_sources import install_with_sources, network_retry_error
from modules.capture_services.openwrt.signal_manager import SignalError


class SourceFailureTests(unittest.TestCase):
    def run_failure(self, fallback, diagnostic="dependency_update exit=6\nFailed to download fixture Packages.gz"):
        online = SignalError("transport", "router network failure")
        online.package_diagnostic = diagnostic
        computer = Mock(side_effect=fallback)
        tell = Mock()
        with self.assertRaises(type(fallback)) as caught:
            install_with_sources(lambda: False, Mock(side_effect=network_retry_error(online)), computer, tell)
        computer.assert_called_once_with()
        return caught.exception

    def test_snapshot_rejection_keeps_update_output_and_both_reasons(self):
        error = self.run_failure(SignalError("environment", "SNAPSHOT unsupported"))
        self.assertEqual(error.code, "environment")
        self.assertIn("router network failure", str(error))
        self.assertIn("SNAPSHOT unsupported", str(error))
        self.assertIn("dependency_update exit=6", error.package_diagnostic)
        self.assertIn("Failed to download fixture Packages.gz", error.package_diagnostic)
        self.assertIn("SNAPSHOT unsupported", error.package_diagnostic)

    def test_both_native_diagnostics_survive_and_remain_bounded(self):
        fallback = SignalError("transport", "computer timeout")
        fallback.package_diagnostic = "computer detail"
        error = self.run_failure(fallback, "x" * 100000 + "router detail")
        self.assertIn("router detail", error.package_diagnostic)
        self.assertIn("computer detail", error.package_diagnostic)
        self.assertLess(len(error.package_diagnostic), 17000)

    def test_cancellation_keeps_original_exception_and_message(self):
        original = SignalError("cancelled", "cancelled by user")
        error = self.run_failure(original)
        self.assertIs(error, original)
        self.assertEqual(str(error), "cancelled by user")
        self.assertIn("dependency_update exit=6", error.package_diagnostic)

    def test_other_exception_keeps_type_and_identity(self):
        original = OSError("disk unavailable")
        error = self.run_failure(original)
        self.assertIs(error, original)
        self.assertIn("disk unavailable", error.package_diagnostic)

    def test_all_combined_output_passes_through_privacy_filter(self):
        with patch("modules.log_services.sanitized_text", return_value="sanitized") as sanitize:
            error = self.run_failure(SignalError("environment", "unsupported"))
        self.assertEqual(error.package_diagnostic, "sanitized")
        self.assertIn("router_online", sanitize.call_args.args[0])

    def test_service_can_export_original_update_output_after_fallback_failure(self):
        from tests.test_dependency_diagnostic_paths import Owner
        from modules.capture_services.dependency_service import OpenWrtDependencyService
        owner = Owner()
        service = OpenWrtDependencyService(owner)
        failure = self.run_failure(SignalError("environment", "SNAPSHOT unsupported"))
        service.manager.install_all = Mock(side_effect=failure)
        service._save_diagnostic = Mock(return_value="fixture/package-manager.log")
        with patch("modules.capture_services.dependency_service.check_tools", return_value=()):
            self.assertTrue(service.install_async({}, scope="wpa_cli"))
            operation, event = owner.pending
            result = operation(event, Mock())
        self.assertEqual(result["diagnostic_path"], "fixture/package-manager.log")
        self.assertIn("dependency_update exit=6", service._save_diagnostic.call_args.args[1])
        owner.task_controller.finish(failed=True)
