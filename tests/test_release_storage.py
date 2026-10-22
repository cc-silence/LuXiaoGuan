"""Publisher storage limits preserve channels, rollback ZIPs and shared blobs."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from scripts.publishing import storage


def key(version):
    return "files/"+str(version)*64+"/LuXiaoGuan_v1.0."+str(version)+"_alpha_20261022_100000.zip"


class API:
    def __init__(self):
        self.rows = {key(v): dict(key=key(v), size=70, etag=str(v)) for v in range(3, 9)}
        self.rows["blobs/"+"f"*64+".z"] = dict(key="blobs/"+"f"*64+".z",size=74)
        self.rows["channel/manifest.json"] = dict(key="channel/manifest.json",size=6)
        self.channels = {"channel/manifest.json": b"signed"}
        self.deleted = []
    def inventory(self): return copy.deepcopy(self.rows)
    def read(self, key): return self.channels[key]
    def delete(self, row): self.deleted.append(row["key"]); self.rows.pop(row["key"])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name)/"archive.zip"; self.archive.write_bytes(b"x"*70)
        self.manifest = Path(self.temp.name)/"manifest.json"; self.manifest.write_bytes(b"signed")
        self.api = API()
        self.verify = patch.object(storage, "verify_manifest", return_value=NS(version="1.0.8_alpha",url="https://example.invalid/"+key(8)))
        self.verify.start(); self.addCleanup(self.verify.stop)
    def ensure(self, **kwargs):
        return storage.ensure_space({}, self.archive, self.manifest, {"url":"https://example.invalid/"+key(9)},
            "channel/manifest.json", api=self.api, report_path=Path(self.temp.name)/"report.json", **kwargs)
    def test_below_limit_does_not_delete_anything(self):
        report=self.ensure(limit=1000)
        self.assertFalse(self.api.deleted)
        self.assertEqual(report["peak_bytes"],576)
    def test_over_limit_removes_only_enough_old_full_zips(self):
        report=self.ensure(limit=500)
        self.assertEqual(self.api.deleted,[key(3),key(4)])
        self.assertLessEqual(report["peak_bytes"],500)
        self.assertIn(key(7),self.api.rows); self.assertIn(key(8),self.api.rows)
        self.assertTrue(any(k.startswith("blobs/") for k in self.api.rows))
        self.assertEqual(len(report["deleted"]),2)
    def test_insufficient_safe_cleanup_stops_before_any_deletion(self):
        with self.assertRaises(ValueError): self.ensure(limit=100)
        self.assertFalse(self.api.deleted)
    def test_changed_channel_stops_before_deletion(self):
        self.api.read = lambda _: b"first" if not hasattr(self.api,"read_once") else b"changed"
        calls=[0]
        def read(_): calls[0]+=1; return b"first" if calls[0]==1 else b"changed"
        self.api.read=read
        with self.assertRaises(ValueError): self.ensure(limit=500)
        self.assertFalse(self.api.deleted)
    def test_unknown_channel_blocks_cleanup(self):
        self.api.rows["channel/foreign.json"]=dict(key="channel/foreign.json",size=1)
        with self.assertRaises(ValueError): self.ensure(limit=500)
        self.assertFalse(self.api.deleted)
    def test_immutable_size_conflict_never_triggers_deletion(self):
        self.api.rows[key(9)]=dict(key=key(9),size=999)
        with self.assertRaises(ValueError): self.ensure(limit=500)
        self.assertFalse(self.api.deleted)
    def test_channel_and_offline_bytes_count_toward_limit(self):
        self.api.rows["offline/openwrt-common/files/current.zip"] = dict(key="offline/openwrt-common/files/current.zip",size=1000)
        with self.assertRaises(ValueError): self.ensure(limit=1000)
        self.assertFalse(self.api.deleted)
    def test_publication_lock_rejects_concurrent_publisher(self):
        @storage.publication_lock
        def inner(): return True
        @storage.publication_lock
        def outer(): return inner()
        with patch.object(storage,"ROOT",Path(self.temp.name)), self.assertRaises(ValueError): outer()
        with patch.object(storage,"ROOT",Path(self.temp.name)): self.assertTrue(inner())


class StorageAPITests(unittest.TestCase):
    def api(self, pages):
        import json
        from unittest.mock import Mock
        from scripts.publishing.storage_api import StorageAPI
        api = StorageAPI.__new__(StorageAPI)
        api._request = Mock(side_effect=[json.dumps(page).encode() for page in pages])
        return api
    def test_inventory_follows_all_pages(self):
        api=self.api([{"success":True,"result":{"objects":[{"key":"one","size":1}],"cursor":"next","truncated":True}},
                      {"success":True,"result":{"objects":[{"key":"two","size":2}]}}])
        self.assertEqual(sum(row["size"] for row in api.inventory().values()),3)
        self.assertEqual(api._request.call_count,2)
    def test_duplicate_keys_and_missing_cursor_fail_closed(self):
        cases=[[{"success":True,"result":{"objects":[],"truncated":True}}],
               [{"success":True,"result":{"objects":[{"key":"one","size":1}],"cursor":"next"}},
                {"success":True,"result":{"objects":[{"key":"one","size":1}]}}]]
        for pages in cases:
            with self.assertRaises(ValueError):self.api(pages).inventory()

    def test_delete_uses_strong_quoted_etag(self):
        from scripts.publishing.storage_api import StorageAPI
        from unittest.mock import Mock
        api=StorageAPI.__new__(StorageAPI);api._request=Mock()
        api.delete({"key":key(3),"etag":"abc123"})
        self.assertEqual(api._request.call_args.kwargs["headers"],{"If-Match":'"abc123"'})
        with self.assertRaises(ValueError):api.delete({"key":key(3)})
