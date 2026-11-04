"""User-install lifecycle: native Linux files, no user settings or real desktop mutation."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from tests.test_linux_transaction import LinuxInstallFixture
from modules.platform_services import linux_user_install as install
from modules.platform_services.linux_recovery_launcher import LAUNCHER,SCRIPT


class InstallPolicyTests(unittest.TestCase):
    def test_wrong_os_refuses_before_any_file_write(self):
        with patch.object(install,'is_linux',return_value=False),patch.object(install,'RootedFiles') as files:
            with self.assertRaises(OSError):install.install('source','destination')
            files.assert_not_called()
    def test_root_install_is_not_accepted(self):
        with patch.object(install,'is_linux',return_value=True),patch.object(install.os,'geteuid',return_value=0,create=True):
            with self.assertRaises(OSError):install.native_user()


@unittest.skipUnless(sys.platform.startswith('linux'),'Native Linux installer requires dirfds and Unix ownership')
class NativeUserInstallTests(LinuxInstallFixture,unittest.TestCase):
    def setUp(self):
        self.setup_install_fixture();self.destination=self.root/'installed'
        self.addCleanup(self.cleanup_destination)
        self.write(self.root/LAUNCHER,SCRIPT,0o755)
    def cleanup_destination(self):
        # Temporary test-owned path only; never any installed/user application.
        import shutil
        if self.destination.exists():shutil.rmtree(self.destination)
    def test_install_uninstall_preserves_external_settings_and_custom_files(self):
        marker=self.root/'config/settings.ini'
        install.install(self.root,self.destination)
        self.assertEqual((self.destination/'LuXiaoGuan').read_bytes(),self.old_data['LuXiaoGuan'])
        (self.destination/'themes/custom.qss').write_bytes(b'custom')
        with patch.object(install,'menu_shortcut'):
            result=install.uninstall(self.destination)
        self.assertFalse((self.destination/'LuXiaoGuan').exists())
        self.assertEqual((self.destination/'themes/custom.qss').read_bytes(),b'custom')
        self.assertEqual(marker.read_bytes(),b'user settings');self.assertTrue(result['user_data_preserved'])
    def test_existing_directory_is_never_replaced(self):
        self.destination.mkdir();marker=self.destination/'mine';marker.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):install.install(self.root,self.destination)
        self.assertEqual(marker.read_bytes(),b'keep')
    def test_modified_source_does_not_produce_installation(self):
        (self.root/'LuXiaoGuan').write_bytes(b'changed')
        with self.assertRaises(ValueError):install.install(self.root,self.destination)
        self.assertFalse(self.destination.exists())
    def test_changed_critical_installation_refuses_uninstall_before_removal(self):
        install.install(self.root,self.destination);(self.destination/'LuXiaoGuan').write_bytes(b'changed')
        with self.assertRaises(ValueError):install.uninstall(self.destination)
        self.assertTrue((self.destination/'resources/library.dat').exists())
    def test_cancellation_leaves_no_destination(self):
        with self.assertRaises(InterruptedError):install.install(self.root,self.destination,cancel=lambda:True)
        self.assertFalse(self.destination.exists())


if __name__=='__main__':unittest.main()
