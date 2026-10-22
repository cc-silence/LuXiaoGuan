"""Retention must never delete active channels or shared update content."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.publishing import storage, retention
from tests.test_release_storage import API, key


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.api=API()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.report=Path(self.temp.name)/'retention.json'
        self.verifier=patch.object(storage,'verify_manifest',return_value=type('Release',(),{'version':'1.0.8_alpha','url':'https://example.invalid/'+key(8)})())
        self.verifier.start();self.addCleanup(self.verifier.stop)

    def clean(self): return retention.cleanup_retention(api=self.api,report_path=self.report)

    def test_all_old_app_zips_removed_even_below_quota(self):
        report=self.clean()
        self.assertEqual(self.api.deleted,[key(v) for v in range(3,7)])
        self.assertIn(key(7),self.api.rows);self.assertIn(key(8),self.api.rows)
        self.assertTrue(any(k.startswith('blobs/') for k in self.api.rows))
        self.assertEqual(report['status'],'complete')
        self.assertFalse(self.clean()['deleted'])

    def bundles(self):
        for kind in ['common','capture']:
            for digit,rev in [('a','20261021090000'),('b','20261021100000')]:
                sha=digit*64;archive='offline/openwrt-'+kind+'/files/'+sha+'/RouterManager_OpenWrt_'+kind.title()+'_Offline_fixture.zip'
                manifest='offline/openwrt-'+kind+'/manifests/'+sha+'.json'
                raw=json.dumps(dict(sha256=sha,url='https://example.invalid/'+archive,size=50,revision=rev)).encode()
                self.api.rows[archive]=dict(key=archive,size=50,etag=digit)
                self.api.rows[manifest]=dict(key=manifest,size=len(raw),etag=digit)
                self.api.channels[manifest]=raw
                if digit=='b':
                    channel='channel/openwrt-'+kind+'.json'
                    self.api.rows[channel]=dict(key=channel,size=len(raw))
                    self.api.channels[channel]=raw
        return patch.object(retention,'verify_bundle_manifest',side_effect=lambda raw,**_:json.loads(raw))

    def test_one_bundle_per_kind_and_two_app_versions(self):
        with self.bundles(), patch.object(storage,'verify_bundle_manifest',side_effect=lambda raw,**_:json.loads(raw)):report=self.clean()
        for kind in ['common','capture']:
            keys=[k for k in self.api.rows if k.startswith('offline/openwrt-'+kind+'/files/')]
            self.assertEqual(len(keys),1);self.assertIn('/'+'b'*64+'/',keys[0])
        self.assertEqual(len(report['deleted']),8)

    def test_failed_offline_signature_blocks_all_cleanup(self):
        self.bundles()
        with patch.object(retention,'verify_bundle_manifest',side_effect=ValueError('invalid signature')):
            with self.assertRaises(ValueError):self.clean()
        self.assertFalse(self.api.deleted)

    def test_changed_channel_stops_before_first_delete(self):
        original=self.api.read;calls=[0]
        def read(k):
            calls[0]+=1
            return original(k) if calls[0]==1 else b'changed'
        self.api.read=read
        with self.assertRaises(ValueError):self.clean()
        self.assertFalse(self.api.deleted)

    def test_only_owned_archive_keys_can_be_deleted(self):
        from scripts.publishing.storage_api import StorageAPI
        from unittest.mock import Mock
        api=StorageAPI.__new__(StorageAPI);api._request=Mock()
        for key in ['blobs/'+'a'*64+'.z','channel/manifest.json','files/private.zip','offline/foreign/files/test.zip']:
            with self.assertRaises(ValueError):api.delete(dict(key=key,etag='abc'))
        api._request.assert_not_called()

    def test_new_executable_names_fail_publication_contract(self):
        from scripts.app_update_catalog import validate_client_contract
        validate_client_contract(dict(files=[dict(path='LuXiaoGuan.exe')],retired=[]))
        for field in ['files','retired']:
            catalog=dict(files=[],retired=[])
            catalog[field]=[dict(path='other.exe')] if field=='files' else ['other.exe']
            with self.assertRaises(ValueError):validate_client_contract(catalog)

    def test_same_version_rebuild_keeps_only_signed_current_archive(self):
        duplicate=key(8).replace('_100000.zip','_110000.zip')
        self.api.rows[duplicate]=dict(key=duplicate,size=70,etag='duplicate')
        self.clean()
        self.assertIn(duplicate,self.api.deleted)
        self.assertIn(key(8),self.api.rows)
        self.assertEqual(len([k for k in self.api.rows if k.startswith('files/')]),2)
