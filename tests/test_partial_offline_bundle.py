import base64
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from modules.app_update_services.manifest import canonical
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.bundle_status import local_bundle_status,compare_bundle
from tests.test_common_offline_bundle import fixture


def old_bundle(root,kind,apk):
    f=fixture(root/'source',apk=apk,kind=kind)
    missing='curl' if kind=='common' else 'pixiewps'
    f.catalog['roots'].remove(missing)
    for profile in f.catalog['profiles']:
        profile['roots'].remove(missing)
        profile['packages']=[r for r in profile['packages'] if r['name']!=missing]
    objects={r['sha256']:f.blobs[r['sha256']] for p in f.catalog['profiles'] for r in p['packages']+p['indexes']}
    raw=json.dumps(f.catalog,separators=(',',':')).encode()
    with zipfile.ZipFile(f.archive,'w') as z:
        z.writestr('catalog.json',raw)
        for sha,data in objects.items():z.writestr('objects/'+sha,data)
    digest=hashlib.sha256(f.archive.read_bytes()).hexdigest()
    payload=dict(f.payload,sha256=digest,size=f.archive.stat().st_size,catalog_sha256=hashlib.sha256(raw).hexdigest(),catalog_size=len(raw),unpacked_size=len(raw)+sum(map(len,objects.values())),url=f.payload['url'].replace(f.payload['sha256'],digest),published_at='2026-11-01T00:00:00+00:00')
    private=Ed25519PrivateKey.generate();public=base64.b64encode(private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
    envelope=canonical(dict(payload=payload,signature=base64.b64encode(private.sign(canonical(payload))).decode()))
    return f,missing,payload,public,envelope


class PartialOfflineBundleTests(unittest.TestCase):
    def test_signed_old_bundles_install_and_report_updates(self):
        for kind in ('common','capture'):
            for apk in (False,True):
                with self.subTest(kind=kind,apk=apk),tempfile.TemporaryDirectory() as folder:
                    root=Path(folder); f,missing,payload,public,envelope=old_bundle(root,kind,apk)
                    target=root/'resources';prepare_archive(f.archive,envelope,target,Event(),public_key=public,kind=kind)
                    status=local_bundle_status(Event(),kind=kind,root=target,public_key=public)
                    self.assertTrue(status['installed']);self.assertEqual(compare_bundle(status,payload),'current')
                    self.assertEqual(compare_bundle(status,dict(sha256='f'*64,published_at='2026-11-02T00:00:00+00:00')),'update')
                    repo=CommonBundleRepository(target,public,kind)
                    self.assertFalse(install_from_local(Mock(),(missing,),f.device,root/'cache',True,Event(),Mock(),repository=repo))
                    if not apk:
                        available='tmux' if kind=='common' else 'reaver'
                        manager=SimpleNamespace(command_runner=Mock(),_execute_stage=Mock(return_value='libc - 1\n'),_install_offline=Mock())
                        self.assertTrue(install_from_local(manager,(available,),f.device,root/'cache',True,Event(),Mock(),repository=repo))
                        manager._install_offline.assert_called_once()
                        manager._install_offline.reset_mock()
                        self.assertFalse(install_from_local(manager,(available,missing),f.device,root/'cache',True,Event(),Mock(),repository=repo))
                        manager._install_offline.assert_called_once()

    def test_publisher_rejects_incomplete_new_release(self):
        from scripts.publishing.offline import prepare
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);f,missing,payload,public,envelope=old_bundle(root,'common',False)
            f.archive.with_suffix('.report.json').write_text(json.dumps({'complete':True,'zip_sha256':payload['sha256'],'zip_bytes':payload['size']}),encoding='utf-8')
            coverage=root/'coverage.json';coverage.write_text('{}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'cover all current bundle roots'):
                prepare('common',f.archive,coverage)
