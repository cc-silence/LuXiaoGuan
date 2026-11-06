"""Run the Lua-generated directory-listing command in a real shell."""
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from tests import test_router_policy as policy_tests

_BASH = Path('C:/Program Files/Git/bin/bash.exe')
SHELL = str(_BASH) if _BASH.is_file() else shutil.which('bash')


@unittest.skipUnless(policy_tests.LuaRuntime and SHELL, 'Lua or bash unavailable')
class InventoryShellTests(unittest.TestCase):
    def test_generated_shell_distinguishes_empty_directory_from_failed_find(self):
        device = policy_tests.PolicyDevice()
        self.addCleanup(device.recovery_temp.cleanup)
        commands = []
        original = device.read
        def read(command):
            if command.startswith('find /root -maxdepth 1 -name'):
                commands.append(command)
            return original(command)
        device.lua.globals().bridge_read = read
        device.policy._call('files_inventory', device.cancel)
        self.assertEqual(len(commands), 1)
        marker = b'\nLXG_POLICY_INVENTORY_OK\n'
        with tempfile.TemporaryDirectory() as folder:
            for exists, path in ((True, Path(folder)), (False, Path(folder) / 'not-created')):
                with self.subTest(exists=exists):
                    command = commands[0].replace('/root', shlex.quote(path.as_posix()), 1)
                    result = subprocess.run([SHELL, '-c', command], capture_output=True, timeout=10)
                    if exists:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, marker)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertNotIn(marker, result.stdout)
