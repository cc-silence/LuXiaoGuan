"""Offline format contracts pinned to Hashcat 6.2.6 and hcxtools 6.3.5."""
import hashlib
import hmac
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from modules.capture_services.openwrt import result_converter as c
from modules.capture_services.openwrt.conversion_output import collect_hash_lines, ssid_octets


class ConversionRules(unittest.TestCase):
    def test_official_pmkid_password_vector(self):
        pmk = hashlib.pbkdf2_hmac("sha1", b"hashcat!", b"hashcat-essid", 4096, 32)
        ap, sta = "FC:69:0C:15:82:64", "F4:74:7F:87:F9:F4"
        value = hmac.new(pmk, b"PMK Name" + c._mac_bytes(ap) + c._mac_bytes(sta), "sha1").digest()[:16]
        self.assertEqual(value.hex(), "4d4fe7aac3a2cecab195321ceb99a7d0")
        line = c._pmkid_22000_line(b"hashcat-essid", ap, sta, value.hex())
        self.assertEqual(line, "WPA*01*4d4fe7aac3a2cecab195321ceb99a7d0*fc690c158264*f4747f87f9f4*686173686361742d6573736964***")

    def test_known_password_eapol_through_real_pcap(self):
        ap, sta = bytes.fromhex("001122334455"), bytes.fromhex("001122334466")
        anonce, snonce = b"a"*32, b"s"*32
        name, password = b"test-network", b"test-password"
        pmk = hashlib.pbkdf2_hmac("sha1", password, name, 4096, 32)
        context = min(ap, sta) + max(ap, sta) + min(anonce, snonce) + max(anonce, snonce)
        kck = hmac.new(pmk, b"Pairwise key expansion\0" + context + b"\0", "sha1").digest()[:16]
        def eapol(info, nonce):
            return bytes([2, 3]) + struct.pack(">H", 95) + bytes([2]) + struct.pack(">HHQ", info, 16, 1) + nonce + bytes(48) + bytes(2)
        m1 = eapol(0x8a, anonce)
        m2 = eapol(0x10a, snonce)
        mic = hmac.new(kck, m2, "sha1").digest()[:16]
        m2 = m2[:81] + mic + m2[97:]
        def frame(body, from_ap):
            header = struct.pack("<HH", 0x0208 if from_ap else 0x0108, 0)
            header += (sta+ap+ap if from_ap else ap+sta+ap) + bytes(2)
            return header + bytes.fromhex("aaaa03000000888e") + body
        blob = struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 105)
        for ts, body in enumerate((frame(m1, True), frame(m2, False)), 1):
            blob += struct.pack("<IIII", ts, 0, len(body), len(body)) + body
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/"input.cap", Path(folder)/"output.22000"
            source.write_bytes(blob)
            c.write_22000(source, name, "", output)
            fields = output.read_text().strip().split("*")
            self.assertEqual(bytes.fromhex(fields[2]), mic)
            self.assertEqual(hmac.new(kck, bytes.fromhex(fields[7]), "sha1").digest()[:16], mic)
            legacy = Path(folder)/"output.hccapx"
            c.write_hccapx(source, name, "", legacy)
            self.assertEqual(len(legacy.read_bytes()), 393)
            self.assertEqual(legacy.read_bytes()[:4], b"HCPX")
            self.assertEqual(source.read_bytes(), blob)

    def test_raw_ssid_is_lossless(self):
        name = b"test\xff\x80"
        ap = bytes.fromhex("001122334455")
        header = struct.pack("<HH", 0x80, 0) + bytes([255])*6 + ap + ap + bytes(2)
        frame = header + bytes(12) + bytes([0, len(name)]) + name
        self.assertIsNone(c._parse_beacon(frame, False))
        self.assertEqual(c._parse_beacon(frame, False, raw=True)[1], name)
        self.assertEqual(ssid_octets(name), name)
        self.assertEqual(c._pmkid_22000_line(name, "00:11:22:33:44:55", "00:11:22:33:44:66", "01"*16).split("*")[5], name.hex())

    def test_output_length_dedup_and_limits(self):
        def line(n):
            return "WPA*02*" + "01"*16 + "*001122334455*001122334466*61*" + "02"*32 + "*" + "00"*n + "*00"
        valid = line(99)
        self.assertEqual(collect_hash_lines(iter([line(98), valid, valid, line(257)]), lambda: None), [valid])
        with self.assertRaises(RuntimeError):
            collect_hash_lines(iter([valid]), lambda: None, max_bytes=10)
        with self.assertRaises(InterruptedError):
            collect_hash_lines(iter([valid]), lambda: (_ for _ in ()).throw(InterruptedError()))

    def test_writer_keeps_multiple_sessions_and_pmkid(self):
        ap, sta = "00:11:22:33:44:55", "00:11:22:33:44:66"
        frames = c._PairFrames()
        for number in (1, 2):
            first = dict(replay=number, keyver=2, nonce=bytes([number])*32, timestamp=float(number), pmkid_hex="03"*16)
            second = dict(replay=number, keyver=2, nonce=b"s"*32, timestamp=float(number), mic=bytes([number])*16, eapol=bytes(99))
            frames.m1.append(first); frames.m2.append(second)
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)/"output.22000"
            with patch.object(c, "_analyze_detailed", return_value=(c.HandshakeAnalysis("m1m2", ""), {(ap,sta):frames})), patch.object(c, "extract_beacon_ssids", return_value={ap:b"raw\xff"}):
                c.write_22000("unused", "display", ap, dest)
            lines = dest.read_text().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(sum(x.startswith("WPA*01") for x in lines), 1)
            self.assertTrue(all(x.split("*")[5] == b"raw\xff".hex() for x in lines))

if __name__ == "__main__":
    unittest.main()
