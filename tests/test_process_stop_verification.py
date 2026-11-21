"""Execute stop readback against fake procfs; never signal real processes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from tests.test_process_recovery_boundaries import BASH, ProcessFixture
from modules.capture_services.openwrt.remote_process import RemoteProcessManager, RemoteProcessHandle
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.process_stop_verification import confirm_stopped


@unittest.skipUnless(BASH.is_file(), "Bash unavailable")
class StopVerificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.device = ProcessFixture(Path(temp.name))
        self.manager = RemoteProcessManager(self.device)
        self.handle = RemoteProcessHandle(100, "123", "airodump-ng", "finished",
                                          "/tmp/winbox-capture/finished")

    def test_absent_pid_is_positive_stop_confirmation(self):
        self.assertTrue(confirm_stopped(self.manager, self.handle))
        self.assertEqual(self.device.signals(), [])

    def test_live_pid_state_is_not_treated_as_success(self):
        self.device.process(100, ["airodump-ng"])
        self.assertFalse(confirm_stopped(self.manager, self.handle))
        self.assertEqual(self.device.signals(), [])

    def test_reused_pid_is_not_signalled(self):
        self.device.process(100, ["unrelated"], token="456")
        self.assertTrue(confirm_stopped(self.manager, self.handle))
        self.assertEqual(self.device.signals(), [])

    def test_unreadable_stat_does_not_mean_stopped(self):
        self.device.process(100, ["airodump-ng"])
        (self.device.root / "proc/100/stat").unlink()
        self.assertFalse(confirm_stopped(self.manager, self.handle))

    def test_stop_timeout_with_positive_readback_releases_owned_handle(self):
        original = self.device.run
        def execute(name, command, **kwargs):
            if name.startswith("stop_"):
                return RemoteCommandResult(name, -1, timed_out=True)
            return original(name, command, **kwargs)
        self.device.run = execute
        self.manager.register(self.handle)
        self.assertTrue(self.manager.stop(self.handle))
        self.assertEqual(self.manager.handles(), ())

    def test_stop_timeout_with_live_pid_keeps_recovery_record(self):
        self.device.process(100, ["airodump-ng"])
        original = self.device.run
        def execute(name, command, **kwargs):
            if name.startswith("stop_"):
                return RemoteCommandResult(name, -1, timed_out=True)
            return original(name, command, **kwargs)
        self.device.run = execute
        self.manager.register(self.handle)
        with self.assertRaises(RuntimeError):
            self.manager.stop(self.handle)
        self.assertEqual(self.manager.handles(), (self.handle,))


class PendingRecoveryAdmissionTests(unittest.TestCase):
    def test_pending_message_explains_relogin_scan_and_failure_steps(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        backend = OpenWrtCaptureBackend()
        backend.reconnect_cleanup.pending = object()
        with self.assertRaises(RuntimeError) as caught:
            backend.reconnect_cleanup.require_ready()
        message = str(caught.exception)
        for word in ("\u65ad\u5f00", "\u91cd\u65b0\u767b\u5f55", "\u540c\u4e00\u53f0",
                     "\u6e05\u7406\u6210\u529f", "\u626b\u63cf", "\u5bfc\u51fa"):
            self.assertIn(word, message)

    def test_preflight_stops_before_radio_probe_when_cleanup_is_pending(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        backend = OpenWrtCaptureBackend()
        backend.reconnect_cleanup.require_ready = Mock(side_effect=RuntimeError("pending cleanup"))
        backend.command_runner.run = Mock()
        with self.assertRaisesRegex(RuntimeError, "pending cleanup"):
            backend.probe_handshake_takeover((), {})
        backend.command_runner.run.assert_not_called()
