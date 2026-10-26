"""Regressions reproduced during the local short-layout/cleanup audit."""
import copy
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
from tests.test_common_offline_bundle import fixture
from tests.test_bundle_layout import prepare, legacy_copy
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository, validate_catalog
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.signal_manager import RouterSignalManager
from modules.capture_services.openwrt.bundle_status import local_bundle_status
from modules.capture_services.openwrt.bundle_export import list_export_profiles


class BundleStorageAuditTests(unittest.TestCase):
    def test_malformed_old_catalog_cannot_replace_success_with_exception(self):
        for kind in ("common", "capture"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); target = root / "resource"
                old = fixture(root / "old", kind=kind); new = fixture(root / "new", kind=kind, apk=True)
                old_path = Path(prepare(old, target, kind)["content_path"])
                catalog = old_path / "catalog.json"
                value = json.loads(catalog.read_text(encoding="utf-8"))
                value["profiles"][0]["target"] = 123
                catalog.write_text(json.dumps(value), encoding="utf-8")
                tell = Mock()
                result = prepare(new, target, kind, stage=tell)
                self.assertTrue(result["cleanup"]["incomplete"])
                self.assertEqual((target / "current.json").read_bytes(), new.envelope)
                self.assertTrue(CommonBundleRepository(target, new.public, kind)._load())
                self.assertTrue(catalog.exists())
                self.assertTrue(any(call.args[1] == "warning" for call in tell.call_args_list))

    def test_invalid_nested_catalog_shapes_are_consistent_data_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            source = fixture(Path(folder) / "source")
            for field, invalid in (("target", 123), ("release", []), ("packages", [None]), ("indexes", None)):
                with self.subTest(field=field):
                    catalog = copy.deepcopy(source.catalog)
                    catalog["profiles"][0][field] = invalid
                    with self.assertRaises(ValueError):
                        validate_catalog(catalog)

    def test_missing_local_bundle_does_not_write_resources_or_block_online_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = fixture(root / "source"); target = root / "read-only" / "missing"
            manager = RouterSignalManager(Mock()); manager._execute_stage = Mock()
            mkdir = Path.mkdir
            def deny_resource_write(path, *args, **kwargs):
                if path == target or target in path.parents:
                    raise PermissionError("resource root is not writable")
                return mkdir(path, *args, **kwargs)
            with patch.object(Path, "mkdir", deny_resource_write):
                repo = CommonBundleRepository(target, source.public)
                self.assertFalse(install_from_local(manager, ["tmux"], source.device, root / "work", False, Event(), Mock(), repository=repo))
                self.assertFalse(local_bundle_status(Event(), root=target, public_key=source.public)["installed"])
            manager._execute_stage.assert_not_called()
            self.assertFalse(target.exists())

    def test_existing_legacy_resource_remains_usable_without_write_permission(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = fixture(root / "source"); target = root / "resource"
            legacy = legacy_copy(source, target)
            manager = RouterSignalManager(Mock()); manager._execute_stage = Mock(return_value="libc - 1")
            manager._install_offline = Mock()
            original_open, original_mkdir = Path.open, Path.mkdir
            def readonly_open(path, mode="r", *args, **kwargs):
                if (path == target or target in path.parents) and any(flag in mode for flag in "wa+"):
                    raise PermissionError("read-only resource")
                return original_open(path, mode, *args, **kwargs)
            def readonly_mkdir(path, *args, **kwargs):
                if path == target or target in path.parents:
                    raise PermissionError("read-only resource")
                return original_mkdir(path, *args, **kwargs)
            with patch.object(Path, "open", readonly_open), patch.object(Path, "mkdir", readonly_mkdir):
                self.assertTrue(local_bundle_status(Event(), root=target, public_key=source.public)["installed"])
                self.assertEqual(list_export_profiles("common", Event(), root=target, public_key=source.public)["revision"], source.payload["sha256"])
                repo = CommonBundleRepository(target, source.public)
                self.assertTrue(install_from_local(manager, ["tmux"], source.device, root / "work", False, Event(), Mock(), repository=repo))
            manager._install_offline.assert_called_once()
            self.assertTrue(legacy.exists())
            self.assertFalse((target / ".storage.lock").exists())

    def test_partial_duplicate_does_not_hide_complete_version_or_become_active(self):
        for shape in ("legacy", "date"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); target = root / "resource"; source = fixture(root / "source")
                complete = Path(prepare(source, target)["content_path"])
                name = source.payload["sha256"] if shape == "legacy" else "19000101-01"
                partial = target / "versions" / name
                partial.mkdir()
                if shape == "date":
                    (partial / "revision.json").write_text(json.dumps(dict(schema=1, kind="common", sha256=source.payload["sha256"])), encoding="utf-8")
                repo = CommonBundleRepository(target, source.public)
                self.assertTrue(repo._load())
                self.assertEqual(repo.content_root, complete)
                self.assertTrue(repo.object_path(source.profile["packages"][0]).is_file())
                result = prepare(source, target)
                self.assertEqual(Path(result["content_path"]), complete)
                self.assertTrue(complete.exists())
                self.assertTrue(CommonBundleRepository(target, source.public)._load())


if __name__ == "__main__":
    unittest.main()
