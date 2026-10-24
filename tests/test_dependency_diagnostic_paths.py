"""Failed diagnostic persistence must not replace the installation error."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject, Signal
from modules.capture_services.dependency_service import OpenWrtDependencyService
from modules.capture_services.task_controller import CaptureTaskController
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot, OpenWrtConnectionState, DependencyInfo, DependencyState
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.workspace import local_session_root
from modules.platform_services.owned_cache import checked_path


class Owner(QObject):
    openwrtSnapshotChanged = Signal(object)
    settingsMessageGenerated = Signal(str, str)
    activityChanged = Signal()

    def __init__(self):
        super().__init__()
        self._shutting_down = False
        self.task_controller = CaptureTaskController(self)
        ssh = SimpleNamespace(_client=object(), connected=True, host_fingerprint="fixture")
        runner = SimpleNamespace(ssh_client=ssh, operation_diagnostics=OperationDiagnostics())
        self.openwrt_backend = SimpleNamespace(ssh_client=ssh, command_runner=runner,
            snapshot=OpenWrtSessionSnapshot(connection_state=OpenWrtConnectionState.CONNECTED))

    @property
    def is_busy(self):
        return self.task_controller.busy

    def _start_openwrt_worker(self, session, operation, event, *args):
        self.pending = operation, event


class DiagnosticPathTests(unittest.TestCase):
    def test_diagnostic_rejection_cannot_replace_installation_error(self):
        owner = Owner()
        service = OpenWrtDependencyService(owner)
        primary = SignalError("transport", "original update failure")
        primary.package_diagnostic = "dependency_update exit=2\nSignature check passed.\nFailed to download"
        service.manager.install_all = Mock(side_effect=primary)
        service._save_diagnostic = Mock(side_effect=ValueError("Cache link/reparse point retained: C:\\software"))
        checked = (DependencyInfo("wpa_cli", "fixture", DependencyState.MISSING, ("wpa-cli",)),)
        with patch("modules.capture_services.dependency_service.check_tools", return_value=checked):
            self.assertTrue(service.install_async({}, scope="wpa_cli"))
            operation, event = owner.pending
            message = Mock()
            result = operation(event, message)
        self.assertEqual(result["error"], "original update failure")
        self.assertEqual(result["checked"], checked)
        self.assertTrue(any(call.args[1] == "warning" for call in message.call_args_list))
        owner.task_controller.finish(failed=True)

    def test_unsafe_primary_uses_existing_managed_temporary_root(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            primary = base / "unsafe"
            primary.mkdir()
            def check(root, path):
                if path == primary:
                    raise ValueError("Cache link/reparse point retained")
                return checked_path(root, path)
            with patch("modules.config_manager.CAPTURE_WORK_DIR", primary), patch("modules.capture_services.openwrt.workspace.tempfile.gettempdir", return_value=directory), patch("modules.platform_services.owned_cache.checked_path", side_effect=check):
                result = local_session_root()
            self.assertEqual(result, base / "winbox-capture-work")
            self.assertTrue(primary.is_dir())
            self.assertFalse(any(primary.iterdir()))

    def test_diagnostic_file_is_saved_and_acknowledged_in_safe_fallback(self):
        with TemporaryDirectory() as directory:
            primary = Path(directory, "unsafe")
            primary.mkdir()
            def check(root, path):
                if path == primary:
                    raise ValueError("Cache link/reparse point retained")
                return checked_path(root, path)
            with patch("modules.config_manager.CAPTURE_WORK_DIR", primary), patch("modules.capture_services.openwrt.workspace.tempfile.gettempdir", return_value=directory), patch("modules.platform_services.owned_cache.checked_path", side_effect=check):
                path = Path(OpenWrtDependencyService._save_diagnostic("a" * 32, "original package output"))
            self.assertEqual(path.read_text(encoding="utf-8"), "original package output")
            self.assertTrue(path.is_relative_to(Path(directory, "winbox-capture-work")))
            import json
            marker = json.loads(path.with_name(".winbox-session.json").read_text(encoding="utf-8"))
            self.assertTrue(marker["safe"])
            self.assertFalse(any(primary.iterdir()))

    def test_ordinary_primary_directory_remains_the_first_choice(self):
        with TemporaryDirectory() as directory:
            primary = Path(directory, "primary")
            with patch("modules.config_manager.CAPTURE_WORK_DIR", primary):
                self.assertEqual(local_session_root(), primary)
            self.assertTrue(primary.is_dir())

    def test_no_safe_root_refuses_to_create_a_session(self):
        with TemporaryDirectory() as directory:
            primary = Path(directory, "primary")
            with patch("modules.config_manager.CAPTURE_WORK_DIR", primary), patch("modules.capture_services.openwrt.workspace.tempfile.gettempdir", return_value=directory), patch("modules.platform_services.owned_cache.checked_path", side_effect=ValueError("unsafe root")):
                with self.assertRaises(OSError):
                    local_session_root()
            self.assertFalse(primary.exists())
