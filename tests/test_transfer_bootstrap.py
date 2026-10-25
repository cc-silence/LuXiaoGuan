"""Run both command-upload codecs in a real shell with binary payloads."""
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.signal_manager import SignalError


class TransferBootstrapTests(unittest.TestCase):
    def test_binary_upload_with_and_without_external_base64(self):
        shell = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
        self.assertTrue(Path(shell).is_file(), "real POSIX shell required")
        for codec in ("base64", "printf"):
            for payload in (bytes(range(256))*39+b"\x00\xff", b""):
                with self.subTest(codec=codec, size=len(payload)), tempfile.TemporaryDirectory() as folder:
                    local = Path(folder)/"package.ipk"
                    local.write_bytes(payload)
                    target = Path(folder)/"remote package.ipk"
                    # Git Bash accepts /c/... paths, and POSIX hosts use their native path.
                    remote = target.as_posix()
                    if len(remote)>1 and remote[1]==":":
                        remote = "/"+remote[0].lower()+remote[2:]
                    manager = OpenWrtDependencyManager(Mock())
                    commands = []
                    def execute(name, command, *args):
                        commands.append((name, command))
                        if name == "dependency_push_codec":
                            return codec
                        result = subprocess.run([shell, "--noprofile", "--norc", "-s"],
                                                input=command.encode(), capture_output=True, timeout=10)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        return result.stdout.decode()
                    manager._execute = Mock(side_effect=execute)
                    with patch.object(Path, "read_bytes", side_effect=AssertionError("whole package read")):
                        manager._exec_push(local, remote, Event())
                    self.assertEqual(target.read_bytes(), payload)
                    self.assertLess(max(len(command) for _,command in commands),9*1024)
                    self.assertEqual(any(name=="dependency_push_join" for name,_ in commands),codec=="base64")

    def test_corruption_is_rejected_by_sha256(self):
        manager = OpenWrtDependencyManager(Mock())
        manager._execute = Mock(return_value="printf")
        with tempfile.TemporaryDirectory() as folder:
            local = Path(folder)/"package.ipk"
            local.write_bytes(b"important package")
            with self.assertRaises(SignalError):
                manager._exec_push(local,"/tmp/package.ipk",Event())


if __name__ == "__main__":
    unittest.main()
