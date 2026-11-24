"""Actual POSIX cleanup contract; no router access or dependency on packet tools."""
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from types import SimpleNamespace as NS
from unittest.mock import Mock
from modules.capture_services.openwrt.handshake_remote_files import release_command,release_downloaded,storage_inventory_command
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.wireless_diagnostics import collect_tool_failure_diagnostics

class RemoteFilesTests(unittest.TestCase):
    def shell(self, body):
        bash = str(Path(os.environ.get("ProgramFiles","C:/Program Files"))/"Git/bin/bash.exe") if os.name=="nt" else shutil.which("bash")
        if not bash or not Path(bash).is_file():self.skipTest("POSIX shell unavailable")
        distro=os.environ.get("LXG_TEST_POSIX_WSL_DISTRO")
        command=["wsl.exe","-d",distro,"--exec","/bin/sh","-s"] if os.name=="nt" and distro else [bash,"-s"]
        done=subprocess.run(command,input=body.encode("utf-8"),capture_output=True,timeout=15)
        if done.returncode == 77:
            self.skipTest("Selected POSIX shell emulates symbolic links; use a native Linux shell")
        self.assertEqual(done.returncode,0,(done.stdout+done.stderr).decode("utf-8",errors="replace"))

    def scenario(self, check):
        root="/tmp/lxg-cleanup-"+uuid4().hex
        with TemporaryDirectory() as folder:
            local=Path(folder)/"copied.cap";local.write_bytes(b"payload")
            command=release_command(root+"/handshake-r1t0",local)
            setup=("set -e\nmkdir '"+root+"'\ntrap 'rm -rf -- "+root+"' EXIT\n"
                   "printf payload >"+root+"/handshake-r1t0-01.cap\n"
                   "printf clients >"+root+"/handshake-r1t0-01.csv\n"
                   "printf log >"+root+"/airodump-ng.log\n"
                   "printf hint >"+root+"/handshake-hint.txt\n"
                   "printf backup >"+root+"/wireless-backup.json\n"
                   "printf active >"+root+"/handshake-r1t1-01.cap\n")
            self.shell(setup+check(root,command))
            self.assertEqual(local.read_bytes(),b"payload")

    def test_complete_copy_frees_only_stopped_group_and_normal_logs(self):
        self.scenario(lambda root,command:command+"\ntest ! -e "+root+"/handshake-r1t0-01.cap\ntest ! -e "+root+"/airodump-ng.log\ntest ! -e "+root+"/handshake-hint.txt\ntest -s "+root+"/wireless-backup.json\ntest -s "+root+"/handshake-r1t1-01.cap\n")

    def test_size_mismatch_preserves_remote_capture_and_logs(self):
        self.scenario(lambda root,command:"printf changed-data >"+root+"/handshake-r1t0-01.cap\nset +e\n( "+command+" ); code=$?\nset -e\ntest $code -eq 73\ntest -s "+root+"/handshake-r1t0-01.cap\ntest -s "+root+"/airodump-ng.log\n")

    def test_link_refusal_happens_before_any_deletion(self):
        self.scenario(lambda root,command:"rm "+root+"/airodump-ng.log\nln -s wireless-backup.json "+root+"/airodump-ng.log\n[ -L "+root+"/airodump-ng.log ] || exit 77\nset +e\n( "+command+" ); code=$?\nset -e\ntest $code -eq 72\ntest -s "+root+"/handshake-r1t0-01.cap\ntest -s "+root+"/wireless-backup.json\n")

    def test_inventory_is_read_only_and_lists_file_sizes(self):
        self.scenario(lambda root,command:storage_inventory_command(root)+"\ntest -s "+root+"/handshake-r1t0-01.cap\n")

    def test_invalid_prefixes_are_rejected(self):
        for prefix in ("/handshake","relative/handshake","/tmp/../handshake","/tmp/owned/wireless-backup"):
            with self.subTest(prefix=prefix),self.assertRaises(ValueError):release_command(prefix)

    def test_no_download_cannot_release_nonempty_capture(self):
        self.scenario(lambda root,command:"set +e\n( "+release_command(root+"/handshake-r1t0")+" ); code=$?\nset -e\ntest $code -eq 73\ntest -s "+root+"/handshake-r1t0-01.cap\n")

    def test_cleanup_failure_keeps_local_copy(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/"copy.cap";path.write_bytes(b"payload")
            runner=Mock();runner.run.return_value=RemoteCommandResult("release",73)
            with self.assertRaises(RuntimeError):release_downloaded(runner,"/tmp/owned/handshake",path)
            self.assertEqual(path.read_bytes(),b"payload")

    def test_repeated_failures_keep_distinct_local_diagnostics(self):
        with TemporaryDirectory() as folder:
            runner=Mock();runner.run.return_value=RemoteCommandResult("diag",0,"first")
            first=collect_tool_failure_diagnostics(runner,NS(local_dir=Path(folder)),None,"airodump")
            runner.run.return_value=RemoteCommandResult("diag",0,"second")
            second=collect_tool_failure_diagnostics(runner,NS(local_dir=Path(folder)),None,"airodump")
            self.assertNotEqual(first,second)
            self.assertIn("first",(Path(folder)/first).read_text(encoding="utf-8"))
            self.assertIn("second",(Path(folder)/second).read_text(encoding="utf-8"))
            self.assertTrue(first.endswith("-wireless-diag.txt"))
