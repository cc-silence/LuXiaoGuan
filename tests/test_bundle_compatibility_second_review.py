"""Second review: cross-kind readers, publication identity and service messages."""
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
import warnings
import zipfile
from modules.app_update_services.manifest import canonical
from modules.capture_services.openwrt.bundle_compatibility import BundleClientUpdateRequired
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
from tests.test_common_offline_bundle import fixture
from tests.test_bundle_client_compatibility import signing_key


class CrossKindReaderTests(unittest.TestCase):
    def test_minimum_and_signature_rules_for_both_bundle_kinds_and_formats(self):
        for kind in ['common','capture']:
            for apk in [False,True]:
                with self.subTest(kind=kind,apk=apk),TemporaryDirectory() as folder:
                    f=fixture(folder,kind=kind,apk=apk);public,sign=signing_key()
                    p=dict(f.payload,min_app_version='1.0.44_alpha')
                    self.assertEqual(verify_bundle_manifest(sign(p),public,kind,current_version='1.0.44')['min_app_version'],p['min_app_version'])
                    with self.assertRaises(BundleClientUpdateRequired):
                        verify_bundle_manifest(sign(p),public,kind,current_version='1.0.43')
                    other='capture' if kind=='common' else 'common'
                    with self.assertRaises(ValueError):verify_bundle_manifest(sign(p),public,other,current_version='1.0.44')
                    raw=json.loads(sign(p));raw['payload']['min_app_version']='999.0.0'
                    with self.assertRaises(ValueError) as failure:verify_bundle_manifest(canonical(raw),public,kind)
                    self.assertNotIsInstance(failure.exception,BundleClientUpdateRequired)

    def test_future_catalog_minimum_is_enforced_for_both_kinds(self):
        for kind in ['common','capture']:
            with self.subTest(kind=kind),TemporaryDirectory() as folder:
                f=fixture(folder,kind=kind)
                self.assertTrue(validate_catalog(f.catalog,kind))
                f.catalog['min_app_version']='999.0.0'
                with self.assertRaises(BundleClientUpdateRequired):validate_catalog(f.catalog,kind)


@contextmanager
def publisher_fixture():
    from scripts.publishing import offline
    with TemporaryDirectory() as folder:
        base=Path(folder);f=fixture(base/'source');public,sign=signing_key()
        def reports():
            f.archive.with_suffix('.report.json').write_bytes(canonical(dict(complete=True,
                zip_sha256=hashlib.sha256(f.archive.read_bytes()).hexdigest(),zip_bytes=f.archive.stat().st_size)))
            coverage=base/'coverage.json'
            coverage.write_bytes(canonical(dict(catalog_sha256=f.payload['catalog_sha256'],
                profiles_verified=1,objects_verified=len(validate_catalog(f.catalog)))))
            return coverage
        def verify(raw,**kwargs):return verify_bundle_manifest(raw,public,**kwargs)
        with patch.object(offline,'ROOT',base),patch.object(offline,'sign',side_effect=sign),patch.object(offline,'verify_bundle_manifest',side_effect=verify):
            yield offline,f,reports


class PublicationBoundaryTests(unittest.TestCase):
    def test_renamed_retry_cannot_reuse_another_asset_filename(self):
        with publisher_fixture() as (offline,f,reports):
            coverage=reports();_,manifest,_=offline.prepare('common',f.archive,coverage)
            previous=manifest.read_bytes()
            f.archive=f.archive.rename(f.archive.with_name(f.archive.stem+'_renamed.zip'))
            coverage=reports()
            with self.assertRaises(ValueError):offline.prepare('common',f.archive,coverage)
            self.assertEqual(manifest.read_bytes(),previous)

    def test_duplicate_zip_entry_is_rejected_before_manifest_creation(self):
        with publisher_fixture() as (offline,f,reports):
            with zipfile.ZipFile(f.archive,'a') as z, warnings.catch_warnings():
                warnings.simplefilter('ignore',UserWarning)
                z.writestr('catalog.json',z.read('catalog.json'))
            coverage=reports()
            with self.assertRaises(ValueError):offline.prepare('common',f.archive,coverage)
            self.assertFalse(list((f.archive.parent.parent/'build/publishing').rglob('manifest.json')))


class CompatibilityMessageTests(unittest.TestCase):
    def test_both_update_directions_reach_service_signals_without_download_offer(self):
        from modules.capture_services.common_bundle_service import CommonBundleDownloadService
        from modules.capture_services.task_worker import CaptureTaskWorker
        from tests.test_bundle_updates import BundleServiceUpdateTests
        for kind in ['common','capture']:
            for direction in ['application','bundle']:
                with self.subTest(kind=kind,direction=direction),TemporaryDirectory() as folder:
                    owner,_=BundleServiceUpdateTests().service()
                    service=CommonBundleDownloadService(owner,kind)
                    service.active=True;service.phase='checking';service.cancel_event=Event()
                    service.local_status=dict(installed=True,label='retained local version')
                    service.offer={'obsolete':True}
                    f=fixture(folder,kind=kind);public,sign=signing_key()
                    if direction=='application':
                        raw=sign(dict(f.payload,min_app_version='999.0.0'))
                        def operation(cancel):return verify_bundle_manifest(raw,public,kind)
                        expected='\u66f4\u65b0\u8f6f\u4ef6'
                    else:
                        catalog=copy.deepcopy(f.catalog);missing=catalog['roots'].pop()
                        for profile in catalog['profiles']:
                            profile['roots'].remove(missing)
                            profile['packages']=[row for row in profile['packages'] if row['name']!=missing]
                        def operation(cancel):return validate_catalog(catalog,kind)
                        expected='\u66f4\u65b0\u79bb\u7ebf'
                    messages=[];done=[];offers=[];worker_done=[]
                    owner.dependencyMessageGenerated.connect(lambda *args:messages.append(args))
                    service.finished.connect(lambda *args:done.append(args))
                    service.offerReady.connect(offers.append)
                    worker=CaptureTaskWorker(operation,service.cancel_event)
                    worker.failed.connect(service._failed)
                    worker.finished.connect(lambda:worker_done.append(True))
                    worker.run()
                    self.assertEqual(worker_done,[True]);self.assertEqual(len(done),1)
                    self.assertFalse(done[0][0]);self.assertIn(expected,done[0][1])
                    self.assertEqual(messages[-1][1],'error');self.assertIn(expected,messages[-1][0])
                    self.assertEqual(offers,[]);self.assertIsNone(service.offer)
                    self.assertFalse(service.active);self.assertEqual(service.phase,'idle')
                    self.assertTrue(service.local_status['installed'])
