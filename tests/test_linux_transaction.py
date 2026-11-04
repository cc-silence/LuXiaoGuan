"""Transactional state tests use Windows file IO models; native CI uses real dirfds."""
from contextlib import contextmanager, ExitStack
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
from modules.app_update_services import linux_transaction as tx
from modules.app_update_services.manifest import canonical
from modules.app_update_services.linux_install_plan import byte_state, names
from modules.platform_services.local_rename import rename_without_replace
from tests.test_linux_release_layout import elf_bytes

MODES={}
LINKS={}


class WindowsFilesModel:
    """Model transaction observations only, without claiming Unix fd/fsync checks."""
    def __init__(self,root):self.root=Path(root)
    def __enter__(self):return self
    def __exit__(self,*_):pass
    @property
    def identity(self):
        info=self.root.stat();return [info.st_dev,info.st_ino]
    def path(self,relative):
        path=self.root/relative
        if not path.resolve().is_relative_to(self.root.resolve()):raise ValueError('Escaping model path')
        for p in path.parents:
            if p==self.root:break
            if p.is_symlink():raise ValueError('Linked model parent')
        return path
    def state(self,relative,check=lambda:None,*,max_bytes=2*1024**3):
        check();path=self.path(relative)
        if not path.exists() and not path.is_symlink():return None
        info=path.lstat()
        if (info.st_dev,info.st_ino) in LINKS:return dict(kind='symlink',target=LINKS[info.st_dev,info.st_ino])
        if path.is_symlink():return dict(kind='symlink',target=os.readlink(path))
        if not path.is_file():raise ValueError('Special model file')
        if info.st_size>max_bytes:raise ValueError('Excessive model file')
        mode=MODES.get((info.st_dev,info.st_ino),0o644)
        return byte_state(path.read_bytes(),mode)
    def read_bytes(self,relative,limit):
        value=self.path(relative).read_bytes()
        if len(value)>limit:raise ValueError('Excessive model record')
        return value
    @contextmanager
    def open_read(self,relative):
        with self.path(relative).open('rb') as stream:yield stream
    def create(self,relative,value,source=None,check=lambda:None):
        path=self.path(relative);path.parent.mkdir(parents=True,exist_ok=True)
        if value['kind']=='symlink':
            with path.open('xb') as output:output.write(b'model symlink; not native')
            info=path.stat();LINKS[info.st_dev,info.st_ino]=value['target'];return
        with path.open('xb') as output:
            try:
                digest=hashlib.sha256();size=0
                for block in iter(lambda:source.read(1024*1024),b''):
                    check();size+=len(block);digest.update(block);output.write(block)
                if size!=value['size'] or digest.hexdigest()!=value['sha256']:raise ValueError('Wrong model source')
                output.flush();os.fsync(output.fileno())
            except BaseException:
                output.close();path.unlink();raise
        info=path.stat();LINKS.pop((info.st_dev,info.st_ino),None);MODES[info.st_dev,info.st_ino]=value['mode']
    def rename(self,source,destination):rename_without_replace(self.path(source),self.path(destination))
    def unlink(self,relative):self.path(relative).unlink()
    @contextmanager
    def parent(self,relative,create=False):
        path=self.path(relative)
        if create:path.parent.mkdir(parents=True,exist_ok=True)
        yield None,path.name
    def rmdir(self,relative):self.path(relative).rmdir()
    def list_names(self,limit=24000):
        names=[p.name for p in self.root.iterdir()]
        if len(names)>limit:raise ValueError('Excessive model directory')
        return names
    def flush(self,relative):pass
    def write_json(self,relative,value,limit=32*1024*1024):
        return self.write_bytes(relative,canonical(value),limit)
    def write_bytes(self,relative,data,limit=32*1024*1024):
        if len(data)>limit:raise ValueError('Excessive journal')
        path=self.path(relative);tmp=path.with_name('.record-'+uuid.uuid4().hex)
        with tmp.open('xb') as output:output.write(data);output.flush();os.fsync(output.fileno())
        os.replace(tmp,path);info=path.stat();LINKS.pop((info.st_dev,info.st_ino),None);MODES[info.st_dev,info.st_ino]=0o600
    def read_json(self,relative,limit=32*1024*1024):return json.loads(self.read_bytes(relative,limit))


class SimulatedPowerLoss(BaseException):pass


@contextmanager
def host_storage():
    with ExitStack() as stack:
        stack.enter_context(patch.object(tx,'is_linux',return_value=True))
        if sys.platform=='win32':stack.enter_context(patch.object(tx,'RootedFiles',WindowsFilesModel))
        yield


