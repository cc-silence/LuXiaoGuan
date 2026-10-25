"""Quota replacement never retires a formal release or another platform."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.publishing import alpha_space, storage
from modules.app_update_services.manifest import canonical
from tests.test_app_updates import KEY, PUBLIC


class API:
    def __init__(self):
        self.rows, self.contents, self.events = {}, {}, []

    def add(self, key, body):
        self.contents[key] = body
        self.rows[key] = dict(key=key, size=len(body), etag=hashlib.md5(body).hexdigest())

    def inventory(self):
        return copy.deepcopy(self.rows)

    def read(self, key):
        return self.contents[key]

    def put(self, path, key, kind):
        self.events.append(("put", key))
        self.add(key, Path(path).read_bytes())

    def delete(self, row):
        self.events.append(("delete", row["key"]))
        self.rows.pop(row["key"])
        self.contents.pop(row["key"])

    def close(self):
        pass


def signed(payload):
    return canonical(dict(payload=payload,
        signature=base64.b64encode(KEY.sign(canonical(payload))).decode()))


class AlphaQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = API()
        self.trust = patch.multiple(alpha_space, PUBLIC_KEY=PUBLIC)
        self.trust.start()
        self.addCleanup(self.trust.stop)
        self.storage_trust = patch.object(storage, "PUBLIC_KEY", PUBLIC)
        self.storage_trust.start()
        self.addCleanup(self.storage_trust.stop)
        self.channel = "channel/manifest.json"
        old_data, new_data = b"old-alpha" * 1000, b"new-alpha" * 1000
        self.old_key, old_payload = self.release(old_data, "1.0.1_alpha")
        self.previous = signed(old_payload)
        self.api.add(self.channel, self.previous)
        self.api.add(self.old_key, old_data)
        self.new_key, self.payload = self.release(new_data, "1.0.2_alpha")
        self.archive = self.root / self.new_key.rsplit("/", 1)[-1]
        self.archive.write_bytes(new_data)
        self.manifest = self.root / "manifest.json"
        self.manifest.write_bytes(signed(self.payload))
        stable_key, stable_payload = self.release(b"formal", "1.0.1")
        self.stable = stable_key
        self.api.add(stable_key, b"formal")
        self.api.add("channel/stable.json", signed(stable_payload))
        self.linux = "files/linux-x64/" + "f" * 64 + "/LuXiaoGuan_v1.0.1_alpha_20261025_100000.zip"
        self.api.add(self.linux, b"other-platform")
        self.limit = sum(row["size"] for row in self.api.rows.values()) + len(new_data) + self.manifest.stat().st_size - len(old_data) + 100

    def release(self, data, version):
        digest = hashlib.sha256(data).hexdigest()
        key = "files/" + digest + "/LuXiaoGuan_v" + version + "_20261025_100000.zip"
        payload = dict(schema=1, product="RouterManager", platform="win-x64", channel="alpha" if "alpha" in version else "stable",
            version=version, notes="synthetic", url="https://example.invalid/" + key,
            sha256=digest, size=len(data), published_at="2026-10-25T10:00:00+08:00")
        return key, payload

    def reserve(self, **kwargs):
        return alpha_space.reserve_alpha_space({}, self.archive, self.manifest,
            self.payload, self.previous, api=self.api, root=self.root,
            limit=kwargs.get("limit", self.limit))

    def test_ordinary_quota_fails_then_only_own_alpha_is_retired(self):
        with self.assertRaises(storage.StorageQuotaError):
            storage.ensure_space({}, self.archive, self.manifest, self.payload,
                self.channel, api=self.api, limit=self.limit,
                report_path=self.root / "normal.json")
        self.assertEqual(self.api.events, [])
        result = self.reserve()
        self.assertEqual(self.api.events, [("put", self.channel), ("delete", self.old_key)])
        self.assertIn(self.stable, self.api.rows)
        self.assertIn(self.linux, self.api.rows)
        self.assertEqual(result["state"], "ready")
        self.assertLessEqual(result["projected_peak_bytes"], self.limit)
        self.assertEqual(json.loads(self.api.read(self.channel))["state"], "publishing")

    def test_insufficient_even_after_retirement_never_mutates_cloud(self):
        with self.assertRaises(ValueError):
            self.reserve(limit=10)
        self.assertEqual(self.api.events, [])
        self.assertIn(self.old_key, self.api.rows)

    def test_changed_prepared_archive_never_mutates_cloud(self):
        self.archive.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.reserve()
        self.assertEqual(self.api.events, [])

    def test_formal_release_cannot_use_alpha_exception(self):
        self.previous = self.api.read("channel/stable.json")
        with self.assertRaises(ValueError):
            self.reserve()
        self.assertEqual(self.api.events, [])

    def test_interrupted_delete_resumes_from_the_original_signed_inputs(self):
        original = self.api.delete
        self.api.delete = lambda row: (_ for _ in ()).throw(OSError("synthetic outage"))
        with self.assertRaises(OSError):
            self.reserve()
        self.assertEqual(json.loads(self.api.read(self.channel))["state"], "publishing")
        self.assertIn(self.old_key, self.api.rows)
        self.api.delete = original
        result = alpha_space.resume_alpha_space(self.archive, "win-x64",
            api=self.api, root=self.root, limit=self.limit)
        self.assertEqual(result["state"], "ready")
        self.assertNotIn(self.old_key, self.api.rows)
        alpha_space.assert_alpha_channel(result, api=self.api)
        self.api.add(self.channel, self.manifest.read_bytes())
        alpha_space.assert_alpha_channel(result, api=self.api)
        alpha_space.complete_alpha_space(result, root=self.root)
        self.assertIsNone(alpha_space.resume_alpha_space(self.archive, "win-x64", api=self.api, root=self.root))

    def test_replaced_channel_and_different_retry_archive_are_rejected(self):
        result = self.reserve()
        other = self.root / "other.zip"
        other.write_bytes(b"different")
        with self.assertRaises(ValueError):
            alpha_space.resume_alpha_space(other, "win-x64", api=self.api, root=self.root)
        self.api.add(self.channel, b"changed by another publisher")
        with self.assertRaises(ValueError):
            alpha_space.assert_alpha_channel(result, api=self.api)
