"""The reconnect window begins after sending, without blocking capture."""
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.handshake_capture import OpenWrtHandshakeCapture
from modules.capture_services.openwrt.models import AccessPointInfo


class DeauthIntervalTests(unittest.TestCase):
    def simulate(self, command_seconds, capture_seconds=90):
        with TemporaryDirectory() as directory:
            clock = [0.0]
            starts, ends, checks = [], [], []
            runner = Mock()
            runner.run.return_value = RemoteCommandResult("offline",0)
            processes = Mock();processes.is_running.return_value = True
            capture = OpenWrtHandshakeCapture(runner, processes)
            capture._verify_tuned_channel = Mock()
            capture._refresh_live_clients = Mock(return_value=["02:11:22:33:44:66"])
            def send(*args, **kwargs):
                starts.append(clock[0]);clock[0] += command_seconds;ends.append(clock[0]);return 1
            capture._deauth_clients = send
            data = bytes.fromhex("d4c3b2a1") + struct.pack("<HHiiii",2,4,0,0,65535,105)
            def download(remote, local, event):
                if Path(local).name == "handshake-live.cap": checks.append(clock[0])
                Path(local).write_bytes(data)
            manager = NS(download_atomic=download)
            workspace = NS(local_dir=Path(directory),remote_dir="/tmp/owned")
            target = AccessPointInfo("02:11:22:33:44:55",channel=6)
            with patch("modules.capture_services.openwrt.handshake_capture.time.monotonic",side_effect=lambda:clock[0]), patch("modules.capture_services.openwrt.handshake_capture.time.sleep",side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
                capture.capture_passive(workspace,"wbmon1","offline",target,capture_seconds,
                    manager,active_deauth=True,defer_analysis=True)
            processes.stop.assert_called_once()
            return starts,ends,checks

    def test_short_command_does_not_consume_reconnect_window(self):
        starts,ends,checks=self.simulate(2)
        self.assertEqual(starts[:2],[0,22])
        self.assertTrue(all(start-end >= 20 for start,end in zip(starts[1:],ends)))
        self.assertTrue(any(ends[0] < t < starts[1] for t in checks))

    def test_command_longer_than_interval_does_not_trigger_back_to_back_send(self):
        starts,ends,checks=self.simulate(25)
        self.assertEqual(starts[:2],[0,45])
        self.assertTrue(all(start-end >= 20 for start,end in zip(starts[1:],ends)))
        self.assertTrue(any(ends[0] <= t < starts[1] for t in checks))

    def test_capture_deadline_does_not_wait_for_unused_window(self):
        starts,ends,checks=self.simulate(2,10)
        self.assertEqual(starts,[0])