class LinuxInstallFixture:
    def setup_install_fixture(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();MODES.clear();LINKS.clear()
        self.old_data={'LuXiaoGuan':elf_bytes()+b'old','resources/library.dat':b'old lib',
                       'resources/retired.dat':b'old retired','themes/dark.qss':b'official dark'}
        self.new_data={'LuXiaoGuan':elf_bytes()+b'new','resources/library.dat':b'new lib',
                       'resources/added.dat':b'new data','themes/dark.qss':b'official dark'}
        for relative,data in self.old_data.items():self.write(self.root/relative,data,0o755 if relative=='LuXiaoGuan' else 0o644)
        self.old=self.catalog('1.0.20_alpha',self.old_data)
        self.new=self.catalog('1.0.21_alpha',self.new_data,['resources/retired.dat'])
        self.write(self.root/tx.BASELINE,canonical(self.old),0o644)
        self.stage=self.root/'.routermanager-update'/hashlib.sha256(canonical(self.new)).hexdigest();self.stage.mkdir(parents=True)
        self.stage_sources()
        self.write(self.root/'config/settings.ini',b'user settings',0o600)
        self.write(self.root/'images/custom.png',b'user image',0o644)

    def write(self,path,data,mode=0o644):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);path.chmod(mode)
        info=path.stat();LINKS.pop((info.st_dev,info.st_ino),None);MODES[info.st_dev,info.st_ino]=mode

    def catalog(self,version,data,retired=None):
        files=[]
        for path,value in sorted(data.items()):
            state=byte_state(value,0o755 if path=='LuXiaoGuan' else 0o644)
            files.append(dict(state,path=path,blob_sha256='b'*64,blob_size=1))
        return dict(schema=2,product='RouterManager',platform='linux-x64',version=version,
                    resource_revision=0,files=files,retired=retired or [],migrations=[],history=[])

    def stage_sources(self):
        for data in self.new_data.values():self.write(self.stage/(hashlib.sha256(data).hexdigest()+'.file'),data)

    def assert_old(self):
        for name,data in self.old_data.items():self.assertEqual((self.root/name).read_bytes(),data,name)
        self.assertFalse((self.root/'resources/added.dat').exists())
        self.assertEqual(json.loads((self.root/tx.BASELINE).read_bytes()),self.old)
        self.assertEqual((self.root/'config/settings.ini').read_bytes(),b'user settings')
        self.assertEqual((self.root/'images/custom.png').read_bytes(),b'user image')

