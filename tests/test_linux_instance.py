"""Single-instance lease contracts and Windows-free bootstrap import smoke."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication
from modules.platform_services.linux_single_instance import LinuxSingleInstance


class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.name = "RouterManager.Test." + uuid4().hex
        self.path = Path(self.temp.name) / "instance.lock"
        self.paths = patch("modules.platform_services.linux_single_instance.instance_paths",
                           return_value=(self.name, self.path))
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.instances = []

    def tearDown(self):
        for instance in reversed(self.instances):
            instance.close()
            instance.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def instance(self, callback=None):
        instance = LinuxSingleInstance(callback or Mock())
        self.instances.append(instance)
        return instance

    def wait_activations(self, callback, expected):
        deadline = time.monotonic() + 2
        while callback.call_count < expected and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertEqual(callback.call_count, expected)

    def test_secondary_close_never_removes_primary_endpoint(self):
        activated = Mock()
        primary, secondary, third = self.instance(activated), self.instance(), self.instance()
        self.assertTrue(primary.acquire())
        with patch.object(QLocalServer, "removeServer", wraps=QLocalServer.removeServer) as remove:
            self.assertFalse(secondary.acquire())
            secondary.close()
            remove.assert_not_called()
        self.wait_activations(activated, 1)
        self.assertFalse(third.acquire())
        self.wait_activations(activated, 2)

    def test_owner_close_releases_lease_and_next_instance_can_start(self):
        first, second = self.instance(), self.instance()
        self.assertTrue(first.acquire())
        self.assertTrue(first.acquire())
        self.assertEqual(first._lease.staleLockTime(), 0)
        first.close()
        self.assertTrue(second.acquire())
        self.assertEqual(second._server.socketOptions(), QLocalServer.UserAccessOption)

    def test_listener_failure_releases_lease_before_retry(self):
        first = self.instance()
        with patch.object(QLocalServer, "listen", return_value=False):
            with self.assertRaises(OSError):
                first.acquire()
        self.assertIsNone(first._lease)
        self.assertTrue(self.instance().acquire())

    def test_windows_free_bootstrap_import_in_isolated_process(self):
        script = r'''
import ctypes
from pathlib import Path
import tempfile
from unittest.mock import patch
from modules.platform_services import runtime, application_paths
for name in ("WINFUNCTYPE", "WinDLL", "windll"):
    if hasattr(ctypes, name):
        delattr(ctypes, name)
with tempfile.TemporaryDirectory() as folder, patch.object(runtime, "current_os", return_value="linux"), patch.object(application_paths, "linux_user_directory", side_effect=lambda kind: Path(folder)/kind):
    import router_app.bootstrap
    from modules.platform_services import process_service
    assert process_service._WND_ENUM_PROC is None
print("bootstrap-import-ok")
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bootstrap-import-ok", result.stdout)
