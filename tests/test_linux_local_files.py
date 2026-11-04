"""Filesystem safety contracts; native Linux execution remains separately required."""
import ctypes
import errno
import os
from pathlib import Path
import stat
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
from modules.platform_services import local_names, local_rename, recycle_bin
from modules.platform_services.local_file_move import move_local
from modules.platform_services.local_file_operations import create_local_directory, recycle_local_entry


def identity(path):
    info = path.lstat()
    return (info.st_mode, info.st_size, info.st_mtime, info.st_uid, info.st_gid)


class LocalPlatformTests(unittest.TestCase):
    def test_native_filename_rules_are_separate(self):
        with patch.object(local_names, "is_windows", return_value=False):
            for name in ("CON", "a:b", "back\\slash", "trailing.", "trailing "):
                self.assertTrue(local_names.valid_local_name(name))
            for name in ("", ".", "..", "a/b", "nul\0byte"):
                self.assertFalse(local_names.valid_local_name(name))
        with patch.object(local_names, "is_windows", return_value=True):
            for name in ("CON", "a:b", "back\\slash", "trailing.", "trailing ", "a\nb"):
                self.assertFalse(local_names.valid_local_name(name))

    def test_linux_rename_sets_noreplace_and_propagates_collision(self):
        rename = Mock(return_value=-1)
        with patch.object(local_rename, "is_windows", return_value=False), \
             patch.object(local_rename, "is_linux", return_value=True), \
             patch.object(local_rename, "_linux_api", return_value=(object(), rename)):
            ctypes.set_errno(errno.EEXIST)
            with self.assertRaises(FileExistsError):
                local_rename.rename_without_replace("/source", "/destination")
            rename.assert_called_once_with(-100, os.fsencode("/source"), -100,
                                           os.fsencode("/destination"), 1)

    def test_linux_unsupported_filesystem_never_falls_back_to_overwriting(self):
        rename = Mock(return_value=-1)
        with patch.object(local_rename, "is_windows", return_value=False), \
             patch.object(local_rename, "is_linux", return_value=True), \
             patch.object(local_rename, "_linux_api", return_value=(object(), rename)), \
             patch.object(local_rename.os, "rename") as unsafe:
            ctypes.set_errno(errno.EINVAL)
            with self.assertRaises(OSError):
                local_rename.rename_without_replace("/source", "/destination")
            unsafe.assert_not_called()

    def test_linux_file_publication_uses_same_atomic_contract(self):
        from modules.platform_services import local_file_publish
        with patch.object(local_file_publish.os, "name", "posix"), \
             patch.object(local_file_publish, "is_linux", return_value=True), \
             patch.object(local_file_publish, "rename_without_replace", side_effect=FileExistsError()) as publish, \
             patch.object(local_file_publish.os, "link") as link:
            with self.assertRaises(FileExistsError):
                local_file_publish.publish_new_file("source", "destination")
            publish.assert_called_once_with("source", "destination")
            link.assert_not_called()

    def test_directory_names_apply_windows_rules_only_on_windows(self):
        from modules.capture_services.openwrt.router_directory_transfer import _name
        for name in ("CON", "file:part", "trailing.", "back\\slash"):
            self.assertEqual(_name(name, windows=False), name)
            with self.assertRaises(ValueError):
                _name(name, windows=True)

    def test_windows_keeps_native_rename(self):
        with patch.object(local_rename, "is_windows", return_value=True), \
             patch.object(local_rename.os, "rename") as native:
            local_rename.rename_without_replace("source", "destination")
            native.assert_called_once_with("source", "destination")

    def test_linux_trash_uses_qt_instance_and_returns_failure_honestly(self):
        entry = Mock()
        with patch.object(recycle_bin, "is_windows", return_value=False), \
             patch.object(recycle_bin, "is_linux", return_value=True), \
             patch("PySide6.QtCore.QFile", return_value=entry):
            entry.moveToTrash.return_value = False
            self.assertFalse(recycle_bin.move_to_recycle_bin("/synthetic", Event()))
            entry.moveToTrash.assert_called_once_with()

    def test_cancelled_trash_never_calls_native_api(self):
        cancel = Event()
        cancel.set()
        with patch("PySide6.QtCore.QFile") as qt:
            with self.assertRaises(InterruptedError):
                recycle_bin.move_to_recycle_bin("/synthetic", cancel)
            qt.assert_not_called()

    def test_failed_trash_keeps_source_and_reports_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "synthetic.txt"
            source.write_text("preserve", encoding="utf-8")
            with patch("modules.platform_services.local_file_operations.move_to_recycle_bin", return_value=False):
                with self.assertRaises(OSError):
                    recycle_local_entry(source, root, identity(source), Event())
            self.assertEqual(source.read_text(encoding="utf-8"), "preserve")

    def test_move_refuses_existing_target_and_uses_shared_atomic_publisher(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, target = root / "source", root / "target"
            source.write_text("synthetic", encoding="utf-8")
            target.mkdir()
            expected, folder_expected = identity(source), identity(target)
            with patch("modules.platform_services.local_file_move.rename_without_replace") as publish:
                move_local(source, root, target, expected, folder_expected, Event())
                publish.assert_called_once_with(source, target / source.name)
            (target / source.name).write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                move_local(source, root, target, identity(source), identity(target), Event())
            self.assertEqual((target / source.name).read_text(encoding="utf-8"), "preserve")

    def test_create_directory_refuses_traversal_and_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("..", "../escape", "a/b"):
                with self.assertRaises(ValueError):
                    create_local_directory(root, name, Event())
            target = Path(create_local_directory(root, "synthetic", Event()))
            self.assertTrue(target.is_dir())
            with self.assertRaises(FileExistsError):
                create_local_directory(root, "synthetic", Event())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Requires a real Linux kernel")
    def test_native_linux_noreplace_for_files_and_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for directory in (False, True):
                source, target = root / "source", root / "target"
                if directory:
                    source.mkdir()
                    target.mkdir()
                else:
                    source.write_text("source", encoding="utf-8")
                    target.write_text("target", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    local_rename.rename_without_replace(source, target)
                self.assertTrue(source.exists())
                target.rmdir() if directory else target.unlink()
                local_rename.rename_without_replace(source, target)
                self.assertFalse(source.exists())
                target.rmdir() if directory else target.unlink()
