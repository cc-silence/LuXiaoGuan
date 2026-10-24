"""Real download/publish pipeline with an in-memory SFTP endpoint."""
from contextlib import nullcontext
import io
import os
from pathlib import Path
import stat
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.router_management import RouterManagement
from modules.capture_services.openwrt.router_backup import RouterBackupManager
from tests.test_router_backup import Device


class DownloadEndpoint:
    def __init__(self, files):
        self.files = files
    def get_channel(self):
        return SimpleNamespace(settimeout=lambda seconds: None)
    def lstat(self, path):
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=len(self.files[path]),
            st_mtime=123, st_uid=0, st_gid=0)
    def open(self, path, mode):
        return io.BytesIO(self.files[path])


@unittest.skipUnless(os.name == "nt", "Windows filesystem publication contract")
class TransferPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cancel = Event()
        self.files = {"/tmp/input": b"payload"}
        endpoint = DownloadEndpoint(self.files)
        self.ssh = SimpleNamespace(transfer=lambda *a, **kw: nullcontext(),
            open_sftp=lambda **kw: endpoint)
        self.manager = RouterManagement(self.ssh, Mock())

    def test_real_download_works_without_hardlink_support(self):
        destination = self.root / "download"
        with patch("os.link", side_effect=OSError("hardlinks unavailable")) as link:
            self.manager.transfer_file(False, destination, "/tmp/input", self.cancel, Mock())
        self.assertEqual(destination.read_bytes(), b"payload")
        link.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [destination])

    def test_real_backup_including_download_works_without_hardlink_support(self):
        device = Device(self.root)
        endpoint = DownloadEndpoint(device.remote)
        device.manager.ssh.transfer = lambda *a, **kw: nullcontext()
        device.manager.ssh.open_sftp = lambda **kw: endpoint
        device.manager.transfer_file = RouterManagement(device.manager.ssh, device).transfer_file
        destination = self.root / "backup.tar.gz"
        with patch("os.link", side_effect=OSError("hardlinks unavailable")) as link:
            RouterBackupManager(device.manager).export(destination, None, self.cancel, Mock())
        self.assertEqual(destination.read_bytes(), device.archive)
        link.assert_not_called()
        self.assertFalse(device.remote)

    def test_existing_unapproved_download_is_preserved_and_staging_is_removed(self):
        destination = self.root / "download"
        destination.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            self.manager.transfer_file(False, destination, "/tmp/input", self.cancel, Mock())
        self.assertEqual(destination.read_bytes(), b"keep")
        self.assertEqual(list(self.root.iterdir()), [destination])

    def test_cancelled_download_preserves_existing_file_and_removes_staging(self):
        destination = self.root / "download"
        destination.write_bytes(b"keep")
        def cancel_after_progress(value):
            self.cancel.set()
        with self.assertRaises(InterruptedError):
            self.manager.transfer_file(False, destination, "/tmp/input", self.cancel, cancel_after_progress)
        self.assertEqual(destination.read_bytes(), b"keep")
        self.assertEqual(list(self.root.iterdir()), [destination])
