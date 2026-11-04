"""Admission prevents using the Windows update pipeline from another OS or CPU."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from modules.platform_services import app_update_support as support
from modules.app_update_services.checker import UpdateChecker
from modules.app_update_services.manifest import verify_manifest
from modules.app_update_services.transfer import UpdateTransfer
from tests.test_app_updates import signed, PUBLIC


class UpdatePlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_linux_never_reads_windows_test_code_and_rejects_missing_channel_without_network(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(support, "is_linux", return_value=True), \
             patch("modules.platform_services.app_update_access.read_code") as read, \
             patch("modules.app_update_services.checker.QNetworkAccessManager") as network:
            checker = UpdateChecker("1.0.17", Path(folder) / "missing.json")
            errors = []
            checker.failed.connect(lambda message, manual: errors.append((message, manual)))
            self.assertEqual(checker.test_code, "")
            self.assertFalse(checker.check(manual=True))
            self.assertTrue(errors[0][1])
            self.assertIn("missing.json", errors[0][0])
            read.assert_not_called()
            network.assert_not_called()
            checker.deleteLater()

    def test_even_correctly_signed_windows_package_is_rejected_on_linux(self):
        with patch.object(support, "is_linux", return_value=True):
            with self.assertRaises(ValueError):
                verify_manifest(signed(), PUBLIC)

    def test_wrong_cpu_and_unknown_os_are_rejected(self):
        with patch.object(support, "is_linux", return_value=False), \
             patch.object(support, "is_windows", return_value=True), \
             patch.object(support.platform, "machine", return_value="aarch64"):
            with self.assertRaises(ValueError):
                support.require_native_updater()
        with patch.object(support, "is_linux", return_value=False), \
             patch.object(support, "is_windows", return_value=False):
            with self.assertRaises(ValueError):
                support.require_native_updater()

    def test_transfer_rejects_before_scanning_or_downloading(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(support, "is_linux", return_value=True), \
             patch("modules.app_update_services.transfer.make_plan") as scan, \
             patch("modules.app_update_services.transfer.preview_plan") as preview:
            transfer = UpdateTransfer(folder, {}, b"", download=True)
            errors = []
            transfer.failed.connect(errors.append)
            transfer.run()
            self.assertTrue(errors)
            scan.assert_not_called()
            preview.assert_not_called()
            self.assertEqual(list(Path(folder).iterdir()), [])
            transfer.deleteLater()
