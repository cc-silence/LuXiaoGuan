"""Full-package-only publishing and old-catalog client behavior."""
import hashlib,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import tests.test_app_updates as fixtures
from scripts.publishing import application

class ManualPublishingTests(unittest.TestCase):
 def test_preparation_never_generates_catalog_or_blobs(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);archive=root/'LuXiaoGuan_v2.0.9_alpha_20261030_080000.zip'
   with zipfile.ZipFile(archive,'w') as zipped:zipped.writestr('LuXiaoGuan/LuXiaoGuan.exe',b'fixture')
   descriptor=root/'descriptor.json';descriptor.write_text(json.dumps(dict(product='RouterManager',platform='win-x64',version='2.0.9_alpha',archive=archive.name,size=archive.stat().st_size,sha256=hashlib.sha256(archive.read_bytes()).hexdigest())),encoding='utf-8')
   with patch.object(application,'ROOT',root),patch.object(application,'metadata_path',return_value=descriptor),patch.object(application,'sign',fixtures.signed),patch.object(application,'PUBLIC_KEY',fixtures.PUBLIC):
    _,manifest,payload,objects=application.prepare(archive,'manual fixture')
   self.assertEqual(objects,{})
   self.assertEqual(payload['delivery_mode'],'full-package')
   self.assertNotIn('catalog_url',payload)
   self.assertEqual([p.name for p in manifest.parent.iterdir()],['manifest.json'])

class ManualCheckingTests(unittest.TestCase):
 def test_old_catalog_metadata_does_not_trigger_a_catalog_request(self):
  fixture=fixtures.NetworkTests('test_real_http_new_version_and_repeat_check')
  fixtures.NetworkTests.setUpClass();fixture.setUp()
  try:
   payload=dict(fixtures.PAYLOAD,catalog_url='https://unreachable.invalid/catalog.json',catalog_sha256='a'*64,catalog_size=100)
   raw=fixtures.signed(payload)
   with patch.object(fixtures,'signed',return_value=raw):result=fixture.run_check()
   self.assertEqual(result[0][0],'ok')
   self.assertIsNone(result[0][1].catalog)
   self.assertEqual(fixture.checker._stage,'manifest')
  finally:fixture.tearDown();fixtures.NetworkTests.tearDownClass()

class ManualPageTests(unittest.TestCase):
 def test_old_incremental_release_uses_browser_without_transfer_or_install(self):
  from configparser import ConfigParser
  from dataclasses import replace
  from PySide6.QtCore import QObject
  from modules.desktop_shell.app_update_presenter import AppUpdatePresenter
  from modules.settings_pages.app_update_page import AppUpdatePage
  runtime=QObject();runtime._exiting=False;runtime.main_window=None;runtime.config=ConfigParser()
  with patch('modules.platform_services.app_update_access.read_code',return_value=''):
   presenter=AppUpdatePresenter('2.0.8_alpha',runtime)
  page=AppUpdatePage(presenter)
  try:
   release=replace(fixtures.verify_manifest(fixtures.signed(),fixtures.PUBLIC),catalog={'files':[],'history':[]})
   presenter._inline_check=True;presenter._completed(release,True)
   with patch.object(presenter,'prepare_plan') as download,patch('PySide6.QtGui.QDesktopServices.openUrl') as browser:
    page.download_button.click()
   download.assert_called_once_with(download=True);browser.assert_not_called()

  finally:
   presenter.stop();page.close();page.deleteLater();runtime.deleteLater();fixtures.APP.processEvents()

if __name__=='__main__':unittest.main()
