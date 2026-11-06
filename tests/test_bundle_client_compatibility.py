"""Signed resources remain extensible without silently expanding install requests."""
import base64
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from modules.app_update_services.manifest import canonical
from modules.capture_services.openwrt.bundle_compatibility import (
    BundleClientUpdateRequired, BundleCoverageUnavailable, require_client_version)
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog, CommonBundleRepository
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from tests.test_common_offline_bundle import fixture


def signing_key():
    private=Ed25519PrivateKey.generate()
    public=base64.b64encode(private.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
    def sign(payload):
        return canonical(dict(payload=payload,signature=base64.b64encode(private.sign(canonical(payload))).decode()))
    return public,sign


class BundleCompatibilityTests(unittest.TestCase):
    def test_additional_signed_roots_and_objects_work_for_both_package_formats(self):
        from modules.capture_services.openwrt.dependency_resolution import InstalledPackages
        for apk in [False,True]:
            with TemporaryDirectory() as folder:
                f=fixture(Path(folder)/'source',apk=apk)
                catalog=copy.deepcopy(f.catalog)
                catalog['roots'].append('future-tool')
                p=catalog['profiles'][0];p['roots'].append('future-tool')
                row=copy.deepcopy(p['packages'][0]);blob=b'extra-tool-fixture'
                sha=hashlib.sha256(blob).hexdigest()
                row.update(name='future-tool',filename='future-tool-1'+('.apk' if apk else '.ipk'),
                           sha256=sha,size=len(blob),blob='objects/'+sha)
                p['packages'].append(row)
                raw=canonical(catalog);blobs=dict(f.blobs);blobs[sha]=blob
                with zipfile.ZipFile(f.archive,'w',zipfile.ZIP_DEFLATED) as z:
                    z.writestr('catalog.json',raw)
                    for digest,content in blobs.items():z.writestr('objects/'+digest,content)
                payload=dict(f.payload)
                digest=hashlib.sha256(f.archive.read_bytes()).hexdigest()
                payload.update(sha256=digest,size=f.archive.stat().st_size,catalog_sha256=hashlib.sha256(raw).hexdigest(),
                               catalog_size=len(raw),unpacked_size=len(raw)+sum(map(len,blobs.values())),
                               url=f.payload['url'].replace(f.payload['sha256'],digest))
                public,sign=signing_key();root=Path(folder)/'resource'
                prepare_archive(f.archive,sign(payload),root,Event(),public_key=public)
                repo=CommonBundleRepository(root,public)
                profile=repo.profile(f.device)
                self.assertIn('future-tool',profile['roots'])
                if not apk:
                    chosen=repo.ipk_selection(profile,['tmux'],InstalledPackages({'libc':'1'}))
                    self.assertEqual([r.name for r in chosen],['tmux'])

    def test_missing_required_root_is_an_update_bundle_error_even_with_an_extra_root(self):
        with TemporaryDirectory() as folder:
            f=fixture(folder);data=copy.deepcopy(f.catalog)
            data['roots'].remove('curl');data['roots'].append('future-tool')
            p=data['profiles'][0];p['roots']=list(data['roots'])
            row=copy.deepcopy(p['packages'][0]);row['name']='future-tool'
            p['packages'].append(row)
            with self.assertRaisesRegex(BundleCoverageUnavailable,'\u66f4\u65b0\u79bb\u7ebf'):
                validate_catalog(data)

    def test_declared_extra_root_must_have_matching_profile_and_package(self):
        with TemporaryDirectory() as folder:
            f=fixture(folder);data=copy.deepcopy(f.catalog)
            data['roots'].append('future-tool')
            with self.assertRaises(ValueError):validate_catalog(data)
            data['profiles'][0]['roots'].append('future-tool')
            with self.assertRaises(ValueError):validate_catalog(data)

    def test_minimum_version_is_optional_and_compares_alpha_and_stable(self):
        require_client_version({},'1.0.0')
        require_client_version({'min_app_version':'1.0.44_alpha'},'1.0.44')
        with self.assertRaisesRegex(BundleClientUpdateRequired,'\u66f4\u65b0\u8f6f\u4ef6'):
            require_client_version({'min_app_version':'1.0.44'},'1.0.44_alpha')
        for bad in [None,True,'unknown','1.0.0; command']:
            with self.assertRaises(ValueError):require_client_version({'min_app_version':bad},'1.0.44')

    def test_signed_manifest_minimum_version_explains_update_application(self):
        with TemporaryDirectory() as folder:
            f=fixture(folder);public,sign=signing_key()
            payload=dict(f.payload,min_app_version='2.0.0')
            with self.assertRaisesRegex(BundleClientUpdateRequired,'2.0.0'):
                verify_bundle_manifest(sign(payload),public,current_version='1.0.44_alpha')
            self.assertEqual(verify_bundle_manifest(sign(payload),public,current_version='2.0.0')['min_app_version'],'2.0.0')
            envelope=json.loads(sign(payload));envelope['payload']['min_app_version']='1.0.0'
            with self.assertRaisesRegex(ValueError,'\u7b7e\u540d'):
                verify_bundle_manifest(canonical(envelope),public,current_version='1.0.44_alpha')

    def test_newer_catalog_format_explains_update_application(self):
        with TemporaryDirectory() as folder:
            f=fixture(folder);f.catalog['schema_version']=2
            with self.assertRaisesRegex(BundleClientUpdateRequired,'\u66f4\u65b0\u8f6f\u4ef6'):
                validate_catalog(f.catalog)

    def test_local_status_displays_the_application_update_reason(self):
        from modules.capture_services.openwrt.bundle_status import local_bundle_status
        with TemporaryDirectory() as folder:
            f=fixture(Path(folder)/'source');root=Path(folder)/'resource'
            prepare_archive(f.archive,f.envelope,root,Event(),public_key=f.public)
            public,sign=signing_key();payload=dict(f.payload,min_app_version='999.0.0')
            (root/'current.json').write_bytes(sign(payload))
            status=local_bundle_status(Event(),root=root,public_key=public)
            self.assertFalse(status['installed'])
            self.assertIn('\u66f4\u65b0\u8f6f\u4ef6',status['reason'])

    def test_publisher_signs_minimum_and_does_not_change_it_on_retry(self):
        from scripts.publishing import offline
        with TemporaryDirectory() as folder:
            f=fixture(Path(folder)/'source');public,sign=signing_key()
            f.archive.with_suffix('.report.json').write_bytes(canonical(dict(complete=True,
                zip_sha256=f.payload['sha256'],zip_bytes=f.payload['size'])))
            coverage=Path(folder)/'coverage.json'
            coverage.write_bytes(canonical(dict(catalog_sha256=f.payload['catalog_sha256'],
                profiles_verified=1,objects_verified=len(validate_catalog(f.catalog)))))
            def verify(raw,**kwargs):return verify_bundle_manifest(raw,public,**kwargs)
            with patch.object(offline,'ROOT',Path(folder)),patch.object(offline,'sign',side_effect=sign),patch.object(offline,'verify_bundle_manifest',side_effect=verify):
                archive,manifest,payload=offline.prepare('common',f.archive,coverage,min_app_version='1.0.0')
                self.assertEqual(payload['min_app_version'],'1.0.0')
                previous=manifest.read_bytes()
                with self.assertRaisesRegex(ValueError,'compatibility requirements'):
                    offline.prepare('common',f.archive,coverage,min_app_version='1.0.1')
                self.assertEqual(manifest.read_bytes(),previous)

    def test_failed_compatibility_check_does_not_leave_a_prepared_manifest(self):
        from scripts.publishing import offline
        with TemporaryDirectory() as folder:
            f=fixture(Path(folder)/'source');public,sign=signing_key()
            f.archive.with_suffix('.report.json').write_bytes(canonical(dict(complete=True,
                zip_sha256=f.payload['sha256'],zip_bytes=f.payload['size'])))
            coverage=Path(folder)/'coverage.json'
            coverage.write_bytes(canonical(dict(catalog_sha256=f.payload['catalog_sha256'],
                profiles_verified=1,objects_verified=len(validate_catalog(f.catalog)))))
            def verify(raw,**kwargs):return verify_bundle_manifest(raw,public,**kwargs)
            with patch.object(offline,'ROOT',Path(folder)),patch.object(offline,'sign',side_effect=sign),patch.object(offline,'verify_bundle_manifest',side_effect=verify):
                with self.assertRaises(BundleClientUpdateRequired):
                    offline.prepare('common',f.archive,coverage,min_app_version='999.0.0')
            self.assertFalse(list((Path(folder)/'build/publishing').rglob('manifest.json')))
