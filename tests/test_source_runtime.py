"""Source entry points must use the checkout runtime rather than the global Qt installation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from modules.platform_services import source_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]


class SourceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = self.root / '.venv'
        self.environment.mkdir()
        (self.environment / 'pyvenv.cfg').write_text('fixture', encoding='utf-8')
        for relative in ('Scripts/python.exe', 'Scripts/pythonw.exe', 'bin/python'):
            path = self.environment / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'fixture')
        self.arguments = dict(executable='C:/Python/python.exe', prefix='C:/Python',
                              base_prefix='C:/Python', platform='win32')

    def select(self, **changes):
        return runtime.source_interpreter(self.root, **(self.arguments | changes))

    def test_console_and_windowed_launchers_use_matching_local_interpreter(self):
        self.assertEqual(self.select(), self.environment / 'Scripts/python.exe')
        self.assertEqual(self.select(executable='C:/Python/pythonw.exe'), self.environment / 'Scripts/pythonw.exe')

    def test_explicit_environment_and_frozen_worker_do_not_relaunch(self):
        self.assertIsNone(self.select(prefix=str(self.environment)))
        self.assertIsNone(self.select(prefix=str(self.root / 'other-env')))
        self.assertIsNone(self.select(frozen=True))

    def test_linux_uses_local_python_without_windows_executable_names(self):
        self.assertEqual(self.select(platform='linux', executable='/usr/bin/python3',
                                     prefix='/usr', base_prefix='/usr'), self.environment / 'bin/python')

    def test_unconfigured_checkout_is_unchanged_and_incomplete_environment_is_reported(self):
        (self.environment / 'pyvenv.cfg').unlink()
        self.assertIsNone(self.select())
        (self.environment / 'pyvenv.cfg').write_text('fixture', encoding='utf-8')
        (self.environment / 'Scripts/python.exe').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            self.select()

    def test_handoff_keeps_arguments_and_does_not_import_qt(self):
        candidate = self.environment / 'Scripts/pythonw.exe'
        argv = [str(ROOT / 'launch.pyw'), '--hidden-command-worker', 'request file.json']
        with patch.object(runtime, 'source_interpreter', return_value=candidate), \
                patch.object(runtime.sys, 'argv', argv), patch.object(runtime.os, 'execv') as execute:
            runtime.ensure_source_runtime()
        execute.assert_called_once_with(str(candidate), [str(candidate), str(ROOT / 'launch.pyw'),
                                                         '--hidden-command-worker', 'request file.json'])


@unittest.skipUnless(sys.platform == 'win32', 'Windows source entry points')
class RealSourceEntryTests(unittest.TestCase):
    def test_global_console_and_windowed_python_reach_real_terminal_smoke(self):
        base = Path(sys.base_prefix)
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env.update(QT_QPA_PLATFORM='offscreen', PYTHONIOENCODING='utf-8',
                       ROUTERMANAGER_USER_CONFIG_DIR=temporary)
            for python, entry in ((base / 'python.exe', 'main.py'),
                                  (base / 'python.exe', 'launch.pyw'),
                                  (base / 'pythonw.exe', 'launch.pyw')):
                with self.subTest(python=python.name, entry=entry):
                    result = subprocess.run([str(python), str(ROOT / entry), '--smoke-test-terminal'],
                                            cwd=ROOT.parent, env=env, capture_output=True, timeout=30,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                    self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
