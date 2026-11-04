"""A Windows shell test must never accidentally enable or enter WSL."""
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import posix_shell


class LocalShellTests(unittest.TestCase):
    def test_linux_uses_native_shell_and_bash_when_required(self):
        with patch.object(posix_shell.sys,'platform','linux'),patch.object(Path,'is_file',return_value=True):
            self.assertEqual(posix_shell.local_shell(),Path('/bin/sh'))
            self.assertEqual(posix_shell.local_shell(bash=True),Path('/bin/bash'))

    def test_windows_uses_only_explicit_git_shell(self):
        with patch.object(posix_shell.sys,'platform','win32'),patch.dict(posix_shell.os.environ,{'ProgramFiles':'GitParent'}),patch.object(Path,'is_file',return_value=True):
            self.assertEqual(posix_shell.local_shell(),Path('GitParent/Git/bin/bash.exe'))

    def test_missing_or_unsupported_shell_does_not_fall_back_to_path_search(self):
        with patch.object(posix_shell.sys,'platform','win32'),patch.object(Path,'is_file',return_value=False):
            self.assertIsNone(posix_shell.local_shell())
        with patch.object(posix_shell.sys,'platform','other'):
            self.assertIsNone(posix_shell.local_shell())


if __name__=='__main__':unittest.main()
