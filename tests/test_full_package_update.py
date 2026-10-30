"""Full ZIP integrity, safe extraction, preservation and transactional rollback."""
import hashlib,json,tempfile,unittest,zipfile,os
from pathlib import Path
from unittest.mock import patch
from tests.test_app_updates import signed,PUBLIC,PAYLOAD
from modules.app_update_services.manifest import verify_manifest
from modules.app_update_services.full_package import prepare_package
from modules.app_update_services.transaction import apply,atomic_json
from modules.app_update_services.locations import CACHE_DIRECTORY

class FullPackageTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
 def archive(self,entries=None):
  path=self.root/'download.zip'
  with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as zipped:
   for name,data in (entries or {'LuXiaoGuan/LuXiaoGuan.exe':b'new app','LuXiaoGuan/resources/library.dll':b'new library','LuXiaoGuan/config/settings.ini':b'never overwrite'}).items():zipped.writestr(name,data)
  release=verify_manifest(signed(dict(PAYLOAD,size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())),PUBLIC)
  stage=self.root/CACHE_DIRECTORY/release.sha256;stage.mkdir(parents=True,exist_ok=True)
  (stage/'package.zip').write_bytes(path.read_bytes())
  return stage,release
 def test_apply_preserves_configuration_and_custom_files(self):
  stage,release=self.archive();(self.root/'config').mkdir();(self.root/'config/settings.ini').write_bytes(b'[user]\nx=1\n')
  (self.root/'notes.txt').write_bytes(b'private');(self.root/'LuXiaoGuan.exe').write_bytes(b'old app')
  catalog=prepare_package(stage/'package.zip',release,stage,self.root)
  with patch('modules.app_update_services.transaction.migrate_defaults'):apply(self.root,stage,catalog)
  self.assertEqual((self.root/'LuXiaoGuan.exe').read_bytes(),b'new app')
  self.assertEqual((self.root/'notes.txt').read_bytes(),b'private')
  self.assertEqual((self.root/'config/settings.ini').read_bytes(),b'[user]\nx=1\n')
 def test_startup_failure_rolls_back_existing_files(self):
  stage,release=self.archive();(self.root/'LuXiaoGuan.exe').write_bytes(b'old app')
  catalog=prepare_package(stage/'package.zip',release,stage,self.root)
  def fail():raise ValueError('startup failed')
  with patch('modules.app_update_services.transaction.migrate_defaults'):
   with self.assertRaises(ValueError):apply(self.root,stage,catalog,fail)
  self.assertEqual((self.root/'LuXiaoGuan.exe').read_bytes(),b'old app')
  self.assertFalse((self.root/'resources/library.dll').exists())
  self.assertFalse((stage.parent/'active.json').exists())
 def test_tampered_zip_rejected_before_extraction(self):
  stage,release=self.archive();(stage/'package.zip').write_bytes(b'bad')
  with self.assertRaises(ValueError):prepare_package(stage/'package.zip',release,stage,self.root)
  self.assertEqual(list(stage.glob('*.file')),[])
 def test_traversal_duplicate_case_and_links_rejected(self):
  for names in ({'LuXiaoGuan/../escape':b'x'},{'LuXiaoGuan/LuXiaoGuan.exe':b'x','LuXiaoGuan/luxiaoguan.EXE':b'y'}):
   stage,release=self.archive(names)
   with self.assertRaises(ValueError):prepare_package(stage/'package.zip',release,stage,self.root)
 def test_cancellation_stops_extraction(self):
  stage,release=self.archive()
  with self.assertRaises(InterruptedError):prepare_package(stage/'package.zip',release,stage,self.root,cancel=lambda:True)
 def test_finished_staging_is_cleaned_without_touching_user_files(self):
  from modules.app_update_services import cache
  stage,release=self.archive();catalog=prepare_package(stage/'package.zip',release,stage,self.root)
  (stage/'manifest.json').write_bytes(signed(dict(PAYLOAD,size=release.size,sha256=release.sha256)))
  atomic_json(stage/'catalog.json',catalog);atomic_json(stage/'journal.json',dict(state='complete',entries=[]))
  with patch.object(cache,'PUBLIC_KEY',PUBLIC):self.assertTrue(cache.cleanup_stage(stage,remove_metadata=True))
  self.assertFalse(stage.exists())


 def terminal_stage(self):
  from modules.app_update_services import cache
  stage,release=self.archive();catalog=prepare_package(stage/'package.zip',release,stage,self.root)
  (stage/'manifest.json').write_bytes(signed(dict(PAYLOAD,size=release.size,sha256=release.sha256)))
  atomic_json(stage/'catalog.json',catalog);atomic_json(stage/'journal.json',dict(state='complete',entries=[]))
  (stage/'RouterManagerUpdateWorker.exe').write_bytes(b'helper')
  return stage,catalog
 def test_locked_helper_does_not_keep_complete_zip_until_next_launch(self):
  from modules.app_update_services import cache
  stage,_=self.terminal_stage();original=Path.unlink
  def unlink(path,*args,**kwargs):
   if path.name=='RouterManagerUpdateWorker.exe':raise PermissionError('in use')
   return original(path,*args,**kwargs)
  with patch.object(cache,'PUBLIC_KEY',PUBLIC),patch.object(Path,'unlink',autospec=True,side_effect=unlink):
   self.assertFalse(cache.cleanup_stage(stage,remove_metadata=True))
  self.assertFalse((stage/'package.zip').exists());self.assertTrue((stage/'manifest.json').exists())
  with patch.object(cache,'PUBLIC_KEY',PUBLIC):self.assertTrue(cache.cleanup_stage(stage,remove_metadata=True))
  self.assertFalse(stage.exists())
 @unittest.skipUnless(os.name=='nt','Windows file sharing semantics')
 def test_real_windows_helper_lock_still_reclaims_zip(self):
  import ctypes
  from ctypes import wintypes
  from modules.app_update_services import cache
  stage,_=self.terminal_stage()
  kernel=ctypes.WinDLL('kernel32',use_last_error=True)
  kernel.CreateFileW.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
  kernel.CreateFileW.restype=wintypes.HANDLE
  kernel.CloseHandle.argtypes=(wintypes.HANDLE,);kernel.CloseHandle.restype=wintypes.BOOL
  handle=kernel.CreateFileW(str(stage/'RouterManagerUpdateWorker.exe'),0x80000000,0,None,3,0,None)
  self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
  try:
   with patch.object(cache,'PUBLIC_KEY',PUBLIC):self.assertFalse(cache.cleanup_stage(stage,remove_metadata=True))
   self.assertFalse((stage/'package.zip').exists())
   self.assertTrue((stage/'manifest.json').exists())
  finally:kernel.CloseHandle(handle)
  with patch.object(cache,'PUBLIC_KEY',PUBLIC):self.assertTrue(cache.cleanup_stage(stage,remove_metadata=True))
  self.assertFalse(stage.exists())
 def test_locked_payload_keeps_zip_for_authenticated_cleanup_retry(self):
  from modules.app_update_services import cache
  stage,catalog=self.terminal_stage();name=catalog['files'][0]['sha256']+'.file';original=Path.unlink
  def unlink(path,*args,**kwargs):
   if path.name==name:raise PermissionError('in use')
   return original(path,*args,**kwargs)
  with patch.object(cache,'PUBLIC_KEY',PUBLIC),patch.object(Path,'unlink',autospec=True,side_effect=unlink):
   self.assertFalse(cache.cleanup_stage(stage,remove_metadata=True))
  self.assertTrue((stage/'package.zip').exists())
  with patch.object(cache,'PUBLIC_KEY',PUBLIC):self.assertTrue(cache.cleanup_stage(stage,remove_metadata=True))
 def test_active_recovery_keeps_complete_package(self):
  from modules.app_update_services import cache
  stage,_=self.terminal_stage();atomic_json(stage/'journal.json',dict(state='applying',entries=[]))
  with patch.object(cache,'PUBLIC_KEY',PUBLIC):self.assertFalse(cache.cleanup_stage(stage,remove_metadata=True))
  self.assertTrue((stage/'package.zip').exists())
 def test_completed_cleanup_releases_zip_even_when_retaining_small_records(self):
  from modules.app_update_services import cache
  stage,_=self.terminal_stage()
  with patch.object(cache,'PUBLIC_KEY',PUBLIC):self.assertTrue(cache.cleanup_stage(stage))
  self.assertFalse((stage/'package.zip').exists());self.assertTrue((stage/'manifest.json').exists())
  (stage/'user.txt').write_bytes(b'preserve')
  with patch.object(cache,'PUBLIC_KEY',PUBLIC):self.assertFalse(cache.cleanup_stage(stage,remove_metadata=True))
  self.assertEqual((stage/'user.txt').read_bytes(),b'preserve')

