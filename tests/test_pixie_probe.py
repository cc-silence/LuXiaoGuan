"""Offline capture probe: parse WPS M1/M2/M3 from synthetic pcap/pcapng."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.pixie_probe import collect, inputs, missing_fields, summary

AP = bytes.fromhex("aabbccddeeff")
STA = bytes.fromhex("112233445566")
PKE = bytes(range(192))
PKR = bytes((value + 1) % 256 for value in range(192))
E_NONCE = bytes(range(16))
R_NONCE = bytes(range(16, 32))
E_HASH1 = bytes(range(32, 64))
E_HASH2 = bytes(range(64, 96))


def attribute(kind, value):
    return struct.pack("!HH", kind, len(value)) + value


def wsc_frame(msg_type, attributes, sender=AP, receiver=STA):
    wsc = attribute(0x1022, bytes([msg_type])) + attributes
    expanded = b"\x00\x37\x2a" + b"\x00\x00\x00\x01" + wsc
    eap = bytes([1, 0x10]) + struct.pack("!H", 5 + len(expanded)) + b"\xfe" + expanded
    eapol = bytes([1, 0]) + struct.pack("!H", len(eap)) + eap
    llc = b"\xaa\xaa\x03\x00\x00\x00\x88\x8e" + eapol
    header = (bytes([0x08, 0x01]) + b"\x00\x00" + receiver + sender + sender
              + b"\x00\x00")
    radiotap = b"\x00\x00\x08\x00" + b"\x00\x00\x00\x00"
    return radiotap + header + llc


def frame_m1():
    return wsc_frame(0x04, attribute(0x1032, PKE)
                     + attribute(0x101a, E_NONCE)
                     + attribute(0x104a, b"\x10"))


def frame_m2():
    return wsc_frame(0x05, attribute(0x1032, PKR)
                     + attribute(0x1039, R_NONCE), sender=STA, receiver=AP)


def frame_m3():
    return wsc_frame(0x07, attribute(0x1014, E_HASH1)
                     + attribute(0x1015, E_HASH2))


def pcap_bytes(packets, link_type=127):
    data = struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, link_type)
    for packet in packets:
        data += struct.pack("<IIII", 0, 0, len(packet), len(packet)) + packet
    return data


def pcapng_block(block_type, payload):
    padding = (-len(payload)) % 4
    total = 12 + len(payload) + padding
    return (struct.pack("<II", block_type, total) + payload + b"\x00" * padding
            + struct.pack("<I", total))


def pcapng_bytes(packets, link_type=127):
    data = pcapng_block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    data += pcapng_block(1, struct.pack("<HHI", link_type, 0, 65535))
    for packet in packets:
        data += pcapng_block(6, struct.pack("<IIIII", 0, 0, 0, len(packet),
                                            len(packet)) + packet)
    return data


class PixieProbeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def write(self, name, data):
        path = Path(self.folder.name) / name
        path.write_bytes(data)
        return path

    def test_extracts_every_pixiewps_field(self):
        path = self.write("full.cap", pcap_bytes([frame_m1(), frame_m2(), frame_m3()]))
        records = summary(path)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["bssid"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(record["client"], "11:22:33:44:55:66")
        self.assertEqual(record["missing"], [])
        self.assertEqual(record["frames"], {"M1": 1, "M2": 1, "M3": 1})
        self.assertEqual(record["parameters"]["pke"], PKE.hex())
        self.assertEqual(record["parameters"]["pkr"], PKR.hex())
        arguments = record["pixiewps"]
        self.assertEqual(arguments[arguments.index("-e") + 1], PKE.hex())
        self.assertEqual(arguments[arguments.index("-r") + 1], PKR.hex())
        self.assertEqual(arguments[arguments.index("-s") + 1], E_HASH1.hex())
        self.assertEqual(arguments[arguments.index("-z") + 1], E_HASH2.hex())
        self.assertEqual(arguments[arguments.index("-b") + 1], "aabbccddeeff")

    def test_missing_m3_is_reported_not_papered_over(self):
        path = self.write("short.cap", pcap_bytes([frame_m1(), frame_m2()]))
        record = summary(path)[0]
        self.assertEqual(len(record["missing"]), 2)
        self.assertIn("E-Hash1", record["missing"][0])

    def test_pcapng_capture_is_supported(self):
        path = self.write("full.pcapng",
                          pcapng_bytes([frame_m1(), frame_m2(), frame_m3()]))
        records = summary(path)
        self.assertEqual(records[0]["missing"], [])

    def test_protected_data_frames_are_skipped(self):
        packet = bytearray(frame_m1())
        packet[9] |= 0x40  # protected bit inside the 802.11 frame control
        path = self.write("encrypted.cap", pcap_bytes([bytes(packet)]))
        self.assertEqual(summary(path), [])

    def test_bssid_filter_keeps_only_that_access_point(self):
        other = bytes.fromhex("020000000001")
        packets = [frame_m1(), frame_m2(), frame_m3(),
                   wsc_frame(0x04, attribute(0x1032, PKE), sender=other,
                             receiver=STA)]
        data = pcap_bytes(packets)
        path = self.write("two.cap", data)
        self.assertEqual(len(collect(path)), 2)
        self.assertEqual(len(summary(path, "aa:bb:cc:dd:ee:ff")), 1)

    def test_missing_public_keys_are_named(self):
        path = self.write("m3-only.cap", pcap_bytes([frame_m3()]))
        record = summary(path)[0]
        values = inputs(collect(path)[0])
        self.assertTrue(missing_fields(values))
        self.assertEqual(record["missing"],
                         ["PKE（M1 的公钥）", "PKR（M2 的公钥）"])
        self.assertEqual(record["parameters"]["pke"], "")


    def test_pixiewps_output_is_read(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from scripts import pixie_probe

        finished = SimpleNamespace(
            returncode=0,
            stdout=b"\n [+] WPS pin:  12345670\n [+] WPA-PSK:  secretpass\n",
            stderr=b"")
        with patch.object(pixie_probe.subprocess, "run", return_value=finished):
            code, text = pixie_probe.run_pixiewps("pixiewps", ["-e", "00"])
        self.assertEqual(code, 0)
        self.assertEqual(pixie_probe.PIN_PATTERN.search(text)[1], "12345670")
        self.assertEqual(pixie_probe.PSK_PATTERN.search(text)[1], "secretpass")


if __name__ == "__main__":
    unittest.main()
