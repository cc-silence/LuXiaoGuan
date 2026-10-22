"""Destructive cleanup is bounded to verified, reproducible local build data."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from scripts import build_cleanup as cleanup


class BuildCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name).resolve()
        (self.root/'build/releases').mkdir(parents=True)
        (self.root/'dist').mkdir()

    def stage(self,name,status='complete'):
        path=self.root/'build/releases'/name;path.mkdir()
        archive=self.root/'dist'/('LuXiaoGuan_v'+name+'.zip')
        with zipfile.ZipFile(archive,'w') as output:output.writestr('LuXiaoGuan/app.exe',b'fixture')
        data=dict(status=status,archive=str(archive),sha256=cleanup.digest(archive),zip_crc_verified=True)
        (path/'report.json').write_text(json.dumps(data),encoding='utf-8')
        (path/'assets').mkdir();(path/'assets/data').write_bytes(b'reproducible')
        return path,archive

    def test_only_latest_verified_stage_remains_and_dry_run_changes_nothing(self):
        old,_=self.stage('1.0.9_alpha_20261022_100000')
        latest,archive=self.stage('1.0.11_alpha_20261022_110000')
        running,_=self.stage('1.0.12_alpha_20261022_120000','building')
        unknown=self.root/'build/releases/user-notes';unknown.mkdir()
        preview=cleanup.cleanup_releases(self.root,apply=False)
        self.assertEqual(preview['retained'],str(latest));self.assertTrue(old.exists())
        cleanup.cleanup_releases(self.root)
        self.assertFalse(old.exists());self.assertTrue(latest.exists());self.assertTrue(running.exists())
        self.assertTrue(unknown.exists());self.assertTrue(archive.exists())
        self.assertFalse(cleanup.cleanup_releases(self.root)['paths'])

    def test_invalid_retained_archive_prevents_all_deletions(self):
        old,_=self.stage('1.0.9_alpha_20261022_100000')
        latest,archive=self.stage('1.0.11_alpha_20261022_110000')
        archive.write_bytes(b'corrupted')
        with self.assertRaises(ValueError):cleanup.cleanup_releases(self.root)
        self.assertTrue(old.exists());self.assertTrue(latest.exists())

    def test_failed_build_never_replaces_the_retained_success(self):
        success,_=self.stage('1.0.10_alpha_20261022_100000')
        failed,_=self.stage('1.0.11_alpha_20261022_110000','failed')
        cleanup.cleanup_releases(self.root)
        self.assertTrue(success.exists());self.assertFalse(failed.exists())

    def test_failed_retry_keeps_failure_report_and_drops_large_staging(self):
        success,_=self.stage('1.0.10_alpha_20261022_100000')
        failed,_=self.stage('1.0.11_alpha_20261022_110000','failed')
        (failed/'pyinstaller.log').write_text('failure evidence',encoding='ascii')
        cleanup.cleanup_failed_build(self.root,failed)
        self.assertTrue(success.exists());self.assertFalse(failed.exists())
        self.assertEqual((self.root/'build/last-build-failure/pyinstaller.log').read_text(),'failure evidence')

    def test_without_success_repeated_failures_keep_only_one_stage(self):
        old,_=self.stage('1.0.10_alpha_20261022_100000','failed')
        latest,_=self.stage('1.0.11_alpha_20261022_110000','failed')
        cleanup.cleanup_releases(self.root)
        self.assertFalse(old.exists());self.assertTrue(latest.exists())

    def test_concurrent_build_is_rejected_and_mutex_releases_after_error(self):
        calls=[]
        @cleanup.locked_build
        def nested():calls.append('called')
        @cleanup.locked_build
        def outer():
            with self.assertRaises(ValueError):nested()
            raise RuntimeError('fixture failure')
        with patch.object(cleanup,'ROOT',self.root):
            with self.assertRaises(RuntimeError):outer()
            nested()
        self.assertEqual(calls,['called'])

    def test_outside_paths_and_build_root_are_rejected(self):
        for path in (self.root,self.root/'build',self.root/'dist'):
            with self.subTest(path=path),self.assertRaises(ValueError):cleanup.remove_candidates(self.root,[path],True)
        self.assertTrue((self.root/'dist').exists())

    def test_credential_and_unknown_build_paths_cannot_be_removal_targets(self):
        for relative in ('publishing/cloudflare-config','release-metadata','user-notes'):
            path=self.root/'build'/relative;path.mkdir(parents=True,exist_ok=True)
            with self.subTest(path=path),self.assertRaises(ValueError):
                cleanup.remove_candidates(self.root,[path],True)
            self.assertTrue(path.exists())

    def test_publication_cleanup_keeps_metadata_credentials_and_one_blob_set(self):
        base=self.root/'build/publishing/application';base.mkdir(parents=True)
        old=base/('a'*64);latest=base/('b'*64)
        for p in (old,latest):
            (p/'blobs').mkdir(parents=True);(p/'blobs/object.z').write_bytes(b'fixture')
            (p/'manifest.json').write_text('signed',encoding='ascii')
        credentials=self.root/'build/publishing/cloudflare-config';credentials.mkdir()
        (credentials/'auth').write_bytes(b'do not delete')
        cleanup.cleanup_publishing(self.root,latest)
        self.assertFalse((old/'blobs').exists());self.assertTrue((old/'manifest.json').exists())
        self.assertTrue((latest/'blobs').exists());self.assertTrue((credentials/'auth').exists())

    def test_offline_cleanup_requires_verified_delivered_copy(self):
        folder=self.root/'build/openwrt-common-fixture';folder.mkdir()
        stage=folder/'offline.zip'
        with zipfile.ZipFile(stage,'w') as output:output.writestr('catalog.json','{}')
        data=dict(complete=True,zip_bytes=stage.stat().st_size,zip_sha256=cleanup.digest(stage))
        (folder/'report.json').write_text(json.dumps(data),encoding='utf-8')
        (folder/'inventory.json').write_text('{}',encoding='utf-8')
        (folder/'bundle').mkdir();(folder/'bundle/copy').write_bytes(b'reproducible')
        self.assertEqual(cleanup.offline_candidates(self.root,folder),[])
        target=self.root/'dist/openwrt_common/offline.zip';target.parent.mkdir();target.write_bytes(stage.read_bytes())
        cleanup.cleanup_offline(self.root,folder,target)
        self.assertFalse((folder/'bundle').exists());self.assertFalse(stage.exists())
        self.assertTrue(target.exists());self.assertTrue((folder/'inventory.json').exists())
        self.assertTrue((folder/'report.json').exists())
