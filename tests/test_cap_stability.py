"""Source stability must hold through validation and publication."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from modules.capture_services.openwrt import cap_container as c
from tests.test_cap_container import ng


class CapStabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/"source.pcapng"; self.source.write_bytes(ng())
        self.native=self.root/"native.cap"
        c.convert_to_cap(self.source,self.native)

    @staticmethod
    def touch_changed(path):
        info=path.stat()
        os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns+2000000000))

    def test_native_change_after_read_is_rejected(self):
        reader=c._pcap_records
        def changed(handle,check):
            yield from reader(handle,check)
            self.touch_changed(self.native)
        with patch.object(c,"_pcap_records",changed):
            with self.assertRaises(ValueError):
                c.validate_cap(self.native)
        self.assertTrue(self.native.exists())

    def test_native_cancel_after_last_record_is_honored(self):
        reader=c._pcap_records; event=threading.Event()
        def cancelled(handle,check):
            yield from reader(handle,check)
            event.set()
        with patch.object(c,"_pcap_records",cancelled):
            with self.assertRaises(InterruptedError):
                c.validate_cap(self.native,cancel_event=event)

    def test_source_change_during_output_verification_prevents_publish(self):
        reader=c._pcap_records
        def changed(handle,check):
            yield from reader(handle,check)
            self.touch_changed(self.source)
        destination=self.root/"changed.cap"
        with patch.object(c,"_pcap_records",changed):
            with self.assertRaises(ValueError):
                c.convert_to_cap(self.source,destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob("*.part")))
        self.assertTrue(self.source.exists())

    def test_unchanged_native_capture_is_not_rewritten(self):
        before=self.native.stat()
        self.assertEqual(c.validate_cap(self.native),self.native)
        self.assertEqual(self.native.stat().st_mtime_ns,before.st_mtime_ns)

if __name__ == "__main__":
    unittest.main()
