"""Linux startup policy and desktop launcher regression checks on an isolated desktop."""
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
from modules.platform_services import linux_desktop_shortcut as linux
from modules.platform_services import desktop_shortcut as shared
from modules.platform_services import desktop_startup
from modules.desktop_shell.startup_firewall_controller import StartupFirewallController
from modules.desktop_shell.startup_firewall_presenter import StartupFirewallPresenter
from tests.test_desktop_shortcut import APP


@contextmanager
def model_unix_execution_bit():
    # Windows does not expose Unix execute permissions on .desktop files.
    original = linux.inspect_shortcut
    def inspect(path):
        value = original(path)
        if sys.platform == 'win32' and value['exists']:
            value['executable'] = True
        return value
    with patch.object(linux, 'inspect_shortcut', side_effect=inspect):
        yield


class LinuxStartupTests(unittest.TestCase):
    def test_windows_audit_is_not_applicable_and_does_not_start_thread_or_dialog(self):
        with patch('modules.desktop_shell.startup_firewall_controller.is_windows', return_value=False):
            controller = StartupFirewallController()
        finished = []
        controller.finished.connect(lambda ok, error: finished.append((ok, error)))
        presenter = StartupFirewallPresenter(controller)
        with patch('modules.desktop_shell.startup_firewall_controller.threading.Thread') as thread, \
                patch('modules.desktop_shell.startup_firewall_controller.ensure_startup_firewall_rules') as audit:
            presenter.show_for(None)
            controller.wait_ready(Event(), 1)
            thread.assert_not_called()
            audit.assert_not_called()
        self.assertEqual(controller.stage, 'not_applicable')
        self.assertFalse(controller.applicable)
        self.assertFalse(controller.active)
        self.assertIsNone(presenter.dialog)
        self.assertEqual(finished, [(True, '')])
        cancelled = Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            controller.wait_ready(cancelled)

    def test_linux_cleanup_never_imports_windows_session(self):
        code = """
import importlib.abc, sys
from modules.platform_services import desktop_startup
desktop_startup.is_windows = lambda: False
class BlockWindows(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('modules.platform_services.firewall_session'):
            raise ImportError(fullname)
sys.meta_path.insert(0, BlockWindows())
desktop_startup.close_startup_sessions()
assert 'modules.platform_services.firewall_session' not in sys.modules
"""
        result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                                text=True, timeout=10, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_cleanup_still_closes_existing_session(self):
        from types import ModuleType
        session = ModuleType('modules.platform_services.firewall_session')
        session.close_firewall_session = Mock()
        with patch.object(desktop_startup, 'is_windows', return_value=True), \
                patch.dict(sys.modules, {'modules.platform_services.firewall_session': session}):
            desktop_startup.close_startup_sessions()
        session.close_firewall_session.assert_called_once()


class LinuxLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "user's \u684c\u9762 space"
        self.root.mkdir()
        self.target = self.root / 'private-python'
        self.target.touch()
        icons = self.root / 'images/icons'
        icons.mkdir(parents=True)
        (icons / 'app-router.png').touch()
        (self.root / 'main.py').touch()
        self.spec = linux.shortcut_spec(self.root, self.root, self.target, False)
        self.staging = self.root / '.luxiaoguan-shortcut-test.desktop'

    def tearDown(self):
        self.temp.cleanup()

    def create(self, observed=None, replace=False):
        observed = observed or linux.inspect_shortcut(self.spec.path)
        with model_unix_execution_bit():
            return linux.operate(self.spec, 'create', observed, replace, self.staging)

    def test_create_inspect_and_repair_share_identity(self):
        self.assertEqual(shared.shortcut_state(self.spec, {'exists': False}), 'missing')
        result = self.create()
        self.assertEqual(shared.shortcut_state(self.spec, result), 'valid')
        self.assertFalse(self.staging.exists())
        data = self.spec.path.read_text(encoding='utf-8')
        self.assertIn('Terminal=false', data)
        self.assertIn('main.py', data)
        self.assertNotIn('pythonw.exe', data)
        self.spec.path.write_text(data.replace('Terminal=false', 'Terminal=true'), encoding='utf-8')
        observed = linux.inspect_shortcut(self.spec.path)
        self.assertEqual(shared.shortcut_state(self.spec, observed), 'outdated')
        self.assertEqual(shared.shortcut_state(self.spec, self.create(observed)), 'valid')

    def test_foreign_file_and_modified_file_require_new_confirmation(self):
        self.spec.path.write_text('another application', encoding='utf-8')
        observed = linux.inspect_shortcut(self.spec.path)
        with self.assertRaises(PermissionError):
            self.create(observed)
        self.assertEqual(self.spec.path.read_text(encoding='utf-8'), 'another application')
        self.spec.path.write_text('changed after inspection', encoding='utf-8')
        with self.assertRaises(OSError):
            self.create(observed, True)
        fresh = linux.inspect_shortcut(self.spec.path)
        self.assertEqual(shared.shortcut_state(self.spec, self.create(fresh, True)), 'valid')

    def test_race_during_new_file_publication_does_not_overwrite_the_new_file(self):
        original = linux.os.link
        def raced(source, target):
            Path(target).write_text('arrived during publication', encoding='utf-8')
            return original(source, target)
        with patch.object(linux.os, 'link', side_effect=raced):
            with self.assertRaises(FileExistsError):
                self.create()
        self.assertEqual(self.spec.path.read_text(encoding='utf-8'), 'arrived during publication')
        self.assertFalse(self.staging.exists())

    def test_preexisting_staging_and_oversized_files_are_preserved(self):
        self.staging.write_text('unrelated staging file', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(self.staging.read_text(encoding='utf-8'), 'unrelated staging file')
        self.spec.path.write_bytes(b'x' * (linux.MAX_BYTES + 1))
        with self.assertRaises(OSError):
            linux.inspect_shortcut(self.spec.path)

    def test_missing_desktop_is_not_recreated(self):
        missing = self.root / 'not-configured'
        spec = linux.shortcut_spec(missing, self.root, self.target, False)
        with self.assertRaises(OSError):
            linux.operate(spec, 'inspect')
        self.assertFalse(missing.exists())

    def test_launcher_escaping_and_worker_command_use_no_shell(self):
        text = linux._argument('space $value "quote" 50% ' + chr(96))
        self.assertIn('%%', text)
        self.assertIn('\\"', text)
        self.assertIn('\\$', text)
        with self.assertRaises(ValueError):
            linux._argument('bad\nvalue')
        arguments = shared.shell_arguments(self.spec, 'inspect', temporary=self.staging)
        self.assertEqual(shared.shortcut_program(self.spec), sys.executable)
        self.assertIn('--desktop-shortcut-worker', arguments)
        payload = json.loads(base64.b64decode(arguments[-1]))
        self.assertEqual(payload['path'], str(self.spec.path))
        self.assertNotIn('powershell.exe', arguments)

    def test_source_worker_dispatch_does_not_import_the_gui(self):
        arguments = shared.shell_arguments(self.spec, 'inspect', temporary=self.staging)
        code = """
import sys
from modules.platform_services import linux_desktop_shortcut
linux_desktop_shortcut.is_linux = lambda: True
import main
sys.argv = ['main.py', '--desktop-shortcut-worker', sys.argv[1]]
result = main.main()
assert 'router_app.bootstrap' not in sys.modules
assert 'PySide6.QtWidgets' not in sys.modules
raise SystemExit(result)
"""
        result = subprocess.run([sys.executable, '-c', code, arguments[-1]],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['exists'])


if __name__ == '__main__':
    unittest.main()
