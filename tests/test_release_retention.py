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
        self.verifier=patch.object(storage,'verify_manifest',return_value=type('Release',(),{'version':'1.0.8_alpha','url':'https://example.invalid/'+key(8),'size':70,'sha256':'8'*64})())
        self.verifier.start();self.addCleanup(self.verifier.stop)

    def clean(self): return retention.cleanup_retention(api=self.api,report_path=self.report)

    def test_all_old_app_zips_removed_even_below_quota(self):
        report=self.clean()
        self.assertEqual(self.api.deleted,[key(v) for v in range(3,8)])
        self.assertNotIn(key(7),self.api.rows);self.assertIn(key(8),self.api.rows)
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

    def test_one_bundle_per_kind_and_one_app_per_slot(self):
        with self.bundles(), patch.object(storage,'verify_bundle_manifest',side_effect=lambda raw,**_:json.loads(raw)):report=self.clean()
        for kind in ['common','capture']:
            keys=[k for k in self.api.rows if k.startswith('offline/openwrt-'+kind+'/files/')]
            self.assertEqual(len(keys),1);self.assertIn('/'+'b'*64+'/',keys[0])
        self.assertEqual(len(report['deleted']),9)

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
        self.assertEqual(len([k for k in self.api.rows if k.startswith('files/')]),1)


class PlatformRetentionTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        from modules.app_update_services.release_targets import APPLICATION_CHANNELS
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.api = API()
        self.api.rows.clear()
        self.api.channels.clear()
        self.current = {}
        self.old = []
        self.slot_verifier = patch.object(storage, "verify_manifest",
            side_effect=lambda raw, public_key, expected_platform=None:
                SimpleNamespace(**json.loads(raw)))
        self.verify_mock = self.slot_verifier.start()
        self.addCleanup(self.slot_verifier.stop)
        self.shared = "blobs/" + "f" * 64 + ".z"
        self.api.rows[self.shared] = dict(key=self.shared, size=10)
        for number, (channel, (platform, lane)) in enumerate(APPLICATION_CHANNELS.items()):
            prefix = "files/" if platform == "win-x64" else "files/" + platform + "/"
            suffix = "_alpha" if lane == "alpha" else ""
            for version in (1, 2):
                digest = format(number * 2 + version, "x") * 64
                name = "LuXiaoGuan_v1.0." + str(version) + suffix + "_20261025_100000.zip"
                archive = prefix + digest + "/" + name
                self.api.rows[archive] = dict(key=archive, size=70, etag=digest)
                if version == 1:
                    self.old.append(archive)
                else:
                    payload = dict(version="1.0.2" + suffix, platform=platform, sha256=digest,
                                   size=70, url="https://example.invalid/" + archive)
                    self.current[channel] = archive
                    raw = json.dumps(payload).encode()
                    self.api.channels[channel] = raw
                    self.api.rows[channel] = dict(key=channel, size=len(raw))

    def clean(self):
        return retention.cleanup_retention(api=self.api,
            report_path=Path(self.temp.name) / "retention.json")

    def test_four_slots_keep_only_current_and_never_cross_delete(self):
        report = self.clean()
        self.assertEqual(set(self.api.deleted), set(self.old))
        self.assertEqual(set(k for k in self.api.rows if k.startswith("files/")),
                         set(self.current.values()))
        self.assertIn(self.shared, self.api.rows)
        self.assertEqual(report["app_versions_per_platform_channel"], 1)
        self.assertEqual(report["app_slots"], 4)
        self.assertFalse(self.clean()["deleted"])
        expected = [storage.APPLICATION_CHANNELS[k][0] for k in self.current]
        called = [call.kwargs["expected_platform"] for call in self.verify_mock.call_args_list[:4]]
        self.assertEqual(called, expected)

    def test_missing_current_archive_blocks_all_deletion(self):
        self.api.rows.pop(next(iter(self.current.values())))
        with self.assertRaises(ValueError):
            self.clean()
        self.assertEqual(self.api.deleted, [])

    def test_wrong_platform_or_channel_in_current_manifest_blocks_cleanup(self):
        for field, value in (("version", "1.0.2_alpha"), ("url", "https://example.invalid/files/unknown.zip")):
            channel = "channel/stable.json"
            original = self.api.channels[channel]
            payload = json.loads(original)
            payload[field] = value
            self.api.channels[channel] = json.dumps(payload).encode()
            with self.assertRaises(ValueError):
                self.clean()
            self.assertEqual(self.api.deleted, [])
            self.api.channels[channel] = original
