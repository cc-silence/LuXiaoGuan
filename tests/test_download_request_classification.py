"""Downloader request denial must not be confused with local package permissions."""
from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest

from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
from modules.capture_services.openwrt.package_manager import (
    PackageManager, package_error_reason, retryable_package_source_failure, REQUEST_SOURCE_ERROR,
)
from modules.capture_services.openwrt.installation_sources import install_with_sources

# Sanitized excerpt of XRJ 20261103_201612: preserve the exact downloader wording.
REQUEST = "Failed to send request: Operation not permitted"
FEEDBACK = ("Downloading https://downloads.openwrt.org/releases/22.03.5/packages/mipsel_24kc/base/Packages.gz\n"
            "*** Failed to download the package list\n\n" + REQUEST + "\n"
            "Collected errors:\n * opkg_download: Failed to download Packages.gz, wget returned 4.\n")


class DownloadRequestClassificationTests(unittest.TestCase):
    def setUp(self):
        source = patch('modules.capture_services.openwrt.temporary_package_sources.domestic_sources',
                       side_effect=lambda manager, base, cancel, tell: nullcontext(base))
        source.start()
        self.addCleanup(source.stop)

    def test_feedback_request_denial_is_retryable(self):
        for output in (FEEDBACK, REQUEST, "Failed to send request: Permission denied\r\n",
                       "  Failed to send request: Operation not permitted  \r\n"):
            with self.subTest(output=output):
                self.assertEqual(package_error_reason(output), REQUEST_SOURCE_ERROR)
                self.assertTrue(retryable_package_source_failure(package_error_reason(output)))

    def test_other_errors_still_block_fallback_in_either_order(self):
        errors = ('/var/lock/opkg.lock: Permission denied', 'chmod: Operation not permitted',
                  'opkg must run as root: not root', 'Read-only file system', 'Input/output error',
                  'Unable to lock database', 'No space left on device', 'Signature check failed.',
                  'UNTRUSTED signature', 'bad checksum', 'Cannot satisfy dependencies',
                  'incompatible with the architectures')
        for fatal in errors:
            for output in (fatal + '\n' + FEEDBACK, FEEDBACK + '\n' + fatal):
                with self.subTest(fatal=fatal, output=output):
                    reason = package_error_reason(output)
                    self.assertEqual(reason, package_error_reason(fatal))
                    self.assertFalse(retryable_package_source_failure(reason))

    def test_partial_or_prefixed_permission_lines_are_not_reinterpreted(self):
        for output in ('opkg: ' + REQUEST, REQUEST + ' while writing /etc/config',
                       'Failed to send request: Permission denied: /etc/opkg.conf'):
            with self.subTest(output=output):
                self.assertFalse(retryable_package_source_failure(package_error_reason(output)))

    def route(self, output, signal=False, cancelled=False, timed_out=False, stderr=""):
        result = NS(stdout=output, stderr=stderr, exit_code=6, cancelled=cancelled, timed_out=timed_out)
        if signal:
            manager = RouterSignalManager(Mock())
            manager.probe_dependencies = Mock(return_value=dict(lua=True, uci=False, ubus=True, jsonc=True))
            manager._installation_environment = Mock(return_value=PackageManager('opkg'))
            manager._run_command = Mock(return_value=result)
            online = lambda: manager.install_dependencies(None, Mock())
        else:
            manager = OpenWrtDependencyManager(Mock())
            manager._preflight = Mock(return_value=PackageManager('opkg'))
            manager.command_runner.run.return_value = result
            online = lambda: manager._install_online(('wpa-cli',), None, Mock())
        return online, Mock()

    def test_both_real_installation_entries_reach_pc_with_correct_reason(self):
        for signal in (False, True):
            with self.subTest(signal=signal):
                online, computer = self.route(FEEDBACK, signal)
                self.assertEqual(install_with_sources(lambda:False, online, computer, Mock()),
                                 '\u7535\u8111\u4e0b\u8f7d\u540e\u4f20\u5230\u8def\u7531\u5668\u5b89\u88c5')
                computer.assert_called_once_with()
                online, _ = self.route(FEEDBACK, signal)
                with self.assertRaises(SignalError) as caught:
                    online()
                self.assertEqual(caught.exception.code, 'offline_retry')
                self.assertIn(REQUEST_SOURCE_ERROR, str(caught.exception))
                if not signal:
                    self.assertIn(REQUEST, caught.exception.package_diagnostic)

    def test_mixed_signature_or_write_denial_never_starts_pc_route(self):
        for signal in (False, True):
            for fatal in ('Signature check failed.', 'open /etc/opkg.conf: Permission denied'):
                with self.subTest(signal=signal, fatal=fatal):
                    online, computer = self.route(FEEDBACK + fatal, signal)
                    with self.assertRaises(SignalError):
                        install_with_sources(lambda:False, online, computer, Mock())
                    computer.assert_not_called()

    def test_cancellation_and_timeout_do_not_start_pc_route(self):
        for signal in (False, True):
            for flags in (dict(cancelled=True), dict(timed_out=True)):
                with self.subTest(signal=signal, flags=flags):
                    online, computer = self.route(FEEDBACK, signal, **flags)
                    with self.assertRaises(SignalError):
                        install_with_sources(lambda:False, online, computer, Mock())
                    computer.assert_not_called()

    def test_downloader_denial_on_stderr_reaches_pc_in_both_entries(self):
        for signal in (False, True):
            with self.subTest(signal=signal):
                online, computer = self.route('Downloading Packages.gz', signal, stderr=FEEDBACK)
                install_with_sources(lambda:False, online, computer, Mock())
                computer.assert_called_once_with()

    def test_fatal_error_in_other_stream_still_stops_both_entries(self):
        fatal_errors = ('Signature check failed.', 'open: Permission denied',
                        'Read-only file system', 'Input/output error', 'Unable to lock database')
        for signal in (False, True):
            for fatal in fatal_errors:
                for stdout, stderr in ((fatal, FEEDBACK), (FEEDBACK, fatal)):
                    with self.subTest(signal=signal, fatal=fatal, stderr=stderr):
                        online, computer = self.route(stdout, signal, stderr=stderr)
                        with self.assertRaises(SignalError):
                            install_with_sources(lambda:False, online, computer, Mock())
                        computer.assert_not_called()
