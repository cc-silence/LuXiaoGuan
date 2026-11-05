"""Durable per-signal deduplication without storing credentials in the database."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest
from modules.capture_services.openwrt.password_history import PasswordAttemptHistory,clear_history

class PasswordHistoryTests(unittest.TestCase):
    def test_reopen_dedup_target_isolation_and_clear_preserve_other_files(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'history.db';key=b'k'*32
            target=NS(bssid='02:11:22:33:44:55',ssid='private-ssid')
            other=NS(bssid='02:11:22:33:44:66',ssid='private-ssid')
            with PasswordAttemptHistory(path,key=key) as history:
                history.mark(target,'private-password');history.mark(target,'private-password')
                self.assertEqual(history.count,1);self.assertFalse(history.contains(other,'private-password'))
            with PasswordAttemptHistory(path,key=key) as history:self.assertTrue(history.contains(target,'private-password'))
            self.assertNotIn(b'private-password',path.read_bytes());self.assertNotIn(b'private-ssid',path.read_bytes())
            keep=Path(folder)/'dictionary.txt';keep.write_text('keep',encoding='utf-8')
            self.assertEqual(clear_history(path),1);self.assertEqual(keep.read_text(),'keep')
            with PasswordAttemptHistory(path,key=key) as history:self.assertFalse(history.contains(target,'private-password'))

    def test_lost_secure_key_does_not_silently_forget_existing_records(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'history.db';target=NS(bssid='02:11:22:33:44:55',ssid='fixture')
            with PasswordAttemptHistory(path,key=b'k'*32) as history:history.mark(target,'candidate')
            with patch('modules.platform_services.credentials.read_generic_password',return_value=''),patch('modules.platform_services.credentials.write_generic_password') as write:
                with self.assertRaises(RuntimeError):PasswordAttemptHistory(path)
                write.assert_not_called()

    def test_capacity_stops_instead_of_evicting_and_repeating_old_candidates(self):
        with TemporaryDirectory() as folder,patch('modules.capture_services.openwrt.password_history.MAX_RECORDS',1):
            with PasswordAttemptHistory(Path(folder)/'history.db',key=b'k'*32) as history:
                target=NS(bssid='02:11:22:33:44:55',ssid='fixture');history.mark(target,'first-pass')
                with self.assertRaises(RuntimeError):history.mark(target,'second-pass')
                self.assertTrue(history.contains(target,'first-pass'))