class FullPackageFlowTests(unittest.TestCase):
 setUp=FullPackageTests.setUp
 archive=FullPackageTests.archive
 def test_transfer_downloads_one_whole_zip_and_stages_without_cloud_catalog(self):
  from modules.app_update_services.full_package_transfer import FullPackageTransfer
  stage,release=self.archive();raw=signed(dict(PAYLOAD,size=release.size,sha256=release.sha256))
  worker=FullPackageTransfer(self.root,raw);results=[];errors=[]
  worker.result.connect(results.append);worker.failed.connect(errors.append)
  def receive(url,target,limit,cancel,progress,deadline,**kwargs):
   self.assertEqual(url,release.url);self.assertEqual(kwargs['expected_sha'],release.sha256)
   target.write_bytes((self.root/'download.zip').read_bytes());progress(dict(completed=limit));return limit
  with patch('modules.app_update_services.full_package_transfer.PUBLIC_KEY',PUBLIC),patch('modules.platform_services.app_update_install.prepare_helper'),patch('modules.capture_services.openwrt.common_bundle_download._receive',side_effect=receive) as download:
   worker.run()
  self.assertEqual(errors,[]);self.assertEqual(len(results),1);self.assertTrue(results[0]['full_package'])
  self.assertEqual(results[0]['download_bytes'],release.size);download.assert_called_once()
 def test_full_installer_ignores_tampered_local_catalog_and_reverifies_zip(self):
  from modules.app_update_services import installer
  stage,release=self.archive();(self.root/'LuXiaoGuan.exe').write_bytes(b'old app')
  (stage/'manifest.json').write_bytes(signed(dict(PAYLOAD,size=release.size,sha256=release.sha256)))
  atomic_json(stage/'catalog.json',{'files':[{'path':'config/settings.ini'}]})
  atomic_json(stage/'job.json',dict(parent_pid=1,parent_executable='LuXiaoGuan.exe'))
  with patch.object(installer,'PUBLIC_KEY',PUBLIC),patch('modules.platform_services.app_update_install.wait_for_parent'),patch.object(installer.subprocess,'run',return_value=type('Result',(),{'returncode':0})()),patch.object(installer.subprocess,'Popen'),patch('modules.app_update_services.transaction.migrate_defaults'),patch('modules.app_update_services.cache.cleanup_stage',return_value=True):
   result=installer.run_install(stage/'job.json')
  self.assertEqual(result['status'],'applied');self.assertEqual((self.root/'LuXiaoGuan.exe').read_bytes(),b'new app')
 def test_finished_transfer_waits_for_explicit_restart_action(self):
  from PySide6.QtCore import QObject
  from modules.desktop_shell.app_update_presenter import AppUpdatePresenter
  runtime=QObject();runtime._exiting=False;runtime.main_window=None
  with patch('modules.platform_services.app_update_access.read_code',return_value=''):presenter=AppUpdatePresenter('2.0.8_alpha',runtime)
  presenter.state='ready';presenter.plan={'full_package':True};presenter._worker=type('Worker',(),{'deleteLater':lambda self:None})()
  with patch.object(presenter,'install_update') as install:presenter._transfer_finished()
  install.assert_not_called()
  self.assertEqual(presenter.state,'ready');self.assertIsNone(presenter._worker)
  self.assertTrue(presenter.plan['full_package'])
  from types import SimpleNamespace
  from modules.settings_pages.app_update_page import AppUpdatePage
  page=SimpleNamespace(presenter=presenter)
  with patch.object(presenter,'install_update') as install:AppUpdatePage._download(page)
  install.assert_called_once_with(page)
  presenter.stop();presenter.deleteLater();runtime.deleteLater()

if __name__=='__main__':unittest.main()
