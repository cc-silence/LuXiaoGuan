"""Owned ZIP cleanup keeps reusable resources and external archives intact."""
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch, Mock
from tests.test_common_offline_bundle import fixture
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
from modules.capture_services.openwrt.common_bundle_download import download_bundle, prepare_archive
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
from modules.capture_services.openwrt.bundle_archive_cleanup import cleanup_repository_archive

DOWNLOAD='modules.capture_services.openwrt.common_bundle_download'

class BundleArchiveCleanupTests(unittest.TestCase):
    def test_download_discards_only_owned_zip_after_activation(self):
        for kind in ('common', 'capture'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root=Path(folder); f=fixture(root/'source',kind=kind); target=root/'resource'
                def receive(url,path,*args):
                    Path(path).write_bytes(f.archive.read_bytes())
                with patch(DOWNLOAD+'._receive',side_effect=receive), patch(DOWNLOAD+'.verify_bundle_manifest',side_effect=lambda raw,**kw:verify_bundle_manifest(raw,f.public,**kw)):
                    result=download_bundle(Event(),Mock(),Mock(),root=target,kind=kind,manifest=f.envelope)
                self.assertEqual(result['archive'],'')
                self.assertFalse(list(target.rglob('*.zip')))
                self.assertTrue(f.archive.is_file())
                repo=CommonBundleRepository(target,f.public,kind)
                self.assertTrue(repo._load())
                self.assertEqual(repo.object_path(f.profile['packages'][0]).read_bytes(),f.blobs[f.profile['packages'][0]['sha256']])

    def test_failed_preparation_keeps_zip_and_previous_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); f=fixture(root/'source'); target=root/'resource'
            target.mkdir(); (target/'current.json').write_bytes(b'old receipt')
            def receive(url,path,*args):Path(path).write_bytes(f.archive.read_bytes())
            with patch(DOWNLOAD+'._receive',side_effect=receive), patch(DOWNLOAD+'.verify_bundle_manifest',side_effect=lambda raw,**kw:verify_bundle_manifest(raw,f.public,**kw)),patch(DOWNLOAD+'.prepare_archive',side_effect=ValueError('failed')):
                with self.assertRaises(ValueError):download_bundle(Event(),Mock(),Mock(),root=target,manifest=f.envelope)
            self.assertEqual((target/'current.json').read_bytes(),b'old receipt')
            self.assertEqual(len(list(target.rglob('*.zip'))),1)
            self.assertFalse(list(target.rglob('*.part')))

    def test_legacy_zip_requires_complete_resources_even_after_subset_install(self):
        for corrupt in (False,True):
            with self.subTest(corrupt=corrupt),tempfile.TemporaryDirectory() as folder:
                root=Path(folder); f=fixture(root/'source'); target=root/'resource'
                prepare_archive(f.archive,f.envelope,target,Event(),public_key=f.public)
                archive=target/'archives'/f.payload['sha256']/f.archive.name
                archive.parent.mkdir(parents=True); archive.write_bytes(f.archive.read_bytes())
                repo=CommonBundleRepository(target,f.public); repo._load()
                if corrupt:
                    repo.object_source(f.profile['packages'][0]).write_bytes(b'bad')
                cleanup_repository_archive(repo,Event(),Mock())
                self.assertEqual(archive.exists(),corrupt)
                self.assertTrue(f.archive.exists())

    def test_cleanup_permission_failure_does_not_fail_prepared_resource(self):
        from modules.capture_services.openwrt.bundle_archive_cleanup import discard_prepared_archive
        with tempfile.TemporaryDirectory() as folder:
            f=fixture(Path(folder)/'source'); warn=Mock()
            from modules.capture_services.openwrt.bundle_archive_paths import reserve_archive
            archive=reserve_archive(Path(folder)/'resource',f.payload,f.envelope,Event())
            archive.write_bytes(f.archive.read_bytes())
            with patch('pathlib.Path.unlink',side_effect=PermissionError('busy')):
                self.assertFalse(discard_prepared_archive(Path(folder)/'resource',f.payload,warn))
            warn.assert_called_once()
            self.assertEqual(warn.call_args.args[1],'warning')

    def test_cancel_does_not_remove_legacy_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); f=fixture(root/'source'); target=root/'resource'
            prepare_archive(f.archive,f.envelope,target,Event(),public_key=f.public)
            archive=target/'archives'/f.payload['sha256']/f.archive.name
            archive.parent.mkdir(parents=True); archive.write_bytes(f.archive.read_bytes())
            repo=CommonBundleRepository(target,f.public); repo._load(); cancel=Event(); cancel.set()
            cleanup_repository_archive(repo,cancel,Mock())
            self.assertTrue(archive.exists())

if __name__=='__main__':unittest.main()
