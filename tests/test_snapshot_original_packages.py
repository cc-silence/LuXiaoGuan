"""Regressions for consumed defaults, exact originals and current configuration."""
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from threading import Event
from contextlib import nullcontext
from modules.capture_services.openwrt.installed_dependency_catalog import InstalledPackage
from modules.capture_services.openwrt.snapshot_original_packages import SnapshotOriginalPackages
from modules.capture_services.openwrt.installed_dependency_backup import InstalledDependencyBackup
from modules.capture_services.openwrt.dependency_snapshot_archive import rebuild_package, _write_ipk, _tar_blob
from modules.capture_services.openwrt.dependency_snapshot_package_check import inspect_package

PREFIX = 'modules.capture_services.openwrt.snapshot_original_packages.'


def blob(files):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode='w:gz') as archive:
        for name, content in files.items():
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(content), 0o755
            archive.addfile(item, io.BytesIO(content))
    return target.getvalue()


class OriginalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = Mock()
        self.task.guard.cancel = Event()
        self.pkg = InstalledPackage('curl', '1-r0',
            {'Package':'curl', 'Version':'1-r0', 'Architecture':'mipsel_24kc', 'I':'128'},
            ('etc/uci-defaults/curl', 'etc/config/curl', 'usr/bin/curl'))
        self.inventory = dict(manager='opkg', identity=dict(release=dict(
            distribution='OpenWrt', version='24.10.8', target='ramips/mt7621')),
            packages={'curl':self.pkg})
        self.source = SnapshotOriginalPackages(self.task, self.inventory, [self.pkg],
            '/tmp/luxiaoguan-dependencies-' + 'a'*32, self.root)
        self.ipk = self.root/'original.ipk'
        control = b'Package: curl\nVersion: 1-r0\nArchitecture: mipsel_24kc\n'
        _write_ipk(self.ipk, [('debian-binary',b'2.0\n'),
            ('control.tar.gz',_tar_blob([('control',control,0o644)])),
            ('data.tar.gz',blob({'etc/uci-defaults/curl':b'original-init',
                                 'etc/config/curl':b'factory-config'}))])

    def test_only_consumed_defaults_recovered_current_config_retained_both_formats(self):
        self.source.original = Mock(return_value=self.ipk)
        recovered = self.source.recover(['/etc/uci-defaults/curl'])
        for manager in ('opkg','apk'):
            with self.subTest(manager=manager):
                payload = self.root/(manager+'.tgz')
                payload.write_bytes(blob({'etc/config/curl':b'user-config', 'usr/bin/curl':b'executable'}))
                task = object.__new__(InstalledDependencyBackup)
                task.guard = self.task.guard
                task._merge_originals(payload, recovered, ['/etc/uci-defaults/curl'])
                with tarfile.open(payload) as archive:
                    self.assertEqual(archive.extractfile('etc/config/curl').read(),b'user-config')
                    self.assertEqual(archive.extractfile('etc/uci-defaults/curl').read(),b'original-init')
                if manager == 'apk':
                    self.pkg.metadata = {'P':'curl','V':'1-r0','A':'mipsel_24kc'}
                output = self.root/('rebuilt.'+manager)
                rebuild_package(self.pkg,manager,payload,output,self.task.guard.check)
                self.assertGreater(inspect_package(output,manager,dict(name='curl',version='1-r0')),0)

    def test_ordinary_missing_file_is_never_repaired_or_downloaded(self):
        self.source.original = Mock()
        with self.assertRaisesRegex(ValueError,'ordinary'):
            self.source.recover(['usr/bin/curl'])
        self.source.original.assert_not_called()

    def test_mismatched_original_identity_and_absent_script_fail(self):
        self.source.original = Mock(return_value=self.ipk)
        self.pkg.version = '2-r0'
        with self.assertRaisesRegex(ValueError,'identity'):
            self.source.recover(['etc/uci-defaults/curl'])
        self.pkg.version = '1-r0'
        self.pkg.files += ('etc/uci-defaults/absent',)
        with self.assertRaisesRegex(ValueError,'missing or unsafe'):
            self.source.recover(['etc/uci-defaults/absent'])

    def test_shared_authenticated_local_package_prevents_network(self):
        repo = Mock(kind='common')
        repo.root = self.root
        repo.profile.return_value = {'packages':[]}
        repo.package_row.return_value = dict(name='curl',version='1-r0')
        repo.materialize.return_value = self.ipk
        with patch(PREFIX+'CommonBundleRepository',return_value=repo), patch(PREFIX+'bundle_lock',side_effect=lambda *a,**k:nullcontext()), patch.object(self.source,'_online',side_effect=AssertionError('network')):
            with self.source:
                self.assertEqual(self.source.original(self.pkg),self.ipk)
            repo.package_row.assert_called_with(repo.profile.return_value,'curl','1-r0')
            self.assertEqual(self.source.provenance['curl']['source'],'local-common')

    def test_corrupt_local_original_is_not_silently_downgraded(self):
        repo = Mock(kind='common',root=self.root)
        repo.profile.return_value = {'packages':[]}
        repo.materialize.side_effect = ValueError('checksum')
        with patch(PREFIX+'CommonBundleRepository',return_value=repo), patch(PREFIX+'bundle_lock',side_effect=lambda *a,**k:nullcontext()), patch.object(self.source,'_online') as online:
            with self.source, self.assertRaisesRegex(ValueError,'checksum'):
                self.source.original(self.pkg)
            online.assert_not_called()

    def test_native_apk_only_extracts_privately_never_installs(self):
        self.inventory['manager'] = 'apk'
        self.task.transfer.read.return_value = blob({'etc/uci-defaults/curl':b'original-init'})
        self.source.original = Mock(return_value=self.ipk)
        self.source.recover(['etc/uci-defaults/curl'])
        commands = [call.args[1] for call in self.task.reader.run.call_args_list]
        self.assertFalse(any('apk verify ' in command for command in commands))
        self.assertTrue(any('apk extract --allow-untrusted --no-network --destination' in command for command in commands))
        self.assertFalse(any('apk add' in command for command in commands))
        self.assertIn('test ! -L "$d"',self.task.transfer.read.call_args.args[0])

    def test_extract_failure_reports_package_and_stops_payload_read(self):
        self.inventory['manager'] = 'apk'
        self.source.original = Mock(return_value=self.ipk)
        self.task.reader.run.side_effect = ValueError('signature rejected')
        with self.assertRaisesRegex(ValueError,'curl 1-r0'):
            self.source.recover(['etc/uci-defaults/curl'])
        self.assertEqual(self.task.reader.run.call_count,1)
        self.task.transfer.read.assert_not_called()

    def test_native_extraction_failure_keeps_real_reason(self):
        import time
        from types import SimpleNamespace
        from modules.capture_services.openwrt.installed_dependency_reader import InstalledDependencyReader
        runner = Mock()
        runner.run.return_value = SimpleNamespace(exit_code=2, stdout='',
            stderr='ERROR: No space left on device')
        guard = Mock(deadline=time.monotonic() + 60, cancel=Event())
        reader = InstalledDependencyReader(runner, Mock(), guard)
        with patch('modules.capture_services.openwrt.installed_dependency_reader.require_command_completion'):
            with self.assertRaisesRegex(RuntimeError, 'No space left on device'):
                reader.run('original_extract', 'apk extract fixture.apk')

    def test_original_timeout_keeps_timeout_type(self):
        self.source.original = Mock(side_effect=TimeoutError('Dependency backup/restore timed out'))
        with self.assertRaises(TimeoutError):
            self.source.recover(['etc/uci-defaults/curl'])
        self.task.transfer.upload.assert_not_called()

    def test_extraction_error_preserves_both_output_channels(self):
        import time
        from types import SimpleNamespace
        from modules.capture_services.openwrt.installed_dependency_reader import InstalledDependencyReader
        runner = Mock()
        runner.run.return_value = SimpleNamespace(exit_code=2, stdout='payload read failed',
            stderr='warning: metadata')
        reader = InstalledDependencyReader(runner, Mock(), Mock(deadline=time.monotonic()+60, cancel=Event()))
        with patch('modules.capture_services.openwrt.installed_dependency_reader.require_command_completion'):
            with self.assertRaises(RuntimeError) as caught:
                reader.run('original_extract', 'fixture')
        self.assertIn('payload read failed', str(caught.exception))
        self.assertIn('warning: metadata', str(caught.exception))

    def test_upload_checksum_failure_prevents_unsigned_extraction(self):
        self.inventory['manager'] = 'apk'
        self.source.original = Mock(return_value=self.ipk)
        self.task._verify_remote.side_effect = ValueError('checksum mismatch')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.source.recover(['etc/uci-defaults/curl'])
        self.task.reader.run.assert_not_called()
        self.task.transfer.read.assert_not_called()

    def test_merge_rejects_extra_files_and_does_not_replace_current(self):
        current = self.root/'current.tgz'
        current.write_bytes(blob({'etc/config/curl':b'user'}))
        original = current.read_bytes()
        recovered = self.root/'recovered.tgz'
        recovered.write_bytes(blob({'etc/uci-defaults/curl':b'init','usr/bin/unrelated':b'bad'}))
        task=object.__new__(InstalledDependencyBackup)
        task.guard=self.task.guard
        with self.assertRaisesRegex(ValueError,'set mismatch'):
            task._merge_originals(current,recovered,['/etc/uci-defaults/curl'])
        self.assertEqual(current.read_bytes(),original)

    def test_cancel_before_source_access(self):
        self.task.guard.check.side_effect=InterruptedError('cancelled')
        with patch(PREFIX+'CommonBundleRepository') as repository:
            with self.assertRaises(InterruptedError):
                self.source.original(self.pkg)
            repository.assert_not_called()

    def test_online_requires_exact_version_hash_and_size(self):
        import hashlib
        from types import SimpleNamespace
        device = self.source._device(self.pkg)
        raw = self.ipk.read_bytes()
        row = SimpleNamespace(version='2-r0',filename='curl_1-r0.ipk',
            sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),url='https://downloads.openwrt.org/curl.ipk')
        self.source.online = {(device.version,device.package_architecture,'opkg'):({'curl':row},None)}
        with patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes',return_value=raw) as fetch:
            with self.assertRaisesRegex(ValueError,'Exact original unavailable'):
                self.source._online(self.pkg,device)
            fetch.assert_not_called()
            row.version='1-r0'
            result=self.source._online(self.pkg,device)
            self.assertEqual(result.read_bytes(),raw)
            row.sha256='0'*64
            with self.assertRaisesRegex(ValueError,'checksum'):
                self.source._online(self.pkg,device)
            row.sha256=hashlib.sha256(raw).hexdigest()
            row.size+=1
            with self.assertRaisesRegex(ValueError,'size mismatch'):
                self.source._online(self.pkg,device)

    def test_multiple_missing_defaults_are_captured_and_merged_in_one_pass(self):
        task=object.__new__(InstalledDependencyBackup)
        task.guard=self.task.guard
        task.reader=Mock()
        task.transfer=Mock()
        task.progress=Mock()
        task._check_space=Mock()
        first='etc/uci-defaults/first';second='etc/uci-defaults/second'
        task.reader.run.side_effect=['missing\t/'+first+'\nmissing\t/'+second+'\n4', '', '1', 'a'*64, '']
        payload=self.root/'captured.tgz'
        task.transfer.download.side_effect=lambda r,p,*args:p.write_bytes(blob({'etc/config/curl':b'user'}))
        recovered=self.root/'recovery.tgz'
        recovered.write_bytes(blob({first:b'one',second:b'two'}))
        recover=Mock(return_value=recovered)
        with patch('modules.capture_services.openwrt.installed_dependency_backup.parse_directory_metadata',return_value={}):
            task._capture('/tmp/private',[first,second,'etc/config/curl'],payload,self.root,recover=recover)
        recover.assert_called_once_with(['/'+first,'/'+second])
        with tarfile.open(payload) as archive:
            self.assertEqual(set(archive.getnames()),{first,second,'etc/config/curl'})

    def test_complete_backup_roundtrip_with_consumed_script(self):
        from modules.capture_services.openwrt.dependency_snapshot_archive import load_snapshot
        task=object.__new__(InstalledDependencyBackup)
        task.guard=self.task.guard
        task.reader=Mock()
        task.transfer=Mock()
        task.progress=Mock()
        self.inventory['builtin']={}
        self.inventory['database_hash']='unchanged'
        task.reader.inventory.return_value=self.inventory
        task._workspace=Mock(return_value=self.source.remote)
        task._cleanup=Mock()
        task._unchanged=Mock()
        task._check_space=Mock()
        self.source.task=task
        self.source.original=Mock(return_value=self.ipk)
        def command(action, *args):
            return {'files':'missing\t/etc/uci-defaults/curl\n8',
                'capture':'', 'archive_size':'1', 'archive_hash':'a'*64,
                'directory_metadata':'/etc\t755 0 0 0\n/etc/config\t755 0 0 0\n/etc/uci-defaults\t755 0 0 0\n/usr\t755 0 0 0\n/usr/bin\t755 0 0 0'}[action]
        task.reader.run.side_effect=command
        task.transfer.download.side_effect=lambda remote,path,*args:path.write_bytes(
            blob({'etc/config/curl':b'user-config','usr/bin/curl':b'executable'}))
        target=self.root/'backup.zip'
        with patch('modules.capture_services.openwrt.installed_dependency_backup.SnapshotOriginalPackages',return_value=self.source):
            result=task.backup(target)
        self.assertEqual(result['stored'],1)
        extracted=self.root/'restored';extracted.mkdir()
        manifest=load_snapshot(target,extracted,lambda:None)
        self.assertEqual(manifest['packages'][0]['version'],'1-r0')
        self.assertGreater(inspect_package(extracted/'curl.opkg','opkg',manifest['packages'][0]),0)
        task._cleanup.assert_called_once()

    def test_many_consumed_files_do_not_expand_ssh_exec_request(self):
        self.inventory['manager']='apk'
        paths={'etc/uci-defaults/'+str(i)+'-'+'x'*80 for i in range(150)}
        self.pkg.files=tuple(paths)
        self.task.transfer.read.return_value=blob({path:b'init' for path in paths})
        self.source.original=Mock(return_value=self.ipk)
        self.source.recover(paths)
        command=self.task.transfer.read.call_args.args[0]
        self.assertLess(len(command.encode('utf-8')),1024)
        listing=self.root/'curl.defaults.paths'
        self.assertEqual(set(listing.read_text(encoding='utf-8').splitlines()),paths)
        self.assertNotIn(b'\r',listing.read_bytes())

    def test_third_party_firmware_does_not_use_official_online_original(self):
        device = self.source._device(self.pkg)
        device.distribution = 'CustomWrt'
        with self.assertRaisesRegex(ValueError,'this firmware'):
            self.source._online(self.pkg,device)
