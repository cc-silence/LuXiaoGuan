import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.handshake_batch_radio import (
    BatchRadioCleanupError, HandshakeBatchRadio,
)
from modules.capture_services.openwrt.handshake_capture import (
    OpenWrtHandshakeCapture, TargetBeaconMissing,
)
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.config_services.capture_settings_manager import CaptureSettingsManager


class BatchRadioTests(unittest.TestCase):
    def setUp(self):
        self.backend = NS(command_runner=Mock(), radio_session=Mock(),
                          _find_scan_radio=Mock(return_value="phy1"),
                          _maybe_takeover=Mock(return_value="takeover"),
                          _restore_task_wireless=Mock(return_value=[]))
        self.record = NS(radio="phy1", interface="wbmon1")
        self.backend.radio_session.create.return_value = self.record
        self.backend.radio_session.record = self.record
        self.session = HandshakeBatchRadio(self.backend, object())
        self.target = NS(band="2.4G", channel=4)

    def acquire(self, target=None):
        return self.session.acquire("session", {}, target or self.target, 1, 0, None, None)

    @patch("modules.capture_services.openwrt.handshake_batch_radio.phy_supports_channel", return_value=True)
    @patch("modules.capture_services.openwrt.handshake_batch_radio.read_monitor_status", return_value="ready")
    @patch("modules.capture_services.openwrt.handshake_batch_radio.require_idle_phy")
    def test_consecutive_targets_reuse_and_validate(self, idle, status, channel):
        self.assertIs(self.acquire(), self.record)
        self.assertIs(self.acquire(NS(band="2.4G", channel=11)), self.record)
        self.backend._maybe_takeover.assert_called_once()
        self.backend.radio_session.create.assert_called_once()
        self.backend._restore_task_wireless.assert_not_called()
        idle.assert_called_once()
        channel.assert_called_once()
        self.assertEqual(self.session.release(), [])
        self.backend._restore_task_wireless.assert_called_once()

    def test_band_change_restores_before_new_session(self):
        self.acquire()
        self.acquire(NS(band="5G", channel=36))
        self.backend._restore_task_wireless.assert_called_once()
        self.assertEqual(self.backend._maybe_takeover.call_count, 2)

    def test_failed_release_stops_band_change(self):
        self.acquire()
        self.backend._restore_task_wireless.return_value = ["restore failed"]
        with self.assertRaises(BatchRadioCleanupError):
            self.acquire(NS(band="5G", channel=36))
        self.assertEqual(self.backend._maybe_takeover.call_count, 1)
        self.assertEqual(self.session.takeover, "takeover")

    def test_absent_beacon_distinguished_from_other_errors(self):
        runner = Mock()
        runner.run.return_value = RemoteCommandResult("deauth_client", 1, "No such BSSID available.", "")
        capture = OpenWrtHandshakeCapture(runner, Mock())
        with self.assertRaises(TargetBeaconMissing):
            capture._deauth_clients("02:11:22:33:44:55", ["02:11:22:33:44:66"], "wbmon1", None)
        runner.run.return_value = RemoteCommandResult("deauth_client", 1, "other failure", "")
        with patch("modules.capture_services.openwrt.handshake_capture.time.sleep"):
            self.assertEqual(capture._deauth_clients("02:11:22:33:44:55", ["02:11:22:33:44:66"], "wbmon1", None), 0)

    def test_missing_beacon_finishes_capture_without_waiting_full_duration(self):
        import struct
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from modules.capture_services.openwrt.handshake_capture import DownloadedHandshake
        with TemporaryDirectory() as directory:
            workspace = NS(local_dir=Path(directory), remote_dir="/tmp/owned")
            runner = Mock()
            runner.run.return_value = RemoteCommandResult("offline", 0)
            processes = Mock()
            processes.is_running.return_value = True
            capture = OpenWrtHandshakeCapture(runner, processes)
            capture._verify_tuned_channel = Mock()
            capture._refresh_live_clients = Mock(return_value=["02:11:22:33:44:66"])
            capture._deauth_clients = Mock(side_effect=TargetBeaconMissing())
            manager = Mock()
            data = bytes.fromhex("d4c3b2a1") + struct.pack("<HHiiii", 2, 4, 0, 0, 65535, 105)
            manager.download_atomic.side_effect = lambda remote, local, event: Path(local).write_bytes(data)
            target = NS(bssid="02:11:22:33:44:55", ssid="offline", channel=6, band="2.4G", clients=())
            with patch("modules.capture_services.openwrt.handshake_capture.time.monotonic", side_effect=[0, 21, 21, 21]), \
                 patch("modules.capture_services.openwrt.handshake_capture.time.sleep"):
                result = capture.capture_passive(workspace, "wbmon1", "session", target, 180, manager,
                    active_deauth=True, defer_analysis=True, stop_on_missing_beacon=True)
            self.assertIsInstance(result, DownloadedHandshake)
            processes.stop.assert_called_once()
            capture._deauth_clients.assert_called_once()
            self.assertTrue(result.local_cap.exists())

    def test_cap_conversion_setting_persists_and_reaches_product_policy(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from modules.capture_services.backends.openwrt_backend import _convert_handshake_products
        with TemporaryDirectory() as folder:
            manager = CaptureSettingsManager(Path(folder) / "settings.json")
            manager.save({"openwrt": {"generate_cap": False}})
            settings = manager.load()["openwrt"]
            self.assertFalse(settings["generate_cap"])
            with patch("modules.capture_services.backends.openwrt_backend.cap_product_note", return_value=(None, "disabled")) as product:
                self.assertEqual(_convert_handshake_products("raw.cap", None, folder, "raw", "complete", settings)[1], "disabled")
            product.assert_called_once_with("raw.cap", enabled=False)

    def test_default_and_existing_setting(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as folder:
            manager = CaptureSettingsManager(Path(folder) / "settings.json")
            self.assertEqual(manager.normalize({})["openwrt"]["deauth_batches"], 10)
            self.assertEqual(manager.normalize({"openwrt": {"deauth_batches": 15}})["openwrt"]["deauth_batches"], 15)


if __name__ == "__main__":
    unittest.main()
