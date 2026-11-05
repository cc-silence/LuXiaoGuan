"""Passive WPS evidence, incremental transfer and scan/UI integration contracts."""
import io
import struct
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.models import AccessPointInfo
from modules.capture_services.openwrt.remote_resources import CaptureResourceError
from modules.capture_services.openwrt.wifi_scanner import OpenWrtWifiScanner
from modules.capture_services.openwrt.wps_scan import WpsScanReader, parse_wps_frame, wps_status_text

MAC = "02:11:22:33:44:55"
ADDRESS = bytes.fromhex(MAC.replace(":", ""))


def attr(tag, value):
    return struct.pack(">HH", tag, len(value)) + value


def beacon(locked=False, *, subtype=8, wps=True, split=False):
    header = bytes([subtype << 4, 0]) + bytes(2) + b"\xff" * 6 + ADDRESS * 2 + bytes(2)
    ies = b"\x00\x07offline"
    if wps:
        data = attr(0x104a, b"\x10") + attr(0x1044, b"\x02")
        if locked is not None:
            data += attr(0x1057, bytes([locked]))
        parts = (data[:7], data[7:]) if split else (data,)
        for part in parts:
            vendor = b"\x00\x50\xf2\x04" + part
            ies += bytes([221, len(vendor)]) + vendor
    return header + bytes(12) + ies


def pcap(*frames, endian="<", dlt=105):
    magic = b"\xd4\xc3\xb2\xa1" if endian == "<" else b"\xa1\xb2\xc3\xd4"
    header = magic + struct.pack(endian + "HHIIII", 2, 4, 0, 0, 65535, dlt)
    return header + b"".join(struct.pack(endian + "IIII", 0, 0, len(frame), len(frame)) + frame for frame in frames)


