"""Short-path identities and backward-compatible local bundle lifecycle."""
import json
from pathlib import Path
import shutil
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
from tests.test_common_offline_bundle import fixture
from modules.capture_services.openwrt.bundle_layout import version_path
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
from modules.capture_services.openwrt.bundle_archive_cleanup import cleanup_repository_archive
from modules.capture_services.openwrt.bundle_export import list_export_profiles, export_packages
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.signal_manager import RouterSignalManager


def prepare(source, root, kind="common", **kwargs):
    return prepare_archive(source.archive, source.envelope, root, Event(), public_key=source.public, kind=kind, **kwargs)


def legacy_copy(source, root, kind="common"):
    result = prepare(source, root, kind)
    compact = Path(result["content_path"])
    legacy = root / "versions" / source.payload["sha256"]
    compact.rename(legacy)
    (legacy / "revision.json").unlink()
    return legacy


class BundleLayoutTests(unittest.TestCase):
    def test_date_sequence_shortens_object_path_without_shortening_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = fixture(root / "source"); target = root / "resource"
            with patch("modules.capture_services.openwrt.bundle_layout.datetime") as clock:
                clock.now.return_value.strftime.return_value = "20261026"
                result = prepare(source, target)
            repo = CommonBundleRepository(target, source.public)
            self.assertTrue(repo._load())
            self.assertEqual(repo.content_root.name, "20261026-01")
            self.assertEqual(result["directory"], "20261026-01")
            self.assertEqual(repo.revision, source.payload["sha256"])
            row = source.profile["packages"][0]
            actual = repo.object_path(row)
            self.assertEqual(actual.name, row["sha256"])
            previous = target / "versions" / source.payload["sha256"] / row["blob"]
            self.assertEqual(len(str(previous)) - len(str(actual)), 53)
            self.assertEqual(json.loads((repo.content_root / "revision.json").read_text())["sha256"], repo.revision)

    def test_same_day_revisions_retry_and_existing_user_folder_do_not_collide(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root / "resource"
            occupied = target / "versions" / "20261026-01"
            occupied.mkdir(parents=True); (occupied / "user.txt").write_bytes(b"keep")
            first = fixture(root / "first"); second = fixture(root / "second", apk=True)
            with patch("modules.capture_services.openwrt.bundle_layout.datetime") as clock:
                clock.now.return_value.strftime.return_value = "20261026"
                a = prepare(first, target)
                b = prepare(second, target)
                retry = prepare(second, target)
            self.assertEqual(a["directory"], "20261026-02")
            self.assertEqual(b["directory"], "20261026-03")
            self.assertEqual(retry["directory"], b["directory"])
            self.assertEqual((occupied / "user.txt").read_bytes(), b"keep")
            self.assertEqual(version_path(target, second.payload["sha256"]), Path(b["content_path"]))

    def test_real_legacy_layout_remains_readable_installable_and_exportable(self):
        for kind in ("common", "capture"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); source = fixture(root / "source", kind=kind); target = root / "resource"
                legacy = legacy_copy(source, target, kind)
                repo = CommonBundleRepository(target, source.public, kind)
                self.assertTrue(repo._load()); self.assertEqual(repo.content_root, legacy)
                wanted = "tmux" if kind == "common" else "iw"
                manager = RouterSignalManager(Mock())
                manager._execute_stage = Mock(return_value="libc - 1")
                manager._install_offline = Mock()
                self.assertTrue(install_from_local(manager, [wanted], source.device, root / "work", False, Event(), Mock(), repository=repo))
                self.assertTrue(manager._install_offline.called)
                info = list_export_profiles(kind, Event(), root=target, public_key=source.public)
                self.assertEqual(info["revision"], source.payload["sha256"])
                output = root / "export"; output.mkdir()
                result = export_packages(kind, dict(info["profiles"][0], revision=info["revision"]), [wanted], output, Event(), root=target, public_key=source.public)
                self.assertEqual((Path(result["path"]) / (wanted + "-1.ipk")).read_bytes(), ("package:" + wanted).encode())
                self.assertTrue(legacy.exists())

    def test_legacy_and_short_revisions_and_old_zip_are_retired_together(self):
        for kind in ("common", "capture"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); target = root / "resource"
                old = fixture(root / "old", kind=kind); new = fixture(root / "new", apk=True, kind=kind)
                legacy = legacy_copy(old, target, kind)
                archive = target / "archives" / old.payload["sha256"] / old.archive.name
                archive.parent.mkdir(parents=True); shutil.copyfile(old.archive, archive)
                result = prepare(new, target, kind)
                self.assertEqual(result["cleanup"]["versions"], 1)
                self.assertFalse(legacy.exists()); self.assertFalse(archive.exists())
                self.assertTrue(Path(result["content_path"]).exists())
                self.assertEqual((target / "current.json").read_bytes(), new.envelope)

    def test_failed_upgrade_keeps_real_legacy_files_and_old_zip(self):
        for failure in ("cancel", "activation"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); target = root / "resource"
                old = fixture(root / "old"); new = fixture(root / "new", apk=True)
                legacy = legacy_copy(old, target)
                archive = target / "archives" / old.payload["sha256"] / old.archive.name
                archive.parent.mkdir(parents=True); shutil.copyfile(old.archive, archive)
                if failure == "cancel":
                    cancel = Event(); cancel.set()
                    with self.assertRaises(InterruptedError):
                        prepare_archive(new.archive, new.envelope, target, cancel, public_key=new.public)
                else:
                    with patch("modules.capture_services.openwrt.common_bundle_download.os.replace", side_effect=PermissionError("busy")):
                        with self.assertRaises(PermissionError):
                            prepare(new, target)
                self.assertEqual((target / "current.json").read_bytes(), old.envelope)
                self.assertTrue(legacy.exists()); self.assertTrue(archive.exists())
                self.assertTrue(CommonBundleRepository(target, old.public)._load())

    def test_old_zip_migration_supports_both_layouts(self):
        for legacy_layout in (False, True):
            with self.subTest(legacy=legacy_layout), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); target = root / "resource"; source = fixture(root / "source")
                if legacy_layout:
                    legacy_copy(source, target)
                else:
                    prepare(source, target)
                archive = target / "archives" / source.payload["sha256"] / source.archive.name
                archive.parent.mkdir(parents=True); shutil.copyfile(source.archive, archive)
                repo = CommonBundleRepository(target, source.public); repo._load()
                cleanup_repository_archive(repo, Event(), Mock())
                self.assertFalse(archive.exists())
                self.assertTrue(repo.object_path(source.profile["packages"][0]).exists())

    def test_legacy_apk_catalog_and_objects_still_install_without_network(self):
        from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
        for kind in ("common", "capture"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); target = root / "resource"
                source = fixture(root / "source", apk=True, kind=kind)
                legacy_copy(source, target, kind)
                repo = CommonBundleRepository(target, source.public, kind)
                profile = repo.profile(source.device)
                wanted = "tmux" if kind == "common" else "iw"
                uploads = []
                def stage(name, command, *_args):
                    return "(1/1) Installing " + wanted + " (1)" if name == "apk_simulate_plan" else "OK:"
                installer = ApkOfflineInstaller(stage, uploader=lambda src, dst: uploads.append((Path(src).name, Path(src).read_bytes())),
                                               fetch=Mock(side_effect=AssertionError("network not permitted")))
                self.assertEqual(installer.install_local([wanted], profile, repo, root / "work", Event()), [(wanted, "1")])
                self.assertIn((wanted + "-1.apk", ("package:" + wanted).encode()), uploads)
                installer.fetch.assert_not_called()

    def test_download_service_reports_short_path_with_full_revision(self):
        from tests.test_bundle_updates import BundleServiceUpdateTests
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = fixture(root / "source"); target = root / "resource"
            result = prepare(source, target)
            owner, service = BundleServiceUpdateTests().service()
            with patch("modules.capture_services.openwrt.common_bundle_repository.bundle_root", return_value=target):
                service._succeeded(result)
            self.assertEqual(service.local_status["path"], str(Path(result["content_path"]).resolve()))
            self.assertEqual(service.local_status["revision"], source.payload["sha256"])

    def test_locator_is_not_authority_for_catalog_or_object_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root / "resource"; source = fixture(root / "source")
            result = prepare(source, target); version = Path(result["content_path"])
            row = source.profile["packages"][0]
            (version / row["blob"]).write_bytes(b"tampered")
            repo = CommonBundleRepository(target, source.public); repo._load()
            with self.assertRaises(ValueError):
                repo.object_path(row)
            (version / "catalog.json").write_bytes(b"{}")
            with self.assertRaises(ValueError):
                CommonBundleRepository(target, source.public)._load()

    def test_partial_short_cleanup_retries_after_metadata_deletion_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root / "resource"
            old = fixture(root / "old"); new = fixture(root / "new", apk=True)
            old_path = Path(prepare(old, target)["content_path"])
            marker = old_path / "revision.json"; original = Path.unlink
            def unlink(path, *args, **kwargs):
                if path == marker:
                    raise PermissionError("locator busy")
                return original(path, *args, **kwargs)
            with patch.object(Path, "unlink", unlink):
                result = prepare(new, target)
            self.assertTrue(result["cleanup"]["incomplete"])
            self.assertTrue(marker.exists()); self.assertFalse((old_path / "catalog.json").exists())
            self.assertFalse(prepare(new, target)["cleanup"]["incomplete"])
            self.assertFalse(old_path.exists())

    def test_existing_selection_keeps_full_identity_across_physical_folder_rename(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root / "resource"; source = fixture(root / "source")
            legacy = legacy_copy(source, target)
            info = list_export_profiles("common", Event(), root=target, public_key=source.public)
            compact = legacy.with_name("20261026-01")
            legacy.rename(compact)
            (compact / "revision.json").write_text(json.dumps(dict(schema=1, kind="common", sha256=source.payload["sha256"])), encoding="utf-8")
            output = root / "export"; output.mkdir()
            export_packages("common", dict(info["profiles"][0], revision=info["revision"]), ["tmux"], output, Event(), root=target, public_key=source.public)
            self.assertEqual(len(list(output.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
