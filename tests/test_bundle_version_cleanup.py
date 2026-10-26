"""Signed local lifecycle regressions; no router or external network."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
from tests.test_common_offline_bundle import fixture
from modules.capture_services.openwrt.bundle_layout import version_path
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.bundle_storage_lock import bundle_lock, storage_lock_path


class BundleVersionCleanupTests(unittest.TestCase):
    def pair(self, root, kind="common"):
        old = fixture(root / "old", kind=kind)
        new = fixture(root / "new", kind=kind, apk=True)
        target = root / "resource"
        prepare_archive(old.archive, old.envelope, target, Event(), public_key=old.public, kind=kind)
        old.content_root = version_path(target, old.payload["sha256"], kind)
        archive = target / "archives" / old.payload["sha256"] / old.archive.name
        archive.parent.mkdir(parents=True)
        archive.write_bytes(old.archive.read_bytes())
        return old, new, target, archive

    def upgrade(self, new, target, kind="common", cancel=None, tell=None):
        return prepare_archive(new.archive, new.envelope, target, cancel or Event(),
                               public_key=new.public, kind=kind, stage=tell or Mock())

    def test_both_kinds_reclaim_old_objects_and_zip_after_activation(self):
        for kind in ("common", "capture"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                old, new, target, archive = self.pair(Path(folder), kind)
                result = self.upgrade(new, target, kind)
                self.assertEqual((target / "current.json").read_bytes(), new.envelope)
                self.assertFalse(old.content_root.exists())
                self.assertFalse(archive.exists())
                self.assertTrue((Path(result["content_path"]) / "catalog.json").is_file())
                self.assertTrue(old.archive.is_file())
                self.assertEqual(result["cleanup"]["versions"], 1)
                self.assertGreater(result["cleanup"]["bytes"], old.archive.stat().st_size)
                self.assertEqual(self.upgrade(new, target, kind)["cleanup"]["bytes"], 0)

    def test_cancel_or_bad_archive_keeps_previous_revision(self):
        for failure in ("cancel", "corrupt"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                old, new, target, archive = self.pair(Path(folder))
                cancel = Event()
                if failure == "cancel":
                    cancel.set()
                else:
                    new.archive.write_bytes(b"invalid archive")
                with self.assertRaises((InterruptedError, ValueError)):
                    self.upgrade(new, target, cancel=cancel)
                self.assertEqual((target / "current.json").read_bytes(), old.envelope)
                self.assertTrue(archive.exists())
                self.assertTrue((old.content_root / "catalog.json").exists())

    def test_unregistered_files_and_other_root_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old, new, target, archive = self.pair(root)
            extra = old.content_root / "my-notes.txt"
            extra.write_bytes(b"user file")
            other = root / "other-kind" / "keep.txt"
            other.parent.mkdir(); other.write_bytes(b"other")
            unrelated = archive.parent / "user.zip"
            unrelated.write_bytes(b"external")
            self.upgrade(new, target)
            self.assertTrue(extra.exists())
            self.assertTrue(other.exists())
            self.assertTrue(unrelated.exists())
            self.assertFalse(archive.exists())

    def test_permission_failure_warns_and_retry_finishes_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new, target, archive = self.pair(Path(folder))
            version = old.content_root
            original = Path.unlink
            denied = next((version / "objects").iterdir())
            def unlink(path, *args, **kwargs):
                if path == denied:
                    raise PermissionError("in use")
                return original(path, *args, **kwargs)
            tell = Mock()
            with patch.object(Path, "unlink", unlink):
                result = self.upgrade(new, target, tell=tell)
            self.assertTrue(result["cleanup"]["incomplete"])
            self.assertEqual((target / "current.json").read_bytes(), new.envelope)
            self.assertTrue((version / "catalog.json").exists())
            self.assertTrue(any(call.args[1] == "warning" for call in tell.call_args_list))
            result = self.upgrade(new, target)
            self.assertFalse(version.exists())
            self.assertFalse(result["cleanup"]["incomplete"])

    def test_activation_failure_never_starts_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new, target, archive = self.pair(Path(folder))
            with patch("modules.capture_services.openwrt.common_bundle_download.os.replace", side_effect=PermissionError("receipt busy")):
                with self.assertRaises(PermissionError):
                    self.upgrade(new, target)
            self.assertTrue(archive.exists())
            self.assertEqual((target / "current.json").read_bytes(), old.envelope)
            self.assertTrue((old.content_root / "catalog.json").exists())

    def test_thread_lease_prevents_update_and_nested_owner_can_continue(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new, target, archive = self.pair(Path(folder))
            with bundle_lock(target):
                with bundle_lock(target):
                    self.assertTrue(archive.exists())
                with ThreadPoolExecutor(1) as pool:
                    task = pool.submit(self.upgrade, new, target)
                    with self.assertRaises(OSError):
                        task.result(timeout=5)
                self.assertEqual((target / "current.json").read_bytes(), old.envelope)
            self.upgrade(new, target)
            self.assertFalse(archive.exists())

    def test_real_installer_and_export_retain_storage_until_finished(self):
        from modules.capture_services.openwrt.common_bundle_installer import install_from_local
        from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
        from modules.capture_services.openwrt.bundle_export import list_export_profiles, export_packages
        from modules.capture_services.openwrt.signal_manager import RouterSignalManager
        for route in ("install", "export"):
            with self.subTest(route=route), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                old, new, target, archive = self.pair(root)
                repo = CommonBundleRepository(target, old.public)
                checked = []
                def try_update(*_args, **_kwargs):
                    with ThreadPoolExecutor(1) as pool:
                        with self.assertRaises(OSError):
                            pool.submit(self.upgrade, new, target).result(timeout=5)
                    self.assertTrue(archive.exists())
                    checked.append(True)
                if route == "install":
                    manager = RouterSignalManager(Mock())
                    manager._execute_stage = Mock(return_value="libc - 1")
                    manager._install_offline = Mock(side_effect=try_update)
                    self.assertTrue(install_from_local(manager, ("tmux",), old.device, root / "work",
                                                      False, Event(), Mock(), repository=repo))
                else:
                    info = list_export_profiles("common", Event(), root=target, public_key=old.public)
                    selection = dict(info["profiles"][0], revision=info["revision"])
                    output = root / "exports"; output.mkdir()
                    from modules.capture_services.openwrt.bundle_export import _copy_verified
                    def copy(*args):
                        try_update()
                        return _copy_verified(*args)
                    with patch("modules.capture_services.openwrt.bundle_export._copy_verified", side_effect=copy):
                        export_packages("common", selection, ["tmux"], output, Event(), root=target, public_key=old.public)
                self.assertTrue(checked)
                self.upgrade(new, target)
                self.assertFalse(old.content_root.exists())

    def test_process_lease_and_crash_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "with spaces"
            command = [sys.executable, "-c", "from modules.capture_services.openwrt.bundle_storage_lock import bundle_lock; import sys; "
                       "lease=bundle_lock(sys.argv[1]); lease.__enter__(); print('locked', flush=True); sys.stdin.read()", str(root)]
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self.assertEqual(child.stdout.readline().strip(), b"locked")
                with self.assertRaises(OSError):
                    with bundle_lock(root):
                        self.fail("Concurrent process acquired the same storage")
            finally:
                child.kill(); child.communicate(timeout=5)
            with bundle_lock(root):
                self.assertTrue(storage_lock_path(root).exists())
                self.assertFalse(root.exists())

    def test_linked_old_objects_are_never_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old, new, target, archive = self.pair(root)
            version = old.content_root
            data = version / "objects"
            outside = root / "outside"
            data.rename(outside)
            # Junction creation is available without Windows symlink privilege.
            if os.name == "nt":
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(data), str(outside)], capture_output=True)
                if result.returncode:
                    self.skipTest("Junction creation unavailable")
            else:
                data.symlink_to(outside, target_is_directory=True)
            try:
                self.upgrade(new, target)
                self.assertTrue(any(outside.iterdir()))
                self.assertTrue((version / "catalog.json").exists())
            finally:
                if os.name == "nt":
                    data.rmdir()
                else:
                    data.unlink()


if __name__ == "__main__":
    unittest.main()
