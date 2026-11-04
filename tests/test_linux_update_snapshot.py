"""Stable-runtime preparation is read-only and survives atomic installed-file replacement."""
from contextlib import contextmanager, ExitStack
import copy
import errno
import hashlib
from pathlib import Path
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from modules.app_update_services.manifest import canonical
from modules.platform_services import linux_update_snapshot as snapshot


def executable_bytes():
    value = bytearray(64); value[:7] = b"\x7fELF\x02\x01\x01"
    value[16:18] = (3).to_bytes(2, "little"); value[18:20] = (62).to_bytes(2, "little")
    return bytes(value) + b"synthetic; never executed"


@contextmanager
def model_linux_modes_on_windows():
    with ExitStack() as stack:
        stack.enter_context(patch.object(snapshot, "is_linux", return_value=True))
        if sys.platform == "win32":
            # This models only the metadata contract; actual O_NOFOLLOW/mode
            # behavior must also run on a native Linux filesystem.
            stack.enter_context(patch.object(snapshot, "_permissions", side_effect=lambda path,info:0o755 if path.name == "LuXiaoGuan" else 0o644))
            stack.enter_context(patch.object(snapshot, "_open_regular", side_effect=lambda path:Path(path).open("rb")))
            stack.enter_context(patch.object(snapshot, "_sync_directory"))
            stack.enter_context(patch.object(snapshot, "_sync_file"))
        yield


class RuntimeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / ".routermanager-update" / ("a" * 64)
        self.stage.mkdir(parents=True)
        (self.root / "resources").mkdir()
        data = {"LuXiaoGuan":executable_bytes(), "resources/runtime.dat":b"old private runtime"}
        files = []
        for relative, value in data.items():
            path = self.root / relative; path.write_bytes(value)
            mode = 0o755 if relative == "LuXiaoGuan" else 0o644; path.chmod(mode)
            files.append(dict(path=relative, kind="file", mode=mode, sha256=hashlib.sha256(value).hexdigest(),
                              size=len(value), blob_sha256="b"*64, blob_size=0))
        self.catalog = dict(schema=2, product="RouterManager", platform="linux-x64", version="1.0.21_alpha", files=files)
        self.digest = hashlib.sha256(canonical(self.catalog)).hexdigest()

    def test_hardlinked_snapshot_survives_atomic_live_file_replacement(self):
        with model_linux_modes_on_windows():
            runtime = snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog)
            self.assertEqual((runtime / "resources/runtime.dat").read_bytes(), b"old private runtime")
            replacement = self.root / "resources/replacement"; replacement.write_bytes(b"new runtime")
            os.replace(replacement, self.root / "resources/runtime.dat")
            self.assertEqual(snapshot.verify_runtime_snapshot(self.stage, self.digest), runtime)
            self.assertEqual((runtime / "resources/runtime.dat").read_bytes(), b"old private runtime")
            self.assertEqual((self.root / "resources/runtime.dat").read_bytes(), b"new runtime")

    def test_changed_source_fails_without_touching_installed_files(self):
        source = self.root / "resources/runtime.dat"; source.write_bytes(b"custom changed runtime")
        with model_linux_modes_on_windows(), self.assertRaises(ValueError):
            snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog)
        self.assertEqual(source.read_bytes(), b"custom changed runtime")
        self.assertFalse((self.stage / "runtime").exists())
        self.assertFalse(list(self.stage.glob(".runtime-*")))

    def test_filesystems_without_hardlinks_use_a_verified_copy(self):
        with model_linux_modes_on_windows(), patch.object(snapshot.os, "link", side_effect=OSError(errno.EXDEV, "cross-device")):
            runtime = snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog)
        self.assertEqual((runtime / "resources/runtime.dat").read_bytes(), b"old private runtime")
        self.assertNotEqual((runtime / "resources/runtime.dat").stat().st_ino, (self.root / "resources/runtime.dat").stat().st_ino)

    def test_changed_snapshot_is_rejected_and_original_bytes_remain(self):
        with model_linux_modes_on_windows():
            runtime = snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog)
            replacement = self.stage / "changed"; replacement.write_bytes(b"changed snapshot")
            os.replace(replacement, runtime / "resources/runtime.dat")
            with self.assertRaises(ValueError): snapshot.verify_runtime_snapshot(self.stage, self.digest)
        self.assertEqual((self.root / "resources/runtime.dat").read_bytes(), b"old private runtime")

    def test_wrong_binary_format_never_publishes_runtime_snapshot(self):
        value = b"MZ" + bytes(64); (self.root / "LuXiaoGuan").write_bytes(value)
        entry = self.catalog["files"][0]; entry.update(sha256=hashlib.sha256(value).hexdigest(), size=len(value))
        with model_linux_modes_on_windows(), self.assertRaises(ValueError):
            snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog)
        self.assertFalse((self.stage / "runtime").exists())

    def test_existing_verified_snapshot_can_be_reused(self):
        with model_linux_modes_on_windows():
            one = snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog)
            two = snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog)
            self.assertEqual(one, two)
            with self.assertRaises(ValueError): snapshot.verify_runtime_snapshot(self.stage, "c"*64)

    def test_wrong_os_fails_before_inspecting_installation(self):
        with patch.object(snapshot, "is_linux", return_value=False), patch.object(snapshot, "_root") as inspect:
            with self.assertRaises(OSError): snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog)
            inspect.assert_not_called()


    def test_cancel_and_expired_deadline_stop_before_runtime_mutation(self):
        with model_linux_modes_on_windows():
            with self.assertRaises(InterruptedError):
                snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog, cancel=lambda:True)
            with patch.object(snapshot.time, "monotonic", side_effect=[100, 221]):
                with self.assertRaises(TimeoutError):
                    snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog, timeout=120)
        self.assertFalse((self.stage / "runtime").exists())
        self.assertFalse(list(self.stage.glob(".runtime-*")))

    def test_busy_update_lease_fails_without_creating_snapshot_files(self):
        with model_linux_modes_on_windows(), patch.object(snapshot, "cache_lock") as lease:
            lease.return_value.__enter__.return_value = False
            with self.assertRaises(BlockingIOError):
                snapshot.prepare_runtime_snapshot(self.root, self.stage, self.catalog)
        self.assertEqual(list(self.stage.iterdir()), [])
        self.assertEqual((self.root / "resources/runtime.dat").read_bytes(), b"old private runtime")
