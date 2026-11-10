import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from modules.capture_services.openwrt.snapshot_configuration import (
    capture_configurations, inspect_configuration, restore_configurations, affected_configurations, stage_configurations)
from modules.capture_services.openwrt.installed_dependency_catalog import InstalledPackage
from tests.test_snapshot_original_packages import blob


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.pkg=InstalledPackage('curl','1',{'Package':'curl','Version':'1'},('etc/config/curl',))
        payload=self.root/'payload.tgz';payload.write_bytes(blob({'etc/config/curl':b'user-config'}))
        self.settings=capture_configurations(payload,[self.pkg],self.root,lambda:None)['curl']
        self.row=dict(name='curl',version='1',metadata=self.pkg.metadata,configuration=self.settings)

    def test_after_hook_configuration_write_verified_with_safe_parents(self):
        task=Mock()
        task.reader.run.side_effect=['',hashlib.sha256(b'user-config').hexdigest()+'  /etc/config/curl','stat\t755 0 0']
        stage_configurations(task,[self.row],self.root,'/tmp/private')
        restore_configurations(task,[self.row],self.root,'/tmp/private')
        command=task.reader.run.call_args_list[0].args[1]
        self.assertIn('test ! -L /etc/config',command)
        self.assertIn('test ! -L /etc/config/curl',command)
        self.assertIn('tar -xzf /tmp/private/curl.config.tgz -C /',command)
        task._verify_remote.assert_called_once()

    def test_failed_file_verification_is_not_reported_successful(self):
        task=Mock();task.reader.run.side_effect=['','0'*64+'  /etc/config/curl']
        with self.assertRaisesRegex(ValueError,'checksum'):
            restore_configurations(task,[self.row],self.root,'/tmp/private')

    def test_content_match_with_wrong_permissions_is_not_success(self):
        task=Mock()
        task.reader.run.side_effect=['',hashlib.sha256(b'user-config').hexdigest()+'  /etc/config/curl','stat\t777 123 456']
        with self.assertRaisesRegex(ValueError,'permissions mismatch'):
            restore_configurations(task,[self.row],self.root,'/tmp/private')

    def test_config_archive_uses_numeric_identity_and_keeps_mode(self):
        source=self.root/'named.tgz'
        with tarfile.open(source,'w:gz',format=tarfile.PAX_FORMAT) as archive:
            item=tarfile.TarInfo('etc/config/curl')
            item.uid,item.gid,item.mode,item.size=123,456,0o640,4
            item.uname,item.gname='old-name','old-group'
            item.pax_headers={'uname':'old-name','gname':'old-group'}
            archive.addfile(item,io.BytesIO(b'user'))
        capture_configurations(source,[self.pkg],self.root,lambda:None)
        with tarfile.open(self.root/'curl.config.tgz') as archive:
            item=archive.getmember('etc/config/curl')
            self.assertEqual((item.uid,item.gid,item.mode),(123,456,0o640))
            self.assertEqual((item.uname,item.gname),('',''))
            self.assertNotIn('uname',item.pax_headers)

    def test_extended_attribute_configuration_is_not_silently_repacked(self):
        self.pkg.extended_attributes=('etc/config/curl',)
        payload=self.root/'extended.tgz';payload.write_bytes(blob({'etc/config/curl':b'user'}))
        with self.assertRaisesRegex(ValueError,'extended attributes'):
            capture_configurations(payload,[self.pkg],self.root,lambda:None)

    def test_oversized_installed_metadata_stops_before_payload_scan(self):
        import gzip
        from modules.capture_services.openwrt.installed_dependency_reader import InstalledDependencyReader
        item=tarfile.TarInfo('huge-unrelated.control');item.size=32*1024*1024+1
        raw=gzip.compress(item.tobuf()+b'\0'*1024)
        reader=InstalledDependencyReader(Mock(),Mock(),Mock());reader.transfer.read.return_value=raw
        with self.assertRaisesRegex(ValueError,'archive is too large'):
            reader.configuration_ownership({'manager':'opkg'},[self.pkg])

    def test_network_credentials_and_unrelated_files_cannot_be_configurations(self):
        path=self.root/'curl.config.tgz'
        for name in ('etc/config/network','etc/config/wireless','etc/config/firewall','etc/shadow','usr/bin/curl','../etc/config/curl'):
            with self.subTest(name=name):
                path.write_bytes(blob({name:b'forbidden'}))
                with self.assertRaises(ValueError):
                    inspect_configuration(path,dict(paths=[name]))

    def test_symlink_configuration_is_rejected(self):
        path=self.root/'curl.config.tgz'
        with tarfile.open(path,'w:gz') as archive:
            item=tarfile.TarInfo('etc/config/curl');item.type=tarfile.SYMTYPE;item.linkname='/etc/shadow';archive.addfile(item)
        with self.assertRaises(ValueError):inspect_configuration(path,self.settings)

    def test_builtin_dependency_settings_are_included_but_other_versions_are_preserved(self):
        builtin=InstalledPackage('luci-base','1',{'Package':'luci-base','Version':'1'})
        root=dict(name='luci-i18n-base-zh-cn',version='1',metadata={'Package':'luci-i18n-base-zh-cn','Version':'1','Depends':'luci-base'})
        base=dict(name='luci-base',version='1',metadata=builtin.metadata,configuration=self.settings,builtin=True)
        other=dict(name='curl',version='1',metadata=self.pkg.metadata,configuration=self.settings)
        result=affected_configurations([root,base,other],['luci-i18n-base-zh-cn'],{'luci-base':builtin,'curl':self.pkg},'opkg')
        self.assertEqual(result,[base])
        builtin.version='2'
        with self.assertRaisesRegex(ValueError,'version differs'):
            affected_configurations([root,base,other],['luci-i18n-base-zh-cn'],{'luci-base':builtin,'curl':self.pkg},'opkg')

    def test_changed_configuration_is_rejected_before_transfer(self):
        (self.root/'curl.config.tgz').write_bytes(blob({'etc/config/curl':b'changed'}))
        task=Mock()
        with self.assertRaisesRegex(ValueError,'changed before staging'):
            stage_configurations(task,[self.row],self.root,'/tmp/private')
        task.transfer.upload.assert_not_called()

    def test_schema_one_without_settings_does_not_read_new_metadata(self):
        self.assertEqual(affected_configurations([{'name':'curl'}],['curl'],{},'opkg'),[])


