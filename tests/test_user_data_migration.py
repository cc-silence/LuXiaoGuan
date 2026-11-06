"""Native directory migration preserves settings, conflicts and interruption safety."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from modules.platform_services.user_data_migration import migrate_directory, prepare_user_directories, CONFLICT_DIRECTORY
from modules.platform_services import application_paths as paths
from modules.platform_services.file_lease import file_lease


class UserDataMigrationTests(unittest.TestCase):
    def pair(self, root):
        return Path(root)/'RouterManager', Path(root)/'LuXiaoGuan'

    def test_whole_directory_rename_and_second_run(self):
        with TemporaryDirectory() as folder:
            old,new=self.pair(folder);(old/'config').mkdir(parents=True)
            (old/'config/settings.ini').write_bytes(b'original settings')
            (old/'history.bin').write_bytes(bytes(range(256)))
            identity=(old/'history.bin').stat().st_ino
            migrate_directory(old,new)
            self.assertFalse(old.exists());self.assertEqual((new/'config/settings.ini').read_bytes(),b'original settings')
            self.assertEqual((new/'history.bin').stat().st_ino,identity)
            self.assertEqual(migrate_directory(old,new)['moved'],0)

    def test_existing_destination_retains_both_conflicting_versions(self):
        with TemporaryDirectory() as folder:
            old,new=self.pair(folder)
            for directory in (old,new):(directory/'config').mkdir(parents=True)
            (old/'config/settings.ini').write_bytes(b'old settings')
            (new/'config/settings.ini').write_bytes(b'new settings')
            (old/'config/proxy.json').write_bytes(b'proxy')
            report=migrate_directory(old,new)
            self.assertEqual((new/'config/settings.ini').read_bytes(),b'new settings')
            self.assertEqual((Path(report['backup'])/'config/settings.ini').read_bytes(),b'old settings')
            self.assertEqual((new/'config/proxy.json').read_bytes(),b'proxy')
            self.assertFalse(old.exists());self.assertEqual(report['conflicts'],1)

    def test_failed_move_preserves_source_and_can_resume(self):
        with TemporaryDirectory() as folder:
            old,new=self.pair(folder);old.mkdir();new.mkdir();(old/'settings.ini').write_bytes(b'settings')
            original=Path.rename
            def rename(path,target):
                if path.name=='settings.ini':raise PermissionError('file in use')
                return original(path,target)
            with patch.object(Path,'rename',rename),self.assertRaises(PermissionError):migrate_directory(old,new)
            self.assertEqual((old/'settings.ini').read_bytes(),b'settings')
            migrate_directory(old,new)
            self.assertEqual((new/'settings.ini').read_bytes(),b'settings');self.assertFalse(old.exists())

    def test_concurrent_migration_is_rejected_without_changes(self):
        with TemporaryDirectory() as folder:
            old,new=self.pair(folder);old.mkdir();(old/'settings.ini').write_bytes(b'settings')
            with (Path(folder)/'.LuXiaoGuan-data-migration.lock').open('wb') as handle,file_lease(handle):
                with self.assertRaises(OSError):migrate_directory(old,new)
            self.assertTrue(old.exists());self.assertFalse(new.exists())

    def test_wrong_paths_and_link_roots_are_rejected(self):
        with TemporaryDirectory() as folder:
            old,new=self.pair(folder);external=Path(folder)/'external';external.mkdir()
            with self.assertRaises(ValueError):migrate_directory(external,new)
            try:old.symlink_to(external,target_is_directory=True)
            except OSError:self.skipTest('Creating symlinks requires native privilege')
            with self.assertRaises(ValueError):migrate_directory(old,new)
            self.assertTrue(external.exists());self.assertFalse(new.exists())

    def test_startup_uses_new_windows_location_and_respects_override(self):
        with TemporaryDirectory() as folder,patch.object(paths,'is_linux',return_value=False),patch.dict(os.environ,{'LOCALAPPDATA':folder},clear=True):
            old,new=self.pair(folder);old.mkdir()
            prepare_user_directories();self.assertTrue(new.exists());self.assertFalse(old.exists())
            self.assertEqual(paths.user_config_dir(),new/'config')
            old.mkdir()
            with patch.dict(os.environ,{'ROUTERMANAGER_USER_CONFIG_DIR':str(Path(folder)/'isolated')}):
                self.assertEqual(prepare_user_directories(),())
            self.assertTrue(old.exists())

    def test_linux_three_xdg_locations_migrate_independently(self):
        with TemporaryDirectory() as folder,patch.object(paths,'is_linux',return_value=True),patch.dict(os.environ,{},clear=True):
            root=Path(folder)
            def directory(kind):return root/kind/'LuXiaoGuan'
            for kind in ('config','state','data'):
                legacy=directory(kind).with_name('RouterManager');legacy.mkdir(parents=True);(legacy/'entry').write_bytes(kind.encode())
            with patch.object(paths,'linux_user_directory',side_effect=directory):prepare_user_directories()
            for kind in ('config','state','data'):self.assertEqual((directory(kind)/'entry').read_bytes(),kind.encode())

class DesktopMigrationOrderTests(unittest.TestCase):
    def exercise(self, acquired, fail=False):
        from unittest.mock import Mock
        from modules.desktop_shell.application_start import run_desktop
        calls=[];instance=Mock();instance.acquire.side_effect=lambda:calls.append('instance') or acquired
        bootstrap=Mock();bootstrap.run.side_effect=lambda *a,**k:calls.append('desktop') or 9
        def migrate():
            calls.append('migration')
            if fail:raise PermissionError('file in use')
        with patch('sys.argv',['app']),patch('PySide6.QtWidgets.QApplication'), \
             patch('modules.desktop_shell.single_instance.SingleInstanceController',return_value=instance), \
             patch('modules.platform_services.application_identity.set_app_id'), \
             patch('modules.platform_services.user_data_migration.prepare_user_directories',side_effect=migrate), \
             patch.dict('sys.modules',{'router_app.bootstrap':bootstrap}):
            if fail:
                with self.assertRaises(PermissionError):run_desktop('test')
                instance.close.assert_called_once()
            else:self.assertEqual(run_desktop('test'),9 if acquired else 0)
        return calls

    def test_existing_instance_is_activated_without_migration(self):
        self.assertEqual(self.exercise(False),['instance'])

    def test_migration_happens_after_ownership_and_before_desktop(self):
        self.assertEqual(self.exercise(True),['instance','migration','desktop'])

    def test_migration_failure_releases_instance_without_loading_desktop(self):
        self.assertEqual(self.exercise(True,fail=True),['instance','migration'])

if __name__=='__main__':unittest.main()
