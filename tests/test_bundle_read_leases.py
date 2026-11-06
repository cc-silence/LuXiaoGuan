"""Real OS leases: readers coexist while downloads and cleanup remain exclusive."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock

from modules.capture_services.openwrt.bundle_storage_lock import bundle_lock, storage_lock_path
from modules.capture_services.openwrt.bundle_status import local_bundle_status
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
from modules.capture_services.openwrt.signal_manager import RouterSignalManager
from tests.test_common_offline_bundle import fixture


def acquire(root, shared):
    with bundle_lock(root, shared=shared):
        return True


class BundleReadLeaseTests(unittest.TestCase):
    def test_concurrent_readers_and_exclusive_writer(self):
        with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(1) as pool:
            root = Path(folder) / "resources"
            with bundle_lock(root, shared=True):
                self.assertTrue(pool.submit(acquire, root, True).result(timeout=5))
                with self.assertRaises(OSError):
                    pool.submit(acquire, root, False).result(timeout=5)
            self.assertTrue(pool.submit(acquire, root, False).result(timeout=5))

    def test_writer_excludes_readers_and_other_writers(self):
        with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(1) as pool:
            root = Path(folder) / "resources"
            with bundle_lock(root):
                for shared in (True, False):
                    with self.subTest(shared=shared), self.assertRaises(OSError):
                        pool.submit(acquire, root, shared).result(timeout=5)
                with bundle_lock(root, shared=True):
                    self.assertTrue(True)
            self.assertTrue(pool.submit(acquire, root, True).result(timeout=5))

    def test_nested_reader_cannot_upgrade_to_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            with bundle_lock(folder, shared=True):
                with bundle_lock(folder, shared=True):
                    self.assertTrue(True)
                with self.assertRaises(OSError):
                    with bundle_lock(folder):
                        self.fail("Reader upgraded into a writer")
            with bundle_lock(folder):
                with bundle_lock(folder):
                    self.assertTrue(True)

    def test_exception_releases_reader(self):
        with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(1) as pool:
            with self.assertRaisesRegex(ValueError, "original"):
                with bundle_lock(folder, shared=True):
                    raise ValueError("original")
            self.assertTrue(pool.submit(acquire, folder, False).result(timeout=5))

    def test_cross_process_readers_and_crash_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "with spaces"
            script = ("from modules.capture_services.openwrt.bundle_storage_lock import bundle_lock; import sys; "
                      "lease=bundle_lock(sys.argv[1],shared=True); lease.__enter__(); "
                      "print('locked',flush=True); sys.stdin.read()")
            child = subprocess.Popen([sys.executable, "-c", script, str(root)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self.assertEqual(child.stdout.readline().strip(), b"locked")
                with bundle_lock(root, shared=True):
                    self.assertTrue(True)
                with self.assertRaises(OSError):
                    with bundle_lock(root):
                        self.fail("Writer ignored a different process reader")
            finally:
                child.kill()
                child.communicate(timeout=5)
            with bundle_lock(root):
                self.assertTrue(True)

    @unittest.skipUnless(os.name == "nt", "Windows CRT interoperability")
    def test_old_windows_crt_lock_still_excludes_new_readers(self):
        import msvcrt
        with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(1) as pool:
            path = storage_lock_path(folder)
            with path.open("w+b") as handle:
                handle.write(b"0"); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    with self.assertRaises(OSError):
                        pool.submit(acquire, folder, True).result(timeout=5)
                finally:
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.assertTrue(pool.submit(acquire, folder, True).result(timeout=5))

    def test_local_status_does_not_conflict_with_using_prepared_bundle(self):
        with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(1) as pool:
            root = Path(folder); source = fixture(root / "source")
            target = root / "resources"
            prepare_archive(source.archive, source.envelope, target, Event(), public_key=source.public)
            repo = CommonBundleRepository(target, source.public)
            manager = RouterSignalManager(Mock())
            manager._execute_stage = Mock(return_value="libc - 1")
            def during_install(*args, **kwargs):
                status = pool.submit(local_bundle_status, Event(), root=target,
                                     public_key=source.public).result(timeout=5)
                self.assertTrue(status["installed"])
                with self.assertRaises(OSError):
                    pool.submit(acquire, target, False).result(timeout=5)
            manager._install_offline = Mock(side_effect=during_install)
            self.assertTrue(install_from_local(manager, ("tmux",), source.device, root / "work",
                                              False, Event(), Mock(), repository=repo))
            manager._install_offline.assert_called_once()


if __name__ == "__main__":
    unittest.main()