class PassiveWpsTests(unittest.TestCase):
    def test_official_aircrack_wps20_fixture(self):
        import hashlib
        data = (Path(__file__).parent / "fixtures" / "wps2.0.pcap").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), "54e312b9745e5d36614f0abd87015ff5b535abb26a75b8530c5e971e6ff38633")
        reader = WpsScanReader()
        for offset in range(0, len(data), 17):
            reader.feed(data[offset:offset+17])
        self.assertEqual(reader.points, {"00:C0:CA:78:B1:37": False})
        self.assertFalse(reader.invalid)
        self.assertFalse(reader.pending)

    def test_only_ap_wps_evidence_enables_support(self):
        point = AccessPointInfo(MAC)
        reader = WpsScanReader()
        reader.feed(pcap(beacon(wps=False)))
        self.assertIsNone(reader.apply([point])[0].wps_supported)
        reader.feed(pcap(beacon())[24:])
        result = reader.apply([point])[0]
        self.assertTrue(result.wps_supported)
        self.assertFalse(result.wps_locked)
        self.assertEqual(wps_status_text(point), "\u672a\u68c0\u6d4b\u5230")
        self.assertEqual(wps_status_text(result), "\u652f\u6301")

    def test_complete_hostap_wps_omission_clears_lock_but_absent_wps_does_not(self):
        reader = WpsScanReader()
        reader.feed(pcap(beacon(True, subtype=5), beacon(wps=False)))
        self.assertTrue(reader.apply([AccessPointInfo(MAC)])[0].wps_locked)
        reader.feed(pcap(beacon(None))[24:])
        self.assertFalse(reader.apply([AccessPointInfo(MAC)])[0].wps_locked)
        reader.feed(pcap(beacon(True))[24:])
        reader.feed(pcap(beacon(False, subtype=5))[24:])
        self.assertFalse(reader.apply([AccessPointInfo(MAC)])[0].wps_locked)

    def test_incomplete_wps_fields_do_not_clear_known_lock(self):
        reader = WpsScanReader(); reader.feed(pcap(beacon(True)))
        frame = beacon(None)
        frame = frame[:-5]
        frame = frame[:46] + bytes([9]) + frame[47:]
        reader.feed(pcap(frame)[24:])
        self.assertTrue(reader.points[MAC])

    def test_incomplete_required_fields_cannot_explicitly_unlock_or_claim_support(self):
        for fields in (attr(0x104a,b"\x10"), attr(0x1044,b"\x02")):
            payload = b"\x00\x50\xf2\x04" + fields + attr(0x1057,b"\x00")
            frame = beacon(wps=False) + bytes([221,len(payload)]) + payload
            reader = WpsScanReader(); reader.feed(pcap(beacon(True)))
            reader.feed(pcap(frame)[24:])
            self.assertTrue(reader.points[MAC])
            fresh = WpsScanReader(); fresh.feed(pcap(frame))
            self.assertFalse(fresh.points)

    def test_version_state_and_lock_attribute_byte_domains(self):
        for tag, allowed in ((0x104a,{0x10}), (0x1044,{1,2}), (0x1057,{0,1})):
            for value in range(256):
                fields={0x104a:b"\x10",0x1044:b"\x02",0x1057:b"\x00"}
                fields[tag]=bytes([value])
                payload=b"\x00\x50\xf2\x04"+b"".join(attr(key,item) for key,item in fields.items())
                frame=beacon(wps=False)+bytes([221,len(payload)])+payload
                result=parse_wps_frame(frame,105)
                with self.subTest(tag=tag,value=value):
                    self.assertEqual(result is not None,value in allowed)

    def test_fragmented_vendor_ies_and_bytewise_transfer(self):
        data = pcap(beacon(True, split=True), endian=">")
        reader = WpsScanReader()
        for byte in data:
            reader.feed(bytes([byte]))
        result = reader.apply([AccessPointInfo(MAC)])[0]
        self.assertEqual(wps_status_text(result), "\u5df2\u9501\u5b9a")
        self.assertEqual(reader.pending, b"")

    def test_malformed_ie_attribute_and_wrong_transmitter_never_claim_support(self):
        valid = beacon(True)
        bad = [valid[:-1], valid + b"\xdd\xff", valid[:10] + bytes(6) + valid[16:],
               beacon(True, subtype=4), valid[:-1] + b"\x02"]
        for frame in bad:
            with self.subTest(frame=frame):
                self.assertIsNone(parse_wps_frame(frame, 105))
        reader = WpsScanReader()
        reader.feed(pcap(valid)[:-1])
        self.assertFalse(reader.points)
        reader.feed(pcap(valid)[-1:])
        self.assertTrue(reader.points[MAC])

    def test_radiotap_fcs_and_bad_fcs(self):
        header = b"\x00\x00\x09\x00\x02\x00\x00\x00"
        self.assertEqual(parse_wps_frame(header + b"\x10" + beacon(True) + bytes(4), 127)[:2], (MAC, True))
        self.assertIsNone(parse_wps_frame(header + b"\x50" + beacon(True) + bytes(4), 127))
        self.assertIsNone(parse_wps_frame(b"\x00\x00\xff\xff" + bytes(4) + beacon(), 127))
        extended = b"\x00\x00\x19\x00\x03\x00\x00\x80" + bytes(4) + bytes(4) + bytes(8) + b"\x10"
        self.assertTrue(parse_wps_frame(extended + beacon(True) + bytes(4), 127)[1])

    def test_pcap_format_matrix_preserves_wps_evidence(self):
        magics=((b"\xd4\xc3\xb2\xa1","<"),(b"\xa1\xb2\xc3\xd4",">"),
                (b"\x4d\x3c\xb2\xa1","<"),(b"\xa1\xb2\x3c\x4d",">"))
        body=beacon(True)
        variants=((105,body),(105|(1<<26)|(2<<28),body+bytes.fromhex("deadbeef")),
                  (127,b"\x00\x00\x08\x00"+bytes(4)+body),
                  (127,b"\x00\x00\x09\x00\x02\x00\x00\x00\x10"+body+bytes.fromhex("deadbeef")))
        for magic,endian in magics:
            for network,frame in variants:
                with self.subTest(magic=magic,network=network):
                    data=magic+struct.pack(endian+"HHIIII",2,4,0,0,65535,network)
                    data+=struct.pack(endian+"IIII",0,0,len(frame),len(frame))+frame
                    reader=WpsScanReader()
                    for index in range(0,len(data),13):reader.feed(data[index:index+13])
                    self.assertEqual(reader.points,{MAC:True})
                    self.assertFalse(reader.pending)
                    self.assertFalse(reader.invalid)

    def test_foreign_linktype_and_oversized_record_are_bounded(self):
        for data in (pcap(beacon(), dlt=1), pcap() + struct.pack("<IIII", 0, 0, 0xffffffff, 0xffffffff)):
            reader = WpsScanReader()
            reader.feed(data)
            self.assertTrue(reader.invalid)
            self.assertFalse(reader.points)
            self.assertFalse(reader.pending)
        reader = WpsScanReader()
        reader.feed(pcap()[:10])
        self.assertFalse(reader.invalid)

    def test_truncated_record_never_implies_complete_wps_evidence(self):
        data = bytearray(pcap(beacon()))
        struct.pack_into("<I", data, 36, len(beacon()) + 100)
        reader = WpsScanReader(); reader.feed(data)
        self.assertFalse(reader.points)

    def test_snaplen_and_explicit_classic_pcap_fcs(self):
        data = bytearray(pcap(beacon(True) + bytes.fromhex("deadbeef")))
        struct.pack_into("<I", data, 20, 105 | (1 << 26) | (2 << 28))
        reader = WpsScanReader(); reader.feed(data)
        self.assertEqual(reader.points, {MAC: True})
        invalid = bytearray(pcap(beacon()))
        struct.pack_into("<I", invalid, 16, 20)
        reader = WpsScanReader(); reader.feed(invalid)
        self.assertFalse(reader.points)
        self.assertTrue(reader.invalid)

    def ssh(self, data):
        sftp = Mock()
        sftp.stat.side_effect = lambda path: NS(st_size=len(data[0]))
        sftp.open.side_effect = lambda *args: io.BytesIO(data[0])
        ssh = Mock()
        ssh.transfer.side_effect = lambda event, **options: nullcontext()
        ssh.open_sftp.return_value = sftp
        return ssh, sftp

    def test_missing_size_does_not_abort_base_scan(self):
        ssh, sftp = self.ssh([b""])
        sftp.stat.side_effect = None
        for size in (None, -1, "123"):
            sftp.stat.return_value = NS(st_size=size)
            reader = WpsScanReader()
            reader.sync(ssh, "/tmp/owned/scan-01.cap")
            self.assertTrue(reader.failure_reported)
        sftp.open.assert_not_called()

    def test_transfer_wait_uses_metadata_budget_and_expired_lock_does_no_io(self):
        ssh, sftp = self.ssh([pcap(beacon())])
        with patch("modules.capture_services.openwrt.wps_scan.time.monotonic", side_effect=[0, 0, 0, 6]):
            WpsScanReader().sync(ssh, "/tmp/owned/scan-01.cap")
        self.assertLessEqual(ssh.transfer.call_args.kwargs["timeout"], 5)
        ssh.open_sftp.assert_not_called()

    def test_timeout_discards_broken_sftp_but_keeps_owned_ssh(self):
        ssh, sftp = self.ssh([pcap(beacon())])
        sftp.open.side_effect = TimeoutError("offline")
        reader = WpsScanReader()
        reader.sync(ssh, "/tmp/owned/scan-01.cap")
        ssh.drop_sftp.assert_called_once()
        ssh.close.assert_not_called()
        self.assertTrue(reader.failure_reported)

    def test_remaining_budget_applies_to_stat_and_open(self):
        ssh, sftp = self.ssh([pcap(beacon())])
        clock = [0.0]
        def opened(**options):
            clock[0] = 4.0
            return sftp
        def stat(path):
            self.assertLessEqual(sftp.get_channel().settimeout.call_args.args[0], 1.0)
            clock[0] = 6.0
            return NS(st_size=100)
        ssh.open_sftp.side_effect = opened
        sftp.stat.side_effect = stat
        with patch("modules.capture_services.openwrt.wps_scan.time.monotonic", side_effect=lambda: clock[0]):
            reader = WpsScanReader(); reader.sync(ssh, "/tmp/owned/scan-01.cap")
        sftp.open.assert_not_called()
        self.assertTrue(reader.failure_reported)

    def test_metadata_merge_preserves_selection_and_other_capture_fields(self):
        point = AccessPointInfo(MAC, ssid="offline", power=-45, channel=6, selected=True)
        reader = WpsScanReader(); reader.feed(pcap(beacon(True)))
        updated = reader.apply([point])[0]
        self.assertTrue(updated.selected)
        self.assertEqual((updated.ssid, updated.power, updated.channel, updated.capture_state),
                         (point.ssid, point.power, point.channel, point.capture_state))
        self.assertIsNone(point.wps_supported)
        self.assertTrue(updated.wps_locked)

    def test_unknown_and_locked_targets_never_launch_a_wps_process(self):
        from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture
        runner, processes = Mock(), Mock()
        capture = OpenWrtWpsCapture(runner, processes)
        for point in (AccessPointInfo(MAC, channel=6),
                      AccessPointInfo(MAC, channel=6, wps_supported=True, wps_locked=True)):
            result = capture._run_single(None, "ownedmon", "session", point, "normal", {}, None, None, 0, 1, None, None)
            self.assertTrue(result.note)
        runner.run.assert_not_called()
        processes.start.assert_not_called()

    def test_protocol_errors_degrade_metadata_and_drop_only_sftp(self):
        class ProtocolError(Exception):
            pass
        ssh, sftp = self.ssh([pcap(beacon())])
        sftp.open.side_effect = ProtocolError("private source detail")
        with patch("modules.capture_services.openwrt.wps_scan._metadata_transport_errors", return_value=(OSError, ProtocolError)):
            with self.assertLogs("routermanager.capture", level="WARNING") as logs:
                reader = WpsScanReader(); reader.sync(ssh, "/tmp/owned/scan-01.cap")
        self.assertTrue(reader.failure_reported)
        ssh.drop_sftp.assert_called_once()
        ssh.close.assert_not_called()
        self.assertNotIn("private source detail", str(logs.output))

    def test_cancellation_during_empty_or_missing_stat_is_not_lost(self):
        for missing in (False, True):
            ssh, sftp = self.ssh([b""])
            event = Event()
            def cancelled_stat(path):
                event.set()
                if missing:
                    raise FileNotFoundError()
                return NS(st_size=0)
            sftp.stat.side_effect = cancelled_stat
            with self.assertRaises(InterruptedError):
                WpsScanReader().sync(ssh, "/tmp/owned/scan-01.cap", event, final=True)

    def test_interleaved_ap_state_transitions_survive_random_chunk_boundaries(self):
        import random
        rng = random.Random(2026110403)
        records, expected = [], {}
        for number in range(600):
            identity = rng.randrange(12)
            mac = bytes([2,0,0,0,0,identity])
            bssid = ":".join(f"{value:02X}" for value in mac)
            kind = rng.randrange(4)
            lock = (False,True,None,False)[kind]
            frame = bytearray(beacon(lock,subtype=5 if number%2 else 8,wps=kind!=3))
            frame[10:16] = mac; frame[16:22] = mac
            records.append(pcap(frame)[24:])
            if kind!=3: expected[bssid] = bool(lock)
        data = pcap() + b"".join(records)
        reader = WpsScanReader(); cursor = 0
        while cursor < len(data):
            size = rng.randrange(1,97)
            reader.feed(data[cursor:cursor+size]); cursor += size
        self.assertEqual(reader.points,expected)
        self.assertFalse(reader.pending)
        self.assertFalse(reader.invalid)

    def test_new_bytes_only_and_incomplete_tail_retried(self):
        full = pcap(beacon(True))
        data = [full[:-1]]
        ssh, sftp = self.ssh(data)
        reader = WpsScanReader()
        reader.sync(ssh, "/tmp/owned/scan-01.cap")
        self.assertEqual(reader.offset, len(full)-1)
        self.assertFalse(reader.points)
        data[0] = full
        reader.sync(ssh, "/tmp/owned/scan-01.cap", final=True)
        self.assertEqual(reader.offset, len(full))
        self.assertTrue(reader.points[MAC])
        reader.sync(ssh, "/tmp/owned/scan-01.cap")
        self.assertEqual(sftp.open.call_count, 2)

    def test_missing_or_failed_metadata_preserves_scan_and_cancellation_propagates(self):
        ssh, sftp = self.ssh([b""])
        reader = WpsScanReader()
        sftp.stat.side_effect = FileNotFoundError()
        reader.sync(ssh, "/tmp/owned/scan-01.cap")
        sftp.stat.side_effect = OSError("offline")
        reader.sync(ssh, "/tmp/owned/scan-01.cap")
        self.assertTrue(reader.failure_reported)
        event = Event(); event.set()
        with self.assertRaises(InterruptedError):
            reader.sync(ssh, "/tmp/owned/scan-01.cap", event)

    def test_oversized_capture_stops_instead_of_filling_memory(self):
        ssh, sftp = self.ssh([b""])
        sftp.stat.side_effect = None
        sftp.stat.return_value = NS(st_size=33*1024*1024)
        with self.assertRaises(CaptureResourceError):
            WpsScanReader().sync(ssh, "/tmp/owned/scan-01.cap")
        sftp.open.assert_not_called()

    def test_real_scanner_merges_final_evidence_and_stops_owned_pid(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            workspace = NS(local_dir=root, remote_dir="/tmp/owned")
            runner, processes, manager = Mock(), Mock(), Mock()
            runner.run.return_value = NS(exit_code=0)
            manager.ssh_client = self.ssh([pcap(beacon(True))])[0]
            point = AccessPointInfo(MAC)
            scanner = OpenWrtWifiScanner(runner, processes)
            with patch("modules.capture_services.openwrt.wifi_scanner.read_monitor_status", return_value="ready"), \
                 patch("modules.capture_services.openwrt.wifi_scanner.time.monotonic", side_effect=[0, 6] + [6]*100), \
                 patch.object(scanner, "parse_csv", return_value=[point]):
                result = scanner.scan(workspace, "ownedmon", "session", "2.4G", 5, 1, manager)
            self.assertTrue(result.access_points[0].wps_supported)
            self.assertTrue(result.access_points[0].wps_locked)
            command = processes.start.call_args.args[1]
            self.assertIn("--output-format csv,pcap", command)
            self.assertIn("--beacons", command)
            self.assertNotIn("active", command)
            processes.stop.assert_called_once_with(processes.start.return_value)

    def test_metadata_abort_stops_owned_process_and_never_publishes_completion(self):
        from modules.capture_services.openwrt.models import CaptureCleanupError
        for primary in (InterruptedError("controlled metadata cancellation"), CaptureResourceError("controlled metadata resource limit")):
            for stop_fails in (False,True):
                with self.subTest(primary=type(primary).__name__,stop_fails=stop_fails), TemporaryDirectory() as folder:
                    workspace=NS(local_dir=Path(folder),remote_dir="/tmp/owned")
                    runner,processes,manager,callback=Mock(),Mock(),Mock(),Mock()
                    if stop_fails:processes.stop.side_effect=RuntimeError("controlled stop failure")
                    scanner=OpenWrtWifiScanner(runner,processes)
                    expected=CaptureCleanupError if stop_fails else type(primary)
                    with patch("modules.capture_services.openwrt.wifi_scanner.read_monitor_status",return_value="ready"), \
                         patch("modules.capture_services.openwrt.wifi_scanner.time.monotonic",side_effect=[0,0,1]), \
                         patch.object(scanner,"_sync_current_results",return_value=()), \
                         patch("modules.capture_services.openwrt.wifi_scanner.WpsScanReader.sync",side_effect=primary):
                        with self.assertRaises(expected) as raised:
                            scanner.scan(workspace,"ownedmon","session","2.4G",5,1,manager,None,callback)
                    self.assertIn(str(primary),str(raised.exception))
                    if stop_fails:self.assertIn("controlled stop failure",str(raised.exception))
                    processes.stop.assert_called_once_with(processes.start.return_value)
                    manager.download_atomic.assert_not_called()
                    callback.assert_not_called()

    def test_cancelling_real_scanner_stops_only_its_process(self):
        with TemporaryDirectory() as folder:
            workspace = NS(local_dir=Path(folder), remote_dir="/tmp/owned")
            runner, processes, manager = Mock(), Mock(), Mock()
            event = Event(); event.set()
            scanner = OpenWrtWifiScanner(runner, processes)
            with patch("modules.capture_services.openwrt.wifi_scanner.read_monitor_status", return_value="ready"):
                with self.assertRaises(RuntimeError):
                    scanner.scan(workspace, "ownedmon", "session", "2.4G", 5, 1, manager, event)
            processes.stop.assert_called_once_with(processes.start.return_value)
            manager.download_atomic.assert_not_called()

    def test_table_and_export_unlock_updates_existing_row(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
        from modules.capture_services.signal_list_export import signal_rows
        table = AccessPointTable()
        reader = WpsScanReader(); reader.feed(pcap(beacon(True)))
        point = AccessPointInfo(MAC, ssid="offline", selected=True)
        locked = reader.apply([point])[0]
        table.set_access_points([locked])
        item = table.item(0,6)
        self.assertEqual(item.text(), "\u5df2\u9501\u5b9a")
        reader.feed(pcap(beacon(None))[24:])
        unlocked = reader.apply([point])[0]
        table.set_access_points([unlocked])
        self.assertIs(table.item(0,6),item)
        self.assertEqual(item.text(), "\u652f\u6301")
        self.assertEqual(tuple(signal_rows([unlocked]))[0][5], item.text())
        self.assertIn(MAC,table.selected_bssids())
        table.deleteLater()

    def test_table_and_export_use_same_unknown_supported_locked_labels(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
        from modules.capture_services.signal_list_export import signal_rows
        table = AccessPointTable()
        points = [AccessPointInfo(MAC), AccessPointInfo(MAC, wps_supported=True),
                  AccessPointInfo(MAC, wps_supported=True, wps_locked=True)]
        for point in points:
            self.assertEqual(table._wps_text(point), tuple(signal_rows([point]))[0][5])
            table.set_access_points([point])
            self.assertEqual(table.item(0, 6).text(), wps_status_text(point))
        table.deleteLater()


if __name__ == "__main__":
    unittest.main()