class RestoreOrderingTests(unittest.TestCase):
    def test_configuration_is_applied_only_after_native_install(self):
        from unittest.mock import patch
        from tests.test_dependency_restore_failure_paths import RestoreFailureTests
        fixture=RestoreFailureTests();fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task=fixture.task
        order=[]
        previous=task.reader.run.side_effect
        def run(action,*args):
            order.append(action)
            return previous(action,*args)
        task.reader.run.side_effect=run
        with patch('modules.capture_services.openwrt.installed_dependency_backup.verify_identity'), patch(
            'modules.capture_services.openwrt.installed_dependency_backup.restore_configurations',
            side_effect=lambda *args:order.append('configuration')):
            result=task.restore(fixture.review)
        self.assertTrue(result['complete'])
        self.assertLess(order.index('restore_install'),order.index('configuration'))

    def test_failed_staging_prevents_native_install(self):
        from unittest.mock import patch
        from tests.test_dependency_restore_failure_paths import RestoreFailureTests
        fixture=RestoreFailureTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with patch('modules.capture_services.openwrt.installed_dependency_backup.stage_configurations',side_effect=OSError('transfer failed')):
            with self.assertRaisesRegex(OSError,'transfer failed'):
                fixture.task.restore(fixture.review)
        fixture.assert_not_installed()

    def test_failed_post_install_validation_never_touches_settings(self):
        from unittest.mock import patch
        from tests.test_dependency_restore_failure_paths import RestoreFailureTests
        fixture=RestoreFailureTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with patch('modules.capture_services.openwrt.installed_dependency_backup.verify_identity'), patch.object(
            fixture.task,'_verify_restored',side_effect=ValueError('unexpected package change')), patch(
            'modules.capture_services.openwrt.installed_dependency_backup.restore_configurations') as restore:
            with self.assertRaisesRegex(RuntimeError,'unexpected package change'):
                fixture.task.restore(fixture.review)
            restore.assert_not_called()

    def test_builtin_settings_survive_schema_two_archive_roundtrip(self):
        import json
        from modules.capture_services.openwrt.dependency_snapshot_archive import save_snapshot, load_snapshot, digest_file
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config=root/'curl.config.tgz';config.write_bytes(blob({'etc/config/curl':b'user'}))
            row=dict(name='curl',version='1',metadata={'Package':'curl','Version':'1'},builtin=True,
                configuration=dict(file='configurations/curl.tgz',paths=['etc/config/curl'],size=config.stat().st_size,sha256=digest_file(config)))
            manifest=dict(format='luxiaoguan-installed-dependencies',schema=2,manager='opkg',identity={},roots=['curl'],packages=[row])
            archive=root/'backup.zip';save_snapshot(archive,manifest,root,lambda:None)
            workspace=root/'read';workspace.mkdir()
            self.assertEqual(load_snapshot(archive,workspace,lambda:None),manifest)
            self.assertEqual((workspace/'curl.config.tgz').read_bytes(),config.read_bytes())
            row['configuration']['sha256']='0'*64
            bad=root/'bad.zip';save_snapshot(bad,manifest,root,lambda:None)
            another=root/'bad';another.mkdir()
            with self.assertRaisesRegex(ValueError,'configuration checksum'):
                load_snapshot(bad,another,lambda:None)
