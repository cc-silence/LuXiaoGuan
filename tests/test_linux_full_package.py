"""Adversarial full-ZIP cases and independent worker revalidation."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from modules.app_update_services.manifest import Release,canonical
from modules.app_update_services import linux_full_package as package
from modules.app_update_services import linux_update_jobs as jobs
from modules.app_update_services.linux_install_plan import byte_state
from modules.platform_services.linux_recovery_launcher import LAUNCHER,SCRIPT

PRIMARY=b'\x7fELF\x02\x01\x01'+b'\0'*9+b'\x03\x00\x3e\x00'+b'native-test-fixture'


def archive(extra=None, change=None):
    contents={'LuXiaoGuan':PRIMARY,'resources/lib.so':b'library'}
    entries=[]
    for path,data in contents.items():
        sha=hashlib.sha256(data).hexdigest()
        entries.append(dict(path=path,kind='file',mode=0o755 if path=='LuXiaoGuan' else 0o644,
                            sha256=sha,size=len(data),blob_sha256=sha,blob_size=len(data)))
    entries.append(dict(path='resources/current.so',kind='symlink',target='lib.so'))
    baseline=dict(schema=2,product='RouterManager',platform='linux-x64',version='1.0.28_alpha',files=entries,retired=[],migrations=[])
    members=[(name,data,stat.S_IFREG|(0o755 if name=='LuXiaoGuan' else 0o644)) for name,data in contents.items()]
    members.extend([(LAUNCHER,SCRIPT,stat.S_IFREG|0o755),('resources/current.so',b'lib.so',stat.S_IFLNK|0o777)])
    if extra:members.extend(extra)
    if change:change(baseline,members)
    members.append((package.BASELINE,canonical(baseline),stat.S_IFREG|0o644))
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as zipped:
        for name,data,mode in members:
            info=zipfile.ZipInfo('LuXiaoGuan/'+name);info.create_system=3;info.external_attr=mode<<16
            zipped.writestr(info,data)
    raw=output.getvalue()
    release=Release(version='1.0.28_alpha',notes='',url='https://example.invalid/files/linux-x64/package.zip',
                    sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),published_at='2026-10-30T12:00:00+08:00',platform='linux-x64')
    return raw,release


class FullLinuxPackageTests(unittest.TestCase):
    def test_modes_links_and_complete_inventory_are_retained(self):
        raw,release=archive();value=package.inventory(io.BytesIO(raw),release)
        entries={e['path']:e for e in value['files']}
        self.assertEqual(entries['LuXiaoGuan']['mode'],0o755)
        self.assertEqual(entries['resources/lib.so']['mode'],0o644)
        self.assertEqual(entries['resources/current.so']['target'],'lib.so')
        self.assertEqual(value['delivery_mode'],'full-package')
    def test_signed_zip_tampering_is_rejected(self):
        raw,release=archive()
        changed=bytearray(raw);changed[10]^=1
        with self.assertRaisesRegex(ValueError,'fingerprint'):package.inventory(io.BytesIO(changed),release)
        with self.assertRaisesRegex(ValueError,'signed size'):package.inventory(io.BytesIO(raw+b'changed'),release)
    def test_wrong_platform_is_rejected(self):
        from dataclasses import replace
        raw,release=archive()
        with self.assertRaisesRegex(ValueError,'Linux signed'):package.inventory(io.BytesIO(raw),replace(release,platform='win-x64'))
    def test_path_traversal_foreign_executable_and_special_files_are_rejected(self):
        for name,data,mode in [('../outside',b'x',stat.S_IFREG|0o644),('resources/a.dll',b'x',stat.S_IFREG|0o644),
                               ('resources/fifo',b'',stat.S_IFIFO|0o644),('resources/unsafe',b'x',stat.S_IFREG|0o777)]:
            with self.subTest(name=name):
                raw,release=archive([(name,data,mode)])
                with self.assertRaises(ValueError):package.inventory(io.BytesIO(raw),release)
    def test_escaping_and_cyclic_links_are_rejected(self):
        for target in ['../../outside','current.so']:
            def mutate(b,m):
                b['files'][-1]['target']=target
                m[-1]=('resources/current.so',target.encode(),stat.S_IFLNK|0o777)
            raw,release=archive(change=mutate)
            with self.subTest(target=target),self.assertRaises(ValueError):package.inventory(io.BytesIO(raw),release)
    def test_duplicate_members_are_rejected(self):
        raw,release=archive([('resources/lib.so',b'other',stat.S_IFREG|0o644)])
        with self.assertRaisesRegex(ValueError,'duplicate'):package.inventory(io.BytesIO(raw),release)
    def test_bundled_baseline_is_verified_against_actual_zip_contents(self):
        def mutate(b,m):b['files'][0]['sha256']='a'*64
        raw,release=archive(change=mutate)
        with self.assertRaisesRegex(ValueError,'bundled baseline'):package.inventory(io.BytesIO(raw),release)
    def test_foreign_native_binary_and_changed_launcher_are_rejected(self):
        for name in ['LuXiaoGuan',LAUNCHER]:
            def mutate(b,m):
                index=next(i for i,row in enumerate(m) if row[0]==name)
                m[index]=(name,b'MZ-not-linux',m[index][2])
            raw,release=archive(change=mutate)
            with self.subTest(name=name),self.assertRaises(ValueError):package.inventory(io.BytesIO(raw),release)
    def test_cancellation_and_free_space_are_enforced(self):
        raw,release=archive()
        with self.assertRaises(InterruptedError):package.inventory(io.BytesIO(raw),release,cancel=lambda:True)
        with self.assertRaisesRegex(ValueError,'space'):package.inventory(io.BytesIO(raw),release,free_bytes=0)
    def test_worker_rederives_catalog_and_rejects_cached_catalog_edits(self):
        raw,release=archive();catalog=package.inventory(io.BytesIO(raw),release)
        class Store:
            def read_bytes(self,name,limit):return b'signed' if name=='manifest.json' else canonical(catalog)
            def open_read(self,name):return io.BytesIO(raw)
        stage=Path(hashlib.sha256(canonical(catalog)).hexdigest())
        with patch.object(jobs,'verify_manifest',return_value=release):
            self.assertEqual(jobs.signed_catalog(Store(),stage)[1],catalog)
            catalog['retired']=['resources/user-file']
            with self.assertRaisesRegex(ValueError,'canonical'):jobs.signed_catalog(Store(),stage)
    @unittest.skipUnless(os.name=='posix','Requires native Linux rooted file operations')
    def test_native_rooted_staging_reuses_verified_files_and_rejects_replacement(self):
        from modules.platform_services.linux_update_files import RootedFiles
        raw,release=archive()
        with tempfile.TemporaryDirectory() as directory,RootedFiles(directory) as files:
            first=package.inventory(io.BytesIO(raw),release,store=files)
            self.assertEqual(package.inventory(io.BytesIO(raw),release,store=files),first)
            entry=next(e for e in first['files'] if e['kind']=='file');name=entry['sha256']+'.file'
            files.unlink(name);Path(directory,name).symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):package.inventory(io.BytesIO(raw),release,store=files)


from tests.test_linux_transaction import LinuxInstallFixture,host_storage,tx


class CompletePackageTransactionTests(LinuxInstallFixture,unittest.TestCase):
    def setUp(self):self.setup_install_fixture()
    def prepare_full(self):
        raw,release=archive()
        with tx.RootedFiles(self.stage) as files:
            catalog=package.inventory(io.BytesIO(raw),release,store=files)
        destination=self.stage.with_name(hashlib.sha256(canonical(catalog)).hexdigest())
        self.stage.rename(destination);self.stage=destination
        return catalog
    def test_complete_package_retires_known_old_files_and_preserves_user_data(self):
        with host_storage():
            catalog=self.prepare_full()
            result=tx.apply(self.root,self.stage,catalog)
        self.assertEqual(result['status'],'complete')
        self.assertEqual((self.root/'LuXiaoGuan').read_bytes(),PRIMARY)
        self.assertFalse((self.root/'resources/retired.dat').exists())
        self.assertFalse((self.root/'resources/library.dat').exists())
        self.assertEqual((self.root/'config/settings.ini').read_bytes(),b'user settings')
        self.assertEqual((self.root/'images/custom.png').read_bytes(),b'user image')
    def test_failed_full_package_startup_restores_old_files_and_baseline(self):
        def failed():raise RuntimeError('full-package startup failed')
        with host_storage():
            catalog=self.prepare_full()
            with self.assertRaisesRegex(RuntimeError,'startup failed'):tx.apply(self.root,self.stage,catalog,validate=failed)
        self.assert_old()
        self.assertFalse((self.root/'resources/lib.so').exists())
        self.assertFalse((self.root/'resources/current.so').exists())



from contextlib import ExitStack
import sys
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock
from tests.test_linux_update_pipeline import Response,ModelPartial
from modules.app_update_services import linux_full_package_transfer as transfer


class FullPackageDownloadTests(LinuxInstallFixture,unittest.TestCase):
    def setUp(self):self.setup_install_fixture()
    def model(self,release):
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(host_storage())
        stack.enter_context(patch.object(transfer,'RootedFiles',tx.RootedFiles))
        if sys.platform=='win32':
            stack.enter_context(patch.object(transfer,'ownership'))
            stack.enter_context(patch.object(transfer,'RootedPartial',ModelPartial))
        stack.enter_context(patch.object(transfer,'verify_manifest',return_value=release))
        stack.enter_context(patch('modules.app_update_services.linux_update_cache.prune_completed'))
        self.helper=stack.enter_context(patch.object(transfer,'prepare_helper'))
        session=stack.enter_context(patch('requests.Session')).return_value.__enter__.return_value
        self.worker=NS(root=self.root,manifest=b'signed-fixture',cancel_event=threading.Event(),
                       channels=[NS(name='direct',proxy_url='')],progress=Mock())
        return session
    def test_download_uses_only_full_zip_then_reuses_verified_ready_cache(self):
        raw,release=archive();session=self.model(release);session.get.return_value=Response(raw)
        result=transfer.download(self.worker)
        self.assertTrue(result['full_package']);stage=Path(result['stage'])
        with tx.RootedFiles(stage) as store:
            with patch.object(jobs,'verify_manifest',return_value=release):
                verified=jobs.signed_catalog(store,stage)[1]
            self.assertEqual(stage.name,hashlib.sha256(canonical(verified)).hexdigest())
        self.helper.assert_called_once();self.assertEqual(session.get.call_args.args[0],release.url)
        again=transfer.download(self.worker)
        self.assertEqual(again['stage'],result['stage']);session.get.assert_called_once()
    def test_interrupted_zip_resumes_exact_bytes_in_a_new_download_task(self):
        import requests
        raw,release=archive();session=self.model(release);offset=len(raw)//2
        class Broken(Response):
            def iter_content(self,size):
                yield raw[:offset]
                raise requests.ConnectionError('interrupted fixture')
        session.get.return_value=Broken(raw)
        with self.assertRaisesRegex(RuntimeError,'routes failed'):transfer.download(self.worker)
        self.helper.assert_not_called()
        resumed=Response(raw[offset:]);resumed.status_code=206
        resumed.headers={'Content-Range':'bytes '+str(offset)+'-'+str(len(raw)-1)+'/'+str(len(raw))}
        session.get.return_value=resumed
        self.worker.cancel_event=threading.Event()
        result=transfer.download(self.worker)
        self.assertEqual(session.get.call_args.kwargs['headers']['Range'],'bytes='+str(offset)+'-')
        self.assertTrue(result['full_package']);self.helper.assert_called_once()


class PartialPackageCacheTests(LinuxInstallFixture,unittest.TestCase):
    def setUp(self):self.setup_install_fixture()
    def test_obsolete_declared_partial_is_reclaimed_but_unknown_files_are_preserved(self):
        from modules.app_update_services import linux_partial_cache as cache
        raw,release=archive();stage=self.stage.with_name(release.sha256);stage.mkdir()
        with host_storage(),patch.object(cache,'RootedFiles',tx.RootedFiles),patch.object(cache,'verify_manifest',return_value=release):
            with tx.RootedFiles(stage) as files:
                files.write_bytes('manifest.json',b'signed-fixture')
                files.write_json(cache.INTENT,cache.intent(release,stage))
                files.write_bytes('package.zip',raw[:len(raw)//2])
                files.write_bytes('my-private-file',b'must survive')
            self.assertFalse(cache.prune_partial(stage,release.version))
            self.assertEqual((stage/'my-private-file').read_bytes(),b'must survive')
            with tx.RootedFiles(stage) as files:files.unlink('my-private-file')
            self.assertTrue(cache.prune_partial(stage,release.version));self.assertFalse(stage.exists())
    def test_pending_release_is_not_pruned_without_matching_signed_identity(self):
        from modules.app_update_services import linux_partial_cache as cache
        raw,release=archive();stage=self.stage.with_name(release.sha256);stage.mkdir()
        with host_storage(),patch.object(cache,'RootedFiles',tx.RootedFiles),patch.object(cache,'verify_manifest',return_value=release):
            with tx.RootedFiles(stage) as files:
                files.write_bytes('manifest.json',b'signed-fixture')
                files.write_json(cache.INTENT,cache.intent(release,stage))
            self.assertFalse(cache.prune_partial(stage,'1.0.20_alpha'))
            self.assertTrue(stage.exists())

if __name__=='__main__':unittest.main()
