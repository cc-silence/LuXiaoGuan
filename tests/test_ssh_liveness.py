"""Probe response validation, deadline and resource ownership."""
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.ssh_liveness import probe_authenticated_connection, MARKER


class LivenessTests(unittest.TestCase):
    def test_only_exact_success_response_is_accepted(self):
        for data, code, expected in ((MARKER, 0, True), (b"ok\n", 0, False),
                                     (MARKER, 1, False), (b"x"*257, 0, False)):
            with self.subTest(data=data, code=code):
                channel = Mock()
                channel.recv_ready.side_effect = [True, False]
                channel.recv.return_value = data
                channel.exit_status_ready.return_value = True
                channel.recv_exit_status.return_value = code
                owner = Mock()
                owner._open_owned.return_value = channel
                self.assertEqual(probe_authenticated_connection(owner, Mock()), expected)
                channel.close.assert_called_once()
                self.assertTrue(owner._open_owned.call_args.kwargs["isolate_failure"])

    def test_silent_response_has_one_deadline_and_closes_channel(self):
        owner, channel = Mock(), Mock()
        owner._open_owned.return_value = channel
        with patch("modules.capture_services.openwrt.ssh_liveness.time.monotonic", side_effect=[0, 4]):
            self.assertFalse(probe_authenticated_connection(owner, Mock(), timeout=3))
        channel.close.assert_called_once()
        owner._open_owned.assert_called_once()

    def test_probe_close_error_does_not_replace_original_failure(self):
        owner, channel = Mock(), Mock()
        owner._open_owned.return_value = channel
        channel.close.side_effect = OSError("fixture close failure")
        with patch("modules.capture_services.openwrt.ssh_liveness.time.monotonic", side_effect=[0, 4]):
            self.assertFalse(probe_authenticated_connection(owner, Mock(), timeout=3))
        channel.close.assert_called_once()
