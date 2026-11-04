"""Native descriptor and Linux channel signing checks without credentials or network."""
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from tests import test_linux_release_layout as fixtures
from tests.test_linux_update_catalog import signed,PUBLIC
from scripts import linux_release_layout as layout
from scripts.publishing import application as publisher
from modules.platform_services.linux_update_admission import ADMISSION,NATIVE_MODULES
from modules.app_update_services.manifest import verify_manifest


class LinuxPublishingTests(unittest.TestCase):
    def setUp(self):
        fixtures.LinuxReleaseLayoutTests.setUp(self)
        p=self.package/ADMISSION;p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(dict(schema=1,platform='linux-x64',protocol=1,native_checks_passed=True,modules=list(NATIVE_MODULES))),encoding='utf-8');p.chmod(0o644)
        p=self.package/'resources/app-update-channel.json';p.write_bytes(b'{}');p.chmod(0o644)
        self.archive=self.root/('LuXiaoGuan_v'+self.version+'_linux-x64.zip')
        with fixtures.modes():
            layout.write_baseline(self.package,self.version);layout.make_zip(self.package,self.archive,self.version)
        self.descriptor=self.root/'release.json'
        self.data=dict(schema=1,product='RouterManager',platform='linux-x64',version=self.version,archive=self.archive.name,
                       size=self.archive.stat().st_size,sha256=publisher.sha256_file(self.archive),native_validated=True,desktop_validated=True)
        self.descriptor.write_text(json.dumps(self.data),encoding='utf-8')
    def prepare(self):
        with patch.object(publisher,'ROOT',self.root),patch.object(publisher,'metadata_path',return_value=self.descriptor),patch.object(publisher,'sign',side_effect=signed),patch.object(publisher,'PUBLIC_KEY',PUBLIC),patch.object(publisher,'gateway',return_value='https://example.invalid'):
            return publisher.prepare(self.archive,'fixture notes')
    def test_linux_full_package_is_signed_without_remote_catalog_or_blobs(self):
        archive,manifest,payload,objects=self.prepare()
        self.assertEqual(payload['platform'],'linux-x64');self.assertIn('/files/linux-x64/',payload['url'])
        release=verify_manifest(manifest.read_bytes(),PUBLIC,expected_platform='linux-x64')
        self.assertEqual(release.version,self.version);self.assertEqual(objects,{})
        self.assertEqual(payload["delivery_mode"],"full-package");self.assertEqual(release.catalog_url,"")
        self.assertNotIn("catalog_url",payload)
    def test_no_native_acceptance_or_modified_archive_cannot_be_prepared(self):
        self.data['desktop_validated']=False;self.descriptor.write_text(json.dumps(self.data),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'native desktop acceptance'):self.prepare()
        self.data['desktop_validated']=True;self.descriptor.write_text(json.dumps(self.data),encoding='utf-8')
        with self.archive.open('ab') as output:output.write(b'changed')
        with self.assertRaisesRegex(ValueError,'release descriptor'):self.prepare()
    def test_formal_linux_release_only_switches_linux_formal_slot_after_download_check(self):
        archive=self.root/'LuXiaoGuan_v1.0.22_linux-x64.zip';archive.write_bytes(b'archive')
        manifest=self.root/'manifest.json';manifest.write_bytes(b'signed')
        payload=dict(platform='linux-x64',version='1.0.22',sha256=publisher.sha256_file(archive),size=7,url='https://example.invalid/archive')
        release=NS(version='1.0.22',sha256=payload['sha256'],catalog_url='https://example.invalid/catalog',catalog_size=2)
        events=[]
        def receive(url,destination,*args,**kwargs):
            if destination:events.append('verified-zip');destination.write_bytes(b'archive');return
            return b'signed' if '/manifest' in url else b'{}'
        with patch('scripts.publishing.storage.ROOT',self.root),patch.object(publisher,'prepare',return_value=(archive,manifest,payload,{})),patch.object(publisher,'ensure_space'),patch.object(publisher,'upload',side_effect=lambda path,key,kind:events.append(key)),patch.object(publisher,'_receive',side_effect=receive),patch.object(publisher,'verify_manifest',return_value=release),patch.object(publisher,'parse_catalog'),patch.object(publisher,'cleanup_retention'),patch.object(publisher,'cleanup_publishing'),patch.object(publisher,'gateway',return_value='https://example.invalid'):
            result=publisher.publish(archive,'fixture')
        self.assertLess(events.index('verified-zip'),events.index('channel/linux-x64/stable.json'))
        self.assertNotIn('channel/stable.json',events)
        self.assertEqual(result['manifest_url'],'https://example.invalid/manifest?platform=linux-x64')


if __name__=='__main__':unittest.main()
