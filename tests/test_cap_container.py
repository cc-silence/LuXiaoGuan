"""Packet preservation and fail-closed CAP publication contracts."""
import contextlib
import io
from pathlib import Path
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt import cap_container as c
from modules.capture_services.openwrt.capture_products import cap_product_note
from modules.capture_services.openwrt.result_converter import finalize_result_file


def block(kind, body, endian="<"):
    body += bytes((-len(body)) % 4)
    size = len(body) + 12
    return struct.pack(endian+"II", kind, size) + body + struct.pack(endian+"I", size)


def ng(data=b"packet-data", *, endian="<", options=b"", timestamp=1234567, original=None):
    shb = block(0x0a0d0d0a, struct.pack(endian+"IHHq", 0x1a2b3c4d, 1, 0, -1), endian)
    idb = block(1, struct.pack(endian+"HHI", 127, 0, 65535)+options, endian)
    epb = block(6, struct.pack(endian+"IIIII", 0, timestamp>>32, timestamp & 0xffffffff, len(data), original or len(data))+data, endian)
    return shb+idb+epb


class CapContainerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.dest = self.root/"source.pcapng", self.root/"output.cap"

    def convert(self, blob, **kwargs):
        self.source.write_bytes(blob)
        c.convert_to_cap(self.source, self.dest, **kwargs)
        self.assertEqual(self.source.read_bytes(), blob)
        self.assertFalse(list(self.root.glob("*.part")))
        return list(c._pcap_records(io.BytesIO(self.dest.read_bytes()), lambda: None))

    def reject(self, blob, exception=ValueError, **kwargs):
        self.source.write_bytes(blob)
        with self.assertRaises(exception):
            c.convert_to_cap(self.source, self.dest, **kwargs)
        self.assertFalse(self.dest.exists())
        self.assertFalse(list(self.root.glob("*.part")))
        self.assertEqual(self.source.read_bytes(), blob)

    def test_endian_padding_original_length(self):
        for endian in ("<", ">"):
            with self.subTest(endian=endian):
                records = self.convert(ng(b"abcde", endian=endian, original=12))
                self.assertEqual(records, [(127, 1234567000, 12, b"abcde")])
                self.dest.unlink()

    def test_nanos_and_signed_offset(self):
        options = struct.pack("<HHB3x", 9, 1, 9)+struct.pack("<HHq", 14, 8, 2)
        records = self.convert(ng(timestamp=1234567891, options=options))
        self.assertEqual(records[0][1], 3234567891)

    def test_binary_resolution(self):
        options = struct.pack("<HHB3x", 9, 1, 0x80|10)
        records = self.convert(ng(timestamp=1024, options=options))
        self.assertEqual(records[0][1], 1000000000)

    def test_subnanosecond_precision_rejected(self):
        self.reject(ng(timestamp=1, options=struct.pack("<HHB3x", 9, 1, 10)))

    def test_truncated_and_bad_trailer(self):
        self.reject(ng()[:-1])
        self.reject(ng()[:-4]+bytes(4))
        self.reject(ng()+b"x")

    def test_bad_packet_lengths(self):
        self.reject(ng(b"abc", original=1))

    def test_multiple_interfaces_and_sections_rejected(self):
        extra_idb = block(1, struct.pack("<HHI", 127, 0, 65535))
        extra_packet = block(6, struct.pack("<IIIII", 1, 0, 9999999, 1, 1)+b"x")
        self.reject(ng()+extra_idb+extra_packet)
        self.reject(ng()+ng())

    def test_no_packets_and_missing_timestamps(self):
        self.reject(ng()[:48])
        self.reject(ng()[:48]+block(3, struct.pack("<I", 1)+b"x"))

    def test_limits_cancel_timeout(self):
        event = threading.Event(); event.set()
        self.reject(ng(), InterruptedError, cancel_event=event)
        self.reject(ng(), TimeoutError, timeout=0)
        self.reject(ng(), max_bytes=10)

    def test_existing_destination_never_replaced(self):
        self.source.write_bytes(ng()); self.dest.write_bytes(b"owned")
        with self.assertRaises(FileExistsError):
            c.convert_to_cap(self.source, self.dest)
        self.assertEqual(self.dest.read_bytes(), b"owned")

    def test_publish_failure_removes_temporary_only(self):
        with patch.object(c, "publish_new_file", side_effect=OSError("unavailable")):
            self.reject(ng(), OSError)

    def test_classic_micro_and_nano_inputs(self):
        for magic, scale in ((0xa1b2c3d4, 1000000), (0xa1b23c4d, 1000000000)):
            for endian in ("<", ">"):
                blob = struct.pack(endian+"IHHIIII", magic, 2, 4, 0, 0, 65535, 105)
                blob += struct.pack(endian+"IIII", 5, 123, 3, 4)+b"abc"
                records = self.convert(blob)
                self.assertEqual(records[0], (105, 5000000000+123*(1000000000//scale), 4, b"abc"))
                self.dest.unlink()

    def test_raw_naming_follows_magic_not_suffix(self):
        self.source.write_bytes(ng())
        result = finalize_result_file(self.source, self.root/"saved", "assisted", ".cap")
        self.assertEqual(result.suffix, ".pcapng")
        product, note = cap_product_note(result)
        self.assertIsNotNone(product)
        self.assertEqual(product.suffix, ".cap")
        self.assertTrue(result.exists())
        self.assertNotEqual(product.read_bytes()[:4], result.read_bytes()[:4])

    def test_interface_fcs_length_preserved_in_classic_header(self):
        for endian in ("<", ">"):
            options = struct.pack(endian+"HHB3x", 13, 1, 32)
            rows = self.convert(ng(b"frame-and-fcs", endian=endian, options=options))
            expected = 127 | (1 << 26) | (2 << 28)
            self.assertEqual(rows[0][0], expected)
            self.assertEqual(struct.unpack_from("<I", self.dest.read_bytes(), 20)[0], expected)
            self.dest.unlink()

    def test_packet_fcs_override_and_mixed_lengths(self):
        original = ng(b"abcdef", options=struct.pack("<HHB3x", 13, 1, 32))
        epb_start = 56
        fields = struct.pack("<IIIII",0,0,9999999,6,6)
        flags = struct.pack("<HHI",2,4,2 << 5)
        blob = original[:epb_start]+block(6,fields+b"abcdef"+bytes(2)+flags)
        rows = self.convert(blob)
        self.assertEqual(rows[0][0],127 | (1 << 26) | (1 << 28))
        self.dest.unlink()
        self.reject(original+block(6,fields+b"abcdef"+bytes(2)+flags))

    def test_error_flags_cannot_be_silently_lost(self):
        head=ng()[:48]
        for flags in (1 << 24,1 << 9,1 << 11):
            fields=struct.pack("<IIIII",0,0,100,4,4)
            self.reject(head+block(6,fields+b"data"+struct.pack("<HHI",2,4,flags)))

    def test_invalid_or_unrepresentable_fcs_options(self):
        self.reject(ng(options=struct.pack("<HHB3x",13,1,24)))
        self.reject(ng(options=struct.pack("<HHI",13,4,32)))
        self.reject(ng(options=struct.pack("<HHB3x",13,1,32)*2))

    def test_libpcap_packet_size_limit(self):
        head=ng()[:28]+block(1,struct.pack("<HHI",127,0,1048576))
        data=b"x"*262145
        self.reject(head+block(6,struct.pack("<IIIII",0,0,100,len(data),len(data))+data))
        blob=struct.pack("<IHHIIII",0xa1b23c4d,2,4,0,0,1048576,127)
        self.reject(blob+struct.pack("<IIII",0,100,len(data),len(data))+data)

    def test_bad_classic_link_extensions(self):
        data=b"data"
        for word in (127 | (1<<16),127 | (1<<27),127 | (2<<28)):
            blob=struct.pack("<IHHIIII",0xa1b23c4d,2,4,0,0,65535,word)
            self.reject(blob+struct.pack("<IIII",0,100,4,4)+data)

    def test_option_scan_honors_cancel_without_building_list(self):
        checks=Mock(side_effect=InterruptedError())
        with self.assertRaises(InterruptedError):
            list(c._options(struct.pack("<HH",1,0)*2000,"<",checks))
        checks.assert_called_once()

    def test_no_22000_or_hccapx_and_raw_retained(self):
        from modules.capture_services.backends.openwrt_backend import _convert_handshake_products, _maybe_drop_raw_cap
        self.source.write_bytes(ng())
        result = _convert_handshake_products(self.source, None, self.root, "target", "m1m2", {"generate_22000":True})
        self.assertEqual((result[0],result[2]), ("", ""))
        self.assertEqual(_maybe_drop_raw_cap(self.source, "unknown", "some-product", {"keep_raw_capture":False}), str(self.source))
        self.assertTrue(self.source.exists())
        self.assertTrue(list(self.root.glob("*.cap")))
        self.assertFalse(list(self.root.glob("*.22000")))
        self.assertFalse(list(self.root.glob("*.hccapx")))

    def test_native_cap_is_validated(self):
        source = self.root/"broken.cap"; source.write_bytes(b"bad")
        product, _ = cap_product_note(source)
        self.assertIsNone(product)
        self.assertEqual(source.read_bytes(), b"bad")

class CaptureDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.blob = ng()

    def fake_ssh(self, unstable=False):
        size = len(self.blob)
        sftp = Mock()
        sftp.stat.side_effect = [SimpleNamespace(st_size=size, st_mtime=1), SimpleNamespace(st_size=size+(1 if unstable else 0), st_mtime=1)]
        sftp.get.side_effect = lambda remote, local: Path(local).write_bytes(self.blob)
        ssh = Mock(); ssh.transfer.side_effect = lambda *a,**k: contextlib.nullcontext()
        ssh.open_sftp.return_value = sftp
        return ssh,sftp

    def test_foreground_download_converts_only_stable_snapshot(self):
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
        for unstable in (False, True):
            with self.subTest(unstable=unstable), tempfile.TemporaryDirectory(dir=self.root) as folder:
                ssh,sftp = self.fake_ssh(unstable)
                owner = SimpleNamespace(remote_cap_file="/root/cap/test.pcapng", output_dir=Path(folder), ssh_client=ssh,
                    stats=SimpleNamespace(file_size_bytes=0), service=SimpleNamespace(radioStreamOutput=Mock(),messageGenerated=Mock(),should_generate_cap=lambda: True),radio_name="radio1")
                AutoCaptureRadioSession._pull_capture_result(owner)
                self.assertEqual(len(list(Path(folder).glob("*.pcapng"))),1)
                self.assertEqual(len(list(Path(folder).glob("*.cap"))), 0 if unstable else 1)
                sftp.close.assert_called_once()
                self.assertFalse(list(Path(folder).glob("*.part")))

    def test_offline_download_and_conversion_share_stability_policy(self):
        from modules.capture_services.openwrt.auto_capture_boot_manager import AutoCaptureBootManager
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
        from types import MethodType
        for unstable in (False, True):
            with self.subTest(unstable=unstable), tempfile.TemporaryDirectory(dir=self.root) as folder:
                ssh,sftp = self.fake_ssh(unstable)
                runner = Mock(); runner.run.return_value = SimpleNamespace(cancelled=False,exit_code=0,stdout="test.pcapng")
                boot = SimpleNamespace(ssh=ssh, runner=runner)
                boot.pull_offline_artifacts = MethodType(AutoCaptureBootManager.pull_offline_artifacts,boot)
                service = SimpleNamespace(boot_manager=boot, messageGenerated=Mock(), should_generate_cap=lambda: True)
                result = AutoCaptureService.pull_offline_artifacts(service,folder)
                self.assertEqual(len(result),1 if unstable else 2)
                self.assertEqual(len(list(Path(folder).glob("*.cap"))),0 if unstable else 1)
                sftp.close.assert_called_once()

if __name__ == "__main__":
    unittest.main()
