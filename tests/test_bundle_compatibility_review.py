"""Three-pass review regressions for resource compatibility and failure commits."""
import copy
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from modules.capture_services.openwrt.bundle_compatibility import (
    current_app_version, require_client_version, require_schema, BundleClientUpdateRequired)


class ReaderContractTests(unittest.TestCase):
    def test_source_wrapper_uses_source_version(self):
        from main import APP_VERSION
        with patch.dict(sys.modules, {'__main__': NS()}):
            self.assertEqual(current_app_version(), APP_VERSION)

    def test_packaged_main_uses_embedded_version(self):
        with patch.dict(sys.modules, {'__main__': NS(APP_VERSION='1.2.3_alpha')}):
            self.assertEqual(current_app_version(), '1.2.3_alpha')

    def test_numeric_and_prerelease_version_boundaries(self):
        for required,current in [('1.0.9','1.0.10'),('1.0.0_rc2','1.0.0_rc3'),('1.0.0_alpha','1.0.0'),('1.0','1.0.0')]:
            require_client_version({'min_app_version':required}, current)
        for required,current in [('1.0.10','1.0.9'),('1.0.0','1.0.0_rc3')]:
            with self.assertRaises(BundleClientUpdateRequired):
                require_client_version({'min_app_version':required}, current)

    def test_schema_types_cannot_silently_act_as_version_one(self):
        require_schema(1)
        for value in [True,False,None,1.0,'1',{},[],-1]:
            with self.assertRaises(ValueError):require_schema(value)
        with self.assertRaises(BundleClientUpdateRequired):require_schema(2)


class ArchiveFailureTests(unittest.TestCase):
    def _rewrite(self, f, catalog, blobs):
        import hashlib, zipfile
        from modules.app_update_services.manifest import canonical
        raw=canonical(catalog)
        with zipfile.ZipFile(f.archive,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('catalog.json',raw)
            for sha,blob in blobs.items():z.writestr('objects/'+sha,blob)
        digest=hashlib.sha256(f.archive.read_bytes()).hexdigest()
        payload=dict(f.payload,sha256=digest,size=f.archive.stat().st_size,
                     catalog_sha256=hashlib.sha256(raw).hexdigest(),catalog_size=len(raw),
                     unpacked_size=len(raw)+sum(map(len,blobs.values())),
                     url=f.payload['url'].replace(f.payload['sha256'],digest))
        return payload

    def test_future_manifest_or_catalog_does_not_replace_a_working_bundle(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from threading import Event
        from tests.test_common_offline_bundle import fixture
        from tests.test_bundle_client_compatibility import signing_key
        from modules.capture_services.openwrt.common_bundle_download import prepare_archive
        from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
        for reason in ['manifest_minimum','catalog_minimum','catalog_schema']:
            with self.subTest(reason=reason), TemporaryDirectory() as folder:
                f=fixture(Path(folder)/'source');root=Path(folder)/'resources'
                public,sign=signing_key()
                prepare_archive(f.archive,sign(f.payload),root,Event(),public_key=public)
                before=(root/'current.json').read_bytes()
                catalog=copy.deepcopy(f.catalog)
                if reason=='catalog_minimum':catalog['min_app_version']='999.0.0'
                if reason=='catalog_schema':catalog['schema_version']=2
                payload=self._rewrite(f,catalog,f.blobs)
                if reason=='manifest_minimum':payload['min_app_version']='999.0.0'
                with self.assertRaises(BundleClientUpdateRequired):
                    prepare_archive(f.archive,sign(payload),root,Event(),public_key=public)
                self.assertEqual((root/'current.json').read_bytes(),before)
                self.assertIsNotNone(CommonBundleRepository(root,public).profile(f.device))

    def test_unused_added_object_is_still_checked_for_both_formats(self):
        import hashlib
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from threading import Event
        from tests.test_common_offline_bundle import fixture
        from tests.test_bundle_client_compatibility import signing_key
        from modules.capture_services.openwrt.common_bundle_download import prepare_archive
        for apk in [False,True]:
            with self.subTest(apk=apk), TemporaryDirectory() as folder:
                f=fixture(Path(folder)/'source',apk=apk);root=Path(folder)/'resources'
                public,sign=signing_key()
                prepare_archive(f.archive,sign(f.payload),root,Event(),public_key=public)
                before=(root/'current.json').read_bytes();catalog=copy.deepcopy(f.catalog)
                extra=b'unused-tool-payload';sha=hashlib.sha256(extra).hexdigest()
                catalog['roots'].append('future-tool')
                profile=catalog['profiles'][0];profile['roots'].append('future-tool')
                row=copy.deepcopy(profile['packages'][0])
                row.update(name='future-tool',filename='future-tool-1'+('.apk' if apk else '.ipk'),
                           sha256=sha,size=len(extra),blob='objects/'+sha)
                profile['packages'].append(row)
                payload=self._rewrite(f,catalog,dict(f.blobs,**{sha:b'x'*len(extra)}))
                with self.assertRaises(ValueError):
                    prepare_archive(f.archive,sign(payload),root,Event(),public_key=public)
                self.assertEqual((root/'current.json').read_bytes(),before)


class PublicationFailureTests(unittest.TestCase):
    def test_storage_failure_leaves_no_partial_manifest_and_retry_succeeds(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from tests.test_common_offline_bundle import fixture
        from tests.test_bundle_client_compatibility import signing_key
        from modules.app_update_services.manifest import canonical
        from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
        from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
        from scripts.publishing import offline
        with TemporaryDirectory() as folder:
            base=Path(folder);f=fixture(base/'source');public,sign=signing_key()
            f.archive.with_suffix('.report.json').write_bytes(canonical(dict(complete=True,
                zip_sha256=f.payload['sha256'],zip_bytes=f.payload['size'])))
            coverage=base/'coverage.json'
            coverage.write_bytes(canonical(dict(catalog_sha256=f.payload['catalog_sha256'],
                profiles_verified=1,objects_verified=len(validate_catalog(f.catalog)))))
            def verify(raw,**kwargs):return verify_bundle_manifest(raw,public,**kwargs)
            original_write=Path.write_bytes
            def failed_direct_write(path,data):
                if path.name=='manifest.json':
                    original_write(path,b'{')
                    raise OSError('simulated interrupted write')
                return original_write(path,data)
            with patch.object(offline,'ROOT',base),patch.object(offline,'sign',side_effect=sign),patch.object(offline,'verify_bundle_manifest',side_effect=verify):
                with patch.object(Path,'write_bytes',failed_direct_write),patch('modules.capture_services.openwrt.bundle_metadata.os.replace',side_effect=OSError('simulated commit failure')):
                    with self.assertRaises(OSError):
                        offline.prepare('common',f.archive,coverage,min_app_version='1.0.0')
                self.assertFalse(list((base/'build/publishing').rglob('manifest.json')))
                self.assertFalse(list((base/'build/publishing').rglob('.metadata-*')))
                _,manifest,payload=offline.prepare('common',f.archive,coverage,min_app_version='1.0.0')
                self.assertEqual(verify(manifest.read_bytes())['sha256'],payload['sha256'])
