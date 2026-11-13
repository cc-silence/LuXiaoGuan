"""Client observation must never treat missing or malformed data as empty."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import struct
import unittest

from modules.capture_services.openwrt.client_observation import parse_snapshot, ClientObservation
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.handshake_capture import OpenWrtHandshakeCapture
from modules.config_services.capture_settings_manager import CaptureSettingsManager

AP = "02:11:22:33:44:55"
C1 = "02:11:22:33:44:66"
C2 = "02:11:22:33:44:77"
HEADER = "BSSID, First time seen, Last time seen, channel, Speed, Privacy, Cipher, Authentication, Power, # beacons, # IV, LAN IP, ID-length, ESSID, Key\n"
AP_ROW = AP + ",2026-01-01 00:00:00,2026-01-01 00:00:00,6,54,WPA2,CCMP,PSK,-50,1,0,0.0.0.0,4,Test,\n"
STATION = "Station MAC, First time seen, Last time seen, Power, # packets, BSSID, Probed ESSIDs\n"
def csv_text(clients=()):
    return HEADER + AP_ROW + "\n" + STATION + "".join(
        mac + ",2026-01-01 00:00:00," + seen + ",-50,1," + AP + ",\n" for mac, seen in clients)


class ClientSnapshotTests(unittest.TestCase):
    def test_empty_valid_and_unknown_records_are_distinct(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "scan.csv"
            self.assertIsNone(parse_snapshot(path))
            path.write_text(HEADER + AP_ROW, encoding="utf-8")
            self.assertIsNone(parse_snapshot(path))
            path.write_text(csv_text(), encoding="utf-8")
            snapshot = parse_snapshot(path)
            self.assertEqual(ClientObservation().update(snapshot, (AP,), 0)[AP], ())
            self.assertIsNone(ClientObservation().update(snapshot, (C1,), 0)[C1])
            path.write_text(csv_text() + "broken,line\n", encoding="utf-8")
            self.assertIsNone(parse_snapshot(path))

    def test_new_clients_are_added_and_old_observations_expire(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "scan.csv"
            tracker = ClientObservation()
            path.write_text(csv_text([(C1,"2026-01-01 00:00:01")]), encoding="utf-8")
            self.assertEqual(tracker.update(parse_snapshot(path), (AP,), 0)[AP], (C1,))
            path.write_text(csv_text([(C1,"2026-01-01 00:00:01"),(C2,"2026-01-01 00:00:05")]), encoding="utf-8")
            self.assertEqual(set(tracker.update(parse_snapshot(path), (AP,), 5)[AP]), {C1,C2})
            path.write_text(csv_text([(C1,"2026-01-01 00:00:01"),(C2,"2026-01-01 00:00:05")]).replace("00:00:00,6,54", "00:00:21,6,54"), encoding="utf-8")
            self.assertEqual(tracker.update(parse_snapshot(path), (AP,), 21)[AP], (C2,))
            path.write_text(csv_text([(C1,"2026-01-01 00:00:01"),(C2,"2026-01-01 00:00:05")]).replace("00:00:00,6,54", "00:00:26,6,54"), encoding="utf-8")
            self.assertEqual(tracker.update(parse_snapshot(path), (AP,), 26)[AP], ())

    def test_seconds_default_and_bounds(self):
        with TemporaryDirectory() as folder:
            manager = CaptureSettingsManager(Path(folder)/"settings.json")
            self.assertEqual(manager.normalize({})["openwrt"]["no_client_observation_seconds"], 30)
            self.assertEqual(manager.normalize({"openwrt":{"no_client_observation_seconds": 120}})["openwrt"]["no_client_observation_seconds"], 90)
            self.assertEqual(manager.normalize({"openwrt":{"no_client_observation_seconds": -1}})["openwrt"]["no_client_observation_seconds"], 20)

    def run_capture(self, contents, *, seconds=40, observation_seconds=20):
        with TemporaryDirectory() as folder:
            workspace=NS(local_dir=Path(folder),remote_dir="/tmp/owned")
            runner=Mock();runner.run.return_value=RemoteCommandResult("offline",0)
            processes=Mock();processes.is_running.return_value=True
            capture=OpenWrtHandshakeCapture(runner,processes);capture._verify_tuned_channel=Mock()
            manager=Mock();clock=[0.0];reads=[]
            pcap=bytes.fromhex("d4c3b2a1")+struct.pack("<HHiiii",2,4,0,0,65535,105)
            def download(remote,local,event):
                if str(remote).endswith(".csv"):
                    reads.append(clock[0]); value=contents(clock[0])
                    if value is None: raise FileNotFoundError()
                    Path(local).write_text(value,encoding="utf-8")
                else: Path(local).write_bytes(pcap)
            manager.download_atomic.side_effect=download
            target=NS(bssid=AP,ssid="test",channel=6,band="2.4G",clients=())
            with patch("modules.capture_services.openwrt.handshake_capture.time.monotonic",side_effect=lambda:clock[0]), \
                 patch("modules.capture_services.openwrt.handshake_capture.time.sleep",side_effect=lambda value:clock.__setitem__(0,clock[0]+value)):
                result=capture.capture_passive(workspace,"wbmon1","session",target,seconds,manager,
                    defer_analysis=True,no_client_observation_seconds=observation_seconds)
            return clock[0],reads,result

    def test_no_clients_stops_after_twenty_seconds(self):
        elapsed,reads,result=self.run_capture(
            lambda now:csv_text().replace("00:00:00,6,54", f"00:00:{int(now):02d},6,54"))
        self.assertEqual(elapsed,20)
        self.assertEqual(reads,[0,5,10,15,20])
        self.assertEqual(result.no_client_targets,(AP,))

    def test_missing_record_does_not_count_as_no_clients(self):
        elapsed,reads,result=self.run_capture(lambda _:None, seconds=10)
        self.assertEqual(elapsed,10)
        self.assertEqual(reads,[0,5])
        self.assertEqual(result.no_client_targets,())

    def test_single_valid_read_does_not_trigger_early_skip(self):
        elapsed, reads, result = self.run_capture(
            lambda now: csv_text() if now == 20 else None, seconds=30)
        self.assertEqual(elapsed, 30)
        self.assertEqual(result.no_client_targets, ())

    def test_short_target_timeout_is_never_extended(self):
        elapsed, reads, result = self.run_capture(lambda _: csv_text(), seconds=10)
        self.assertEqual(elapsed, 10)
        self.assertEqual(result.no_client_targets, ())

    def test_initial_client_keeps_capture_until_timeout(self):
        elapsed,reads,result=self.run_capture(lambda _:csv_text([(C1,"2026-01-01 00:00:01")]), seconds=10)
        self.assertEqual(elapsed,10)
        self.assertEqual(result.no_client_targets,())


if __name__ == "__main__": unittest.main()