class LinuxTransactionTests(LinuxInstallFixture,unittest.TestCase):
    def setUp(self):self.setup_install_fixture()

    def test_success_replaces_retires_and_keeps_user_files(self):
        with host_storage():result=tx.apply(self.root,self.stage,self.new)
        self.assertEqual(result['status'],'complete');self.assertFalse(result['cleanup_pending'])
        for name,data in self.new_data.items():self.assertEqual((self.root/name).read_bytes(),data)
        self.assertFalse((self.root/'resources/retired.dat').exists())
        self.assertEqual((self.root/'config/settings.ini').read_bytes(),b'user settings')
        self.assertEqual((self.root/'images/custom.png').read_bytes(),b'user image')
        self.assertFalse((self.stage.parent/'active.json').exists())
        self.assertFalse(list(self.root.rglob('.routermanager-*.old')))

    def test_failed_validation_restores_original_files_baseline_and_modes(self):
        with host_storage(),self.assertRaisesRegex(RuntimeError,'startup failed'):
            tx.apply(self.root,self.stage,self.new,validate=lambda:(_ for _ in ()).throw(RuntimeError('startup failed')))
        self.assert_old();self.assertFalse((self.stage.parent/'active.json').exists())
        with host_storage(),tx.RootedFiles(self.root) as files:
            self.assertEqual(files.state('LuXiaoGuan')['mode'],0o755)
        self.assertEqual(json.loads((self.stage/tx.JOURNAL).read_bytes())['state'],'rolled_back')

    def test_interruption_between_parking_and_publication_recovers_idempotently(self):
        cls=WindowsFilesModel if sys.platform=='win32' else tx.RootedFiles
        original=cls.rename;raised=[]
        def interrupted(store,source,destination):
            original(store,source,destination)
            if store.root==self.root and destination.endswith('.old') and not raised:
                raised.append(True);raise SimulatedPowerLoss()
        with host_storage(),patch.object(cls,'rename',interrupted),self.assertRaises(SimulatedPowerLoss):
            tx.apply(self.root,self.stage,self.new)
        self.assertTrue((self.stage.parent/'active.json').exists())
        with host_storage():
            self.assertEqual(tx.recover(self.root,self.stage)['status'],'rolled_back')
            self.assertEqual(tx.recover(self.root,self.stage)['status'],'rolled_back')
        self.assert_old()

    def test_concurrent_edit_during_failed_validation_is_preserved_and_recovery_blocks(self):
        def validate():
            self.write(self.root/'resources/library.dat',b'user edit during update')
            raise RuntimeError('startup failed')
        with host_storage(),self.assertRaises(tx.RecoveryConflict):tx.apply(self.root,self.stage,self.new,validate)
        self.assertEqual((self.root/'resources/library.dat').read_bytes(),b'user edit during update')
        self.assertTrue((self.stage.parent/'active.json').exists())
        journal=json.loads((self.stage/tx.JOURNAL).read_bytes())
        index=next(i for i,e in enumerate(journal['entries']) if e['path']=='resources/library.dat')
        old=names('resources/library.dat',journal['token'],index)[1]
        self.assertEqual((self.root/old).read_bytes(),b'old lib')

    def test_locally_modified_managed_file_is_rejected_before_journaling(self):
        self.write(self.root/'resources/library.dat',b'manual edit')
        with host_storage(),self.assertRaises(ValueError):tx.apply(self.root,self.stage,self.new)
        self.assertEqual((self.root/'resources/library.dat').read_bytes(),b'manual edit')
        self.assertFalse((self.stage/tx.JOURNAL).exists())

    def test_unchanged_official_theme_keeps_local_customization(self):
        self.write(self.root/'themes/dark.qss',b'custom theme')
        with host_storage():result=tx.apply(self.root,self.stage,self.new)
        self.assertEqual(result['preserved'],['themes/dark.qss'])
        self.assertEqual((self.root/'themes/dark.qss').read_bytes(),b'custom theme')

    def test_unowned_new_target_is_never_overwritten(self):
        self.write(self.root/'resources/added.dat',b'user original')
        with host_storage(),self.assertRaises(ValueError):tx.apply(self.root,self.stage,self.new)
        self.assertEqual((self.root/'resources/added.dat').read_bytes(),b'user original')
        self.assertFalse((self.stage/tx.JOURNAL).exists())

    def test_corrupt_download_fails_before_any_installed_file_changes(self):
        source=self.stage/(hashlib.sha256(self.new_data['resources/library.dat']).hexdigest()+'.file')
        source.write_bytes(b'broken download')
        with host_storage(),self.assertRaises(ValueError):tx.apply(self.root,self.stage,self.new)
        self.assert_old();self.assertFalse((self.stage/tx.JOURNAL).exists())

    def test_busy_lock_and_wrong_os_fail_before_transaction_writes(self):
        with host_storage(),patch.object(tx,'cache_lock') as lease:
            lease.return_value.__enter__.return_value=False
            with self.assertRaises(BlockingIOError):tx.apply(self.root,self.stage,self.new)
        with patch.object(tx,'is_linux',return_value=False),patch.object(tx,'RootedFiles') as opened:
            with self.assertRaises(OSError):tx.apply(self.root,self.stage,self.new)
            opened.assert_not_called()
        self.assert_old();self.assertFalse((self.stage/tx.JOURNAL).exists())

    def test_cancellation_after_first_install_restores_old_files(self):
        cancelled=[]
        def progress(phase,done,total):
            if phase=='installing' and done==1:cancelled.append(True)
        with host_storage(),self.assertRaises(InterruptedError):
            tx.apply(self.root,self.stage,self.new,cancel=lambda:bool(cancelled),progress=progress)
        self.assert_old()

    def test_catalog_stage_identity_mismatch_and_downgrade_are_rejected(self):
        changed=copy.deepcopy(self.new);changed['version']='1.0.19_alpha'
        with host_storage(),self.assertRaises(ValueError):tx.apply(self.root,self.stage,changed)
        stage=self.stage.parent/hashlib.sha256(canonical(changed)).hexdigest();stage.mkdir()
        with host_storage(),self.assertRaises(ValueError):tx.apply(self.root,stage,changed)
        self.assert_old()

    def test_tampered_journal_cannot_restore_configuration_paths(self):
        def interrupt(phase,done,total):
            if phase=='preparing':raise SimulatedPowerLoss()
        with host_storage(),self.assertRaises(SimulatedPowerLoss):tx.apply(self.root,self.stage,self.new,progress=interrupt)
        path=self.stage/tx.JOURNAL;journal=json.loads(path.read_bytes());journal['entries'][0]['path']='config/settings.ini'
        path.write_bytes(canonical(journal))
        with host_storage(),self.assertRaises(ValueError):tx.recover(self.root,self.stage)
        self.assert_old()

    def test_completed_commit_interruption_finishes_cleanup_without_reverting_new_version(self):
        cls=WindowsFilesModel if sys.platform=='win32' else tx.RootedFiles
        original=cls.write_json
        def interrupt(store,relative,value,*args,**kwargs):
            original(store,relative,value,*args,**kwargs)
            if relative==tx.JOURNAL and value.get('state')=='complete':raise SimulatedPowerLoss()
        with host_storage(),patch.object(cls,'write_json',interrupt),self.assertRaises(SimulatedPowerLoss):
            tx.apply(self.root,self.stage,self.new)
        with host_storage():self.assertEqual(tx.recover(self.root,self.stage)['status'],'complete')
        for name,data in self.new_data.items():self.assertEqual((self.root/name).read_bytes(),data)
        self.assertFalse((self.stage.parent/'active.json').exists())

    def test_existing_old_runtime_hardlink_keeps_its_original_bytes(self):
        snapshot=self.root/'old-runtime';snapshot.mkdir();os.link(self.root/'LuXiaoGuan',snapshot/'LuXiaoGuan')
        with host_storage():tx.apply(self.root,self.stage,self.new)
        self.assertEqual((snapshot/'LuXiaoGuan').read_bytes(),self.old_data['LuXiaoGuan'])
        self.assertEqual((self.root/'LuXiaoGuan').read_bytes(),self.new_data['LuXiaoGuan'])

    def test_missing_original_backup_stops_recovery_instead_of_reporting_success(self):
        def validate():
            journal=json.loads((self.stage/tx.JOURNAL).read_bytes())
            index=next(i for i,e in enumerate(journal['entries']) if e['path']=='resources/library.dat')
            old=names('resources/library.dat',journal['token'],index)[1];(self.root/old).unlink()
            raise RuntimeError('startup failed')
        with host_storage(),self.assertRaises(tx.RecoveryConflict):tx.apply(self.root,self.stage,self.new,validate)
        self.assertTrue((self.stage.parent/'active.json').exists())
        self.assertNotEqual(json.loads((self.stage/tx.JOURNAL).read_bytes())['state'],'rolled_back')

    def test_foreign_active_update_record_is_preserved(self):
        marker=dict(schema=1,platform='linux-x64',stage='f'*64,root=[0,0]);path=self.stage.parent/'active.json'
        path.write_bytes(canonical(marker))
        with host_storage(),self.assertRaises(tx.RecoveryConflict):tx.apply(self.root,self.stage,self.new)
        self.assertEqual(json.loads(path.read_bytes()),marker);self.assert_old()

    def test_windows_executable_bytes_cannot_be_installed_under_linux_name(self):
        self.new_data['LuXiaoGuan']=b'MZ'+bytes(64);self.new=self.catalog('1.0.21_alpha',self.new_data,['resources/retired.dat'])
        self.stage=self.stage.parent/hashlib.sha256(canonical(self.new)).hexdigest();self.stage.mkdir();self.stage_sources()
        with host_storage(),self.assertRaises(ValueError):tx.apply(self.root,self.stage,self.new)
        self.assert_old();self.assertFalse((self.stage/tx.JOURNAL).exists())

    def test_fresh_application_directory_can_be_populated_without_configuration_changes(self):
        fresh=self.root/'fresh-install';fresh.mkdir()
        catalog=self.catalog('1.0.21_alpha',self.new_data)
        stage=fresh/'.routermanager-update'/hashlib.sha256(canonical(catalog)).hexdigest();stage.mkdir(parents=True)
        for data in self.new_data.values():self.write(stage/(hashlib.sha256(data).hexdigest()+'.file'),data)
        with host_storage():result=tx.apply(fresh,stage,catalog)
        self.assertEqual(result['status'],'complete')
        for name,data in self.new_data.items():self.assertEqual((fresh/name).read_bytes(),data)
        self.assertFalse((fresh/'config').exists())

    def test_file_to_link_transition_and_validation_failure_restore_original_kind(self):
        self.new['files']=[entry if entry['path']!='resources/library.dat' else
                           dict(path='resources/library.dat',kind='symlink',target='added.dat') for entry in self.new['files']]
        self.stage=self.stage.parent/hashlib.sha256(canonical(self.new)).hexdigest();self.stage.mkdir();self.stage_sources()
        def validate():
            with tx.RootedFiles(self.root) as files:
                self.assertEqual(files.state('resources/library.dat'),dict(kind='symlink',target='added.dat'))
            raise RuntimeError('failed linked runtime validation')
        with host_storage(),self.assertRaisesRegex(RuntimeError,'linked runtime'):
            tx.apply(self.root,self.stage,self.new,validate)
        self.assert_old()

    def test_link_to_regular_transition_preserves_other_link_destination(self):
        with host_storage(),tx.RootedFiles(self.root) as files:
            files.create('resources/alias',dict(kind='symlink',target='library.dat'))
        self.old['files'].append(dict(path='resources/alias',kind='symlink',target='library.dat'))
        self.write(self.root/tx.BASELINE,canonical(self.old))
        self.new_data['resources/alias']=b'new regular alias';self.new=self.catalog('1.0.21_alpha',self.new_data,['resources/retired.dat'])
        self.stage=self.stage.parent/hashlib.sha256(canonical(self.new)).hexdigest();self.stage.mkdir();self.stage_sources()
        with host_storage():tx.apply(self.root,self.stage,self.new)
        self.assertEqual((self.root/'resources/alias').read_bytes(),b'new regular alias')
        self.assertEqual((self.root/'resources/library.dat').read_bytes(),b'new lib')

    def test_interruption_after_publication_restores_all_applied_files(self):
        def interrupt(phase,done,total):
            if phase=='installing' and done==2:raise SimulatedPowerLoss()
        with host_storage(),self.assertRaises(SimulatedPowerLoss):tx.apply(self.root,self.stage,self.new,progress=interrupt)
        with host_storage():self.assertEqual(tx.recover(self.root,self.stage)['status'],'rolled_back')
        self.assert_old()

    def test_recreated_target_between_parking_and_publication_is_not_overwritten(self):
        cls=WindowsFilesModel if sys.platform=='win32' else tx.RootedFiles;original=cls.rename
        def concurrent(store,source,destination):
            original(store,source,destination)
            if store.root==self.root and source=='resources/library.dat' and destination.endswith('.old'):
                self.write(self.root/'resources/library.dat',b'user recreated file')
        with host_storage(),patch.object(cls,'rename',concurrent),self.assertRaises(tx.RecoveryConflict):
            tx.apply(self.root,self.stage,self.new)
        self.assertEqual((self.root/'resources/library.dat').read_bytes(),b'user recreated file')
        self.assertTrue((self.stage.parent/'active.json').exists())
        journal=json.loads((self.stage/tx.JOURNAL).read_bytes())
        index=next(i for i,e in enumerate(journal['entries']) if e['path']=='resources/library.dat')
        self.assertEqual((self.root/names('resources/library.dat',journal['token'],index)[1]).read_bytes(),b'old lib')

    def test_validation_callback_cannot_silently_commit_a_changed_runtime(self):
        def validate():self.write(self.root/'resources/library.dat',b'changed while validating')
        with host_storage(),self.assertRaises(tx.RecoveryConflict):tx.apply(self.root,self.stage,self.new,validate)
        self.assertEqual((self.root/'resources/library.dat').read_bytes(),b'changed while validating')
        self.assertNotEqual(json.loads((self.stage/tx.JOURNAL).read_bytes())['state'],'complete')

    def test_recovery_journal_must_include_its_bound_baseline(self):
        def interrupt(phase,done,total):raise SimulatedPowerLoss()
        with host_storage(),self.assertRaises(SimulatedPowerLoss):tx.apply(self.root,self.stage,self.new,progress=interrupt)
        path=self.stage/tx.JOURNAL;journal=json.loads(path.read_bytes());journal['entries'].pop();path.write_bytes(canonical(journal))
        with host_storage(),self.assertRaises(ValueError):tx.recover(self.root,self.stage)
        self.assert_old()

    def test_linux_backend_never_opens_legacy_winbox_update_cache(self):
        legacy=self.root/'.winbox-update'/self.stage.name;legacy.mkdir(parents=True)
        with host_storage(),self.assertRaises(ValueError):tx.apply(self.root,legacy,self.new)
        self.assertFalse((legacy.parent/'install.lock').exists());self.assert_old()
