import unittest
from datetime import datetime
from types import SimpleNamespace
from modules.main_pages.packet_capture.openwrt.backup_filenames import dependency_backup_filename


class DependencyBackupFilenameTests(unittest.TestCase):
    def test_model_system_and_version_from_snapshot(self):
        device=SimpleNamespace(model='TP-Link TL-WDR7500 v3', distribution='OpenWrt', version='21.02.1')
        name=dependency_backup_filename(device,now=datetime(2026,11,8,21,2,26))
        self.assertTrue(name.endswith('_TP-Link TL-WDR7500 v3_OpenWrt_21.02.1_20261108_210226.zip'))
        self.assertNotIn('LuXiaoGuan_Dependencies',name)

    def test_missing_and_unsafe_device_information(self):
        self.assertTrue(dependency_backup_filename().endswith('.zip'))
        device=SimpleNamespace(model='../router:/test\\name\n',distribution='X'*500,version='Y'*500)
        name=dependency_backup_filename(device)
        self.assertFalse(any(c in name for c in '/\\:\n'))
        self.assertLess(len(name.encode('utf-8')),255)
