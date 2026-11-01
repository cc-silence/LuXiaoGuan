"""Consolidation retains every original payload and the dedicated install policy."""
import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.common_bundle_repository import validate_catalog, CommonBundleRepository
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.hcx_bundle import primary_package, validate_variants
from modules.capture_services.openwrt.signal_manager import SignalError
from scripts.openwrt_packages.collect_common_bundle import Collector
from scripts.openwrt_packages.hcx_payloads import collect_hcx_payloads
from tests.hcx_bundle_fixture import hcx_fixture, prepare_hcx
from tests.test_common_offline_bundle import fixture


class HcxBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_collector_includes_all_original_objects_and_deduplicates(self):
        f = fixture(self.root / "fixture", kind="capture")
        task = Collector(self.root / "build", kind="capture")
        for sha, content in f.blobs.items():
            task.put(content, "fixture.ipk")
        variants = collect_hcx_payloads(task)
        # One real hcx object is also referenced by the ordinary profile.
        shared = next(v for v in variants if v["branch"] == "24.10"
                      and v["architecture"] == f.device.package_architecture)
        ordinary = next(r for r in f.profile["packages"] if r["name"] == "hcxdumptool")
        ordinary.update(next(r for r in shared["packages"] if r["name"] == "hcxdumptool"))
        inventory = dict(errors=[], profiles=[f.profile],
                         releases=f.catalog["releases"], targets=f.catalog["targets"])
        result = task.finish(inventory, [])
        with zipfile.ZipFile(result["zip_path"]) as zipped:
            catalog = json.loads(zipped.read("catalog.json"))
            objects = validate_catalog(catalog, "capture")
            self.assertEqual(catalog["profiles"], [f.profile])
            self.assertEqual(catalog["hcx_variants"], variants)
            self.assertEqual(set(zipped.namelist()), {"catalog.json"} | {"objects/" + s for s in objects})
            for sha, row in objects.items():
                raw = zipped.read(row["blob"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
                self.assertEqual(len(raw), row["size"])
            self.assertEqual(len(variants), 8)
            self.assertEqual(sum(len(v["packages"]) for v in variants), 24)

    def test_malformed_missing_or_wrong_version_variants_are_rejected(self):
        task = Collector(self.root / "build", kind="capture")
        variants = collect_hcx_payloads(task)
        broken = [variants[:-1], variants + [variants[0]]]
        missing = copy.deepcopy(variants)
        missing[0]["packages"].pop()
        broken.append(missing)
        wrong = copy.deepcopy(variants)
        wrong[-1]["packages"][-1]["version"] = "6.2.4-1"
        broken.append(wrong)
        for value in broken:
            with self.assertRaises(ValueError):
                validate_variants(value)

    def test_missing_unified_bundle_never_uses_legacy_tools_or_old_system_version(self):
        repo = CommonBundleRepository(self.root / "missing", kind="capture")
        device = NS(package_manager="opkg", package_architecture="mipsel_24kc")
        with patch("modules.capture_services.openwrt.common_bundle_repository.CommonBundleRepository", return_value=repo):
            with self.assertRaises(SignalError):
                with primary_package(device, Event()):
                    self.fail("Missing capture bundle must stop installation")

    def test_old_capture_bundle_requires_update(self):
        f = fixture(self.root / "old", kind="capture")
        repo = prepare_hcx(f, self.root / "repository")
        with patch("modules.capture_services.openwrt.common_bundle_repository.CommonBundleRepository", return_value=repo):
            with self.assertRaises(SignalError):
                with primary_package(f.device, Event()):
                    self.fail("Old bundle has no designated hcx payload")

    def test_corrupt_designated_object_prevents_materialization(self):
        f = hcx_fixture(self.root)
        repo = prepare_hcx(f, self.root / "repository")
        repo._load()
        v = next(v for v in repo.catalog["hcx_variants"] if v["branch"] == "24.10"
                 and v["architecture"] == f.device.package_architecture)
        row = next(r for r in v["packages"] if r["name"] == "hcxdumptool")
        repo.object_path(row).write_bytes(b"corrupt")
        with patch("modules.capture_services.openwrt.common_bundle_repository.CommonBundleRepository", return_value=repo):
            with self.assertRaises(ValueError):
                with primary_package(f.device, Event()):
                    self.fail("Corrupt hcx must not be installed")

    def test_old_firmware_still_selects_designated_634_from_2410(self):
        f = hcx_fixture(self.root)
        f.device.version = "23.05.6"
        repo = prepare_hcx(f, self.root / "repository")
        with patch("modules.capture_services.openwrt.common_bundle_repository.CommonBundleRepository", return_value=repo):
            with primary_package(f.device, Event()) as (package, row):
                self.assertEqual(row["version"], "6.3.4-r1")
                self.assertIn("/24.10.8/", row["url"])
                self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(), row["sha256"])
            self.assertFalse(package.exists())

    def test_missing_libraries_use_exact_capture_profile_without_network(self):
        f = hcx_fixture(self.root)
        repo = prepare_hcx(f, self.root / "repository")
        manager = NS(command_runner=Mock(), _execute_stage=Mock(return_value="libc - 1\n"),
                     _install_offline=Mock(), _install_online=Mock(side_effect=AssertionError("network")))
        def installed(*args, **kwargs):
            self.assertEqual(set(kwargs["local_packages"]), {"libpcap1", "libopenssl3"})
            self.assertTrue(all(p.is_file() for p in kwargs["local_paths"]))
        manager._install_offline.side_effect = installed
        self.assertTrue(install_from_local(manager, ["libpcap1", "libopenssl3"], f.device,
                        self.root / "cache", True, Event(), lambda *_: None, repository=repo))
        manager._install_offline.assert_called_once()
        manager._install_online.assert_not_called()


if __name__ == "__main__":
    unittest.main()
