"""Runtime admission and non-Windows import-boundary regressions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from scripts import linux_runtime_check as check


class RuntimeAdmissionTests(unittest.TestCase):
    def test_supported_host_and_system_python_rejection(self):
        args = ('Linux', {'ID': 'debian', 'VERSION_ID': '12'})
        self.assertEqual(check.host_errors(*args, (3, 10, 22), 'CPython', 'x86_64'), [])
        errors = check.host_errors(*args, (3, 11, 2), 'CPython', 'x86_64')
        self.assertTrue(any('private CPython 3.10' in item for item in errors))

    def test_old_private_runtime_patches_are_not_admitted(self):
        for version in ((3, 10, 11), (3, 10, 21), (3, 10)):
            with self.subTest(version=version):
                errors = check.host_errors('Linux', {'ID': 'debian', 'VERSION_ID': '12'}, version, 'CPython', 'x86_64')
                self.assertTrue(any('3.10.22' in item for item in errors))

    def test_derivative_wrong_release_and_unknown_architecture_are_not_certified(self):
        cases = [
            ('Linux', {'ID': 'ubuntu', 'VERSION_ID': '12'}, (3, 10), 'CPython', 'x86_64'),
            ('Linux', {'ID': 'debian', 'VERSION_ID': '13'}, (3, 10), 'CPython', 'x86_64'),
            ('Linux', {'ID': 'debian', 'VERSION_ID': '12'}, (3, 10), 'CPython', 'aarch64'),
            ('Windows', {}, (3, 10), 'CPython', 'AMD64'),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertTrue(check.host_errors(*args))

    def test_shared_dependency_pins_and_invalid_input(self):
        self.assertEqual(check.pinned_requirements(check.ROOT / 'requirements.txt')['PySide6'], '6.3.2')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'requirements.txt'
            for text in ('PySide6>=6.3.2', 'PySide6==6.4.0', 'PySide6==6.3.2\nPySide6==6.3.2'):
                path.write_text(text, encoding='utf-8')
                with self.subTest(text=text), self.assertRaises(ValueError):
                    check.pinned_requirements(path)

    def test_native_crash_invalid_output_and_timeout_fail_admission(self):
        for output in (
                subprocess.CompletedProcess([], -11, '', 'native load failure'),
                subprocess.CompletedProcess([], 0, 'not JSON', ''),
                subprocess.CompletedProcess([], 0, '[]', '')):
            with self.subTest(output=output), patch.object(check.subprocess, 'run', return_value=output):
                self.assertTrue(check.probe_dependencies({'PySide6': '6.3.2'})['errors'])
        with patch.object(check.subprocess, 'run', side_effect=subprocess.TimeoutExpired('probe', 1)):
            self.assertTrue(check.probe_dependencies({'PySide6': '6.3.2'})['errors'])

    def test_dependency_probe_has_timeout_and_uses_selected_interpreter(self):
        result = {'versions': {'PySide6': '6.3.2'}, 'errors': []}
        with patch.object(check.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0, json.dumps(result), '')) as run:
            self.assertEqual(check.probe_dependencies({'PySide6': '6.3.2'}), result)
        self.assertEqual(run.call_args.args[0][:2], [sys.executable, '-I'])
        self.assertEqual(run.call_args.kwargs['timeout'], 45)

    def test_wrong_host_never_imports_native_dependencies(self):
        with patch.object(check.platform, 'system', return_value='Windows'), \
                patch.object(check, 'probe_dependencies') as probe:
            report = check.inspect_runtime()
        self.assertFalse(report['ready'])
        self.assertFalse(report['application_validated'])
        probe.assert_not_called()


class FirewallImportBoundaryTests(unittest.TestCase):
    def test_non_windows_import_never_loads_registry_backend(self):
        script = """
import importlib.abc, sys
from modules.platform_services import runtime
runtime.current_os = lambda: runtime.LINUX
from pathlib import Path
from unittest.mock import patch
patch('pathlib.Path.home', return_value=Path('/home/test')).start()
sys.modules.pop('winreg', None)  # Windows stdlib preloads this in the test host.
blocked = {'winreg', 'modules.platform_services.local_gpo_firewall'}
class DenyWindowsBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise ImportError('Windows backend imported: ' + fullname)
sys.meta_path.insert(0, DenyWindowsBackend())
from modules.platform_services import windows_firewall, startup_firewall
assert not windows_firewall.is_windows()
assert windows_firewall.STARTUP_OWNER == 'routermanager-startup'
assert not any(name in sys.modules for name in blocked)
"""
        result = subprocess.run([sys.executable, '-c', script], cwd=check.ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows backend identity contract')
    def test_windows_backend_keeps_original_callable_contract(self):
        from modules.platform_services import local_gpo_firewall as gpo, windows_firewall as firewall
        self.assertEqual(gpo.STARTUP_OWNER, firewall.STARTUP_OWNER)
        for name in ('check_rules', 'run_elevated_request', 'startup_rule_name', 'verified_rules_unchanged'):
            self.assertIs(getattr(gpo, name), getattr(firewall, name))


if __name__ == '__main__':
    unittest.main()
