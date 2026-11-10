"""Windows portable settings are isolated per installation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from modules.config_services.config_store import ConfigPair, application_settings_pair
from modules.platform_services import application_paths


class PortableSettingsPolicyTests(unittest.TestCase):
    def test_windows_ignores_other_installations_appdata_and_writes_only_local(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(application_paths, 'is_linux', return_value=False), \
                patch.dict('os.environ', {'ROUTERMANAGER_USER_CONFIG_DIR': ''}):
            root=Path(folder); old=root/'old-user'/'settings.json'; first=root/'app-a'/'config'/'settings.json'; second=root/'app-b'/'config'/'settings.json'
            ConfigPair(old, old).write_json({'value':'legacy'})
            a=application_settings_pair(old, first); b=application_settings_pair(old, second)
            self.assertIsNone(a.read_newest_json()); self.assertIsNone(b.read_newest_json())
            a.write_json({'value':'new'})
            self.assertEqual(a.read_newest_json(), {'value':'new'})
            self.assertIsNone(b.read_newest_json())
            self.assertEqual(ConfigPair(old,old).read_newest_json(), {'value':'legacy'})

    def test_packaged_defaults_do_not_mark_scale_as_user_selected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(application_paths, 'is_linux', return_value=False):
            path=Path(folder)/'settings.ini'; path.write_text('[ui]\nscale=100\n',encoding='utf-8')
            pair=application_settings_pair(Path(folder)/'old.ini',path)
            self.assertIsNone(pair.read_primary_ini_text())
            pair.write_ini_text('[ui]\nscale=125\n')
            self.assertIn('125',pair.read_primary_ini_text())

    def test_linux_retains_existing_user_storage(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(application_paths, 'is_linux', return_value=True):
            primary=Path(folder)/'user'/'settings.json'; mirror=Path(folder)/'other'/'settings.json'
            pair=application_settings_pair(primary, mirror)
            pair.write_json({'value':3})
            self.assertTrue(primary.is_file());self.assertTrue(mirror.is_file())


class PortableManagerIntegrationTests(unittest.TestCase):
    def test_capture_settings_use_application_copy_not_old_user_copy(self):
        from modules.config_services import capture_settings_manager as capture
        with tempfile.TemporaryDirectory() as folder, patch.object(application_paths, 'is_linux', return_value=False), \
                patch.dict('os.environ', {'ROUTERMANAGER_USER_CONFIG_DIR': ''}):
            root=Path(folder); old=root/'old'; app=root/'app'; capture_dir=app/'capture'
            old_file=old/'capture/openwrt_settings.json'
            ConfigPair(old_file,old_file).write_json({'host':'192.0.2.10'})
            with patch.object(capture,'user_config_dir',return_value=old), \
                    patch.object(capture,'CAPTURE_MIRROR_DIR',capture_dir), \
                    patch.object(capture,'CAPTURE_SETTINGS_FILE',app/'capture_settings.json'):
                manager=capture.CaptureSettingsManager()
                pair=manager._split_pair('openwrt_settings')
                self.assertEqual(pair.primary,capture_dir/'openwrt_settings.json')
                self.assertIsNone(pair.read_newest_json())
                pair.write_json({'host':'192.0.2.20'})
                self.assertEqual(ConfigPair(old_file,old_file).read_newest_json(),{'host':'192.0.2.10'})


class PortableMigrationTests(unittest.TestCase):
    def test_same_old_and_new_directory_never_removes_live_configuration(self):
        from modules.config_services.portable_settings_migration import migrate
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); target=root/'settings.ini'
            ConfigPair(target,target).write_ini_text('[ui]\nscale=125\n')
            before=target.read_bytes()
            self.assertEqual(migrate(root,root),0)
            self.assertEqual(target.read_bytes(),before)

    def test_migration_error_does_not_prevent_application_start(self):
        from modules.config_services import portable_settings_migration as migration
        import os, sys
        with patch.object(sys,'frozen',True,create=True), \
                patch.object(application_paths,'is_linux',return_value=False), \
                patch.dict(os.environ,{'ROUTERMANAGER_USER_CONFIG_DIR':''}), \
                patch.object(migration,'migrate',side_effect=PermissionError('private')), \
                self.assertLogs(migration.__name__,level='WARNING'):
            self.assertEqual(migration.prepare(),0)

    def test_existing_install_keeps_newer_copy_backups_and_removes_old(self):
        from modules.config_services.portable_settings_migration import migrate, MARKER
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); old=root/'appdata'; app=root/'portable'
            source=old/'capture/openwrt_settings.json'; target=app/'capture/openwrt_settings.json'
            ConfigPair(target,target).write_json({'value':'older'})
            ConfigPair(source,source).write_json({'value':'newer'})
            import json
            older=json.loads(target.read_text(encoding='utf-8')); newer=json.loads(source.read_text(encoding='utf-8'))
            older['_winbox']['last_write_ms']=10;newer['_winbox']['last_write_ms']=20
            target.write_text(json.dumps(older),encoding='utf-8');source.write_text(json.dumps(newer),encoding='utf-8')
            srcbytes=source.read_bytes();dstbytes=target.read_bytes()
            self.assertEqual(migrate(old,app),1)
            self.assertFalse(source.exists());self.assertEqual(target.read_bytes(),srcbytes)
            backups=list((app/'portable-migration-backup').iterdir())
            self.assertEqual(len(backups),1)
            self.assertEqual((backups[0]/'old').read_bytes(),srcbytes)
            self.assertEqual((backups[0]/'local').read_bytes(),dstbytes)
            self.assertTrue((app/MARKER).is_file())
            self.assertEqual(migrate(old,app),0)

    def test_new_extraction_marker_never_imports_or_deletes_old_install_settings(self):
        from modules.config_services.portable_settings_migration import migrate, MARKER
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'appdata';app=root/'fresh';app.mkdir()
            source=old/'settings.ini';ConfigPair(source,source).write_ini_text('[ui]\nscale=125\n')
            (app/MARKER).write_text('1',encoding='ascii')
            self.assertEqual(migrate(old,app),0)
            self.assertTrue(source.exists());self.assertFalse((app/'settings.ini').exists())

    def test_repeated_cleanup_failure_reuses_identical_backup(self):
        from modules.config_services import portable_settings_migration as migration
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'old';app=root/'app'
            source=old/'settings.ini';target=app/'settings.ini'
            ConfigPair(source,source).write_ini_text('[ui]\nscale=125\n')
            target.parent.mkdir(parents=True);target.write_bytes(source.read_bytes())
            original=Path.unlink
            def denied(path,*args,**kwargs):
                if path == source:raise PermissionError('busy')
                return original(path,*args,**kwargs)
            with patch.object(Path,'unlink',denied):
                for _ in range(3):
                    with self.assertRaises(PermissionError):migration.migrate(old,app)
            self.assertTrue(source.exists());self.assertTrue(target.exists())
            self.assertEqual(len(list((app/'portable-migration-backup').iterdir())),1)

    def test_new_local_file_appearing_mid_migration_is_not_overwritten(self):
        from modules.config_services import portable_settings_migration as migration
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'old';app=root/'app'
            source=old/'proxy_config.json';target=app/'proxy_config.json'
            ConfigPair(source,source).write_json({'value':'old'})
            original=migration._backup
            def create_concurrent(path, raw):
                original(path,raw)
                if path.name=='old':
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_text('{"value":"new"}',encoding='utf-8')
            with patch.object(migration,'_backup',side_effect=create_concurrent):
                with self.assertRaises(ValueError):migration.migrate(old,app)
            self.assertTrue(source.exists())
            self.assertEqual(target.read_text(encoding='utf-8'),'{"value":"new"}')
            self.assertFalse((app/migration.MARKER).exists())

    def test_invalid_legacy_is_preserved_and_never_marks_success(self):
        from modules.config_services.portable_settings_migration import migrate, MARKER
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'old';app=root/'app';old.mkdir()
            source=old/'proxy_config.json';source.write_bytes(b'{broken')
            with self.assertRaises(ValueError):migrate(old,app)
            self.assertEqual(source.read_bytes(),b'{broken')
            self.assertFalse((app/MARKER).exists())


if __name__ == '__main__': unittest.main()
