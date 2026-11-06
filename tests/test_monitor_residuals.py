"""Execute the actual recovery shell against a fake kernel, never a router."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.monitor_residuals import recover_idle_monitors, login_monitor_cleanup

SHELL = str(Path("C:/Program Files/Git/bin/bash.exe")) if os.name == "nt" else shutil.which("sh")
OWNER = "routermanager:" + "a" * 32


class Router:
    def __init__(self, root):
        self.root = root
        (root / "net").mkdir()
        (root / "proc/net").mkdir(parents=True)
        (root / "proc/net/packet").write_text("sk RefCnt Type Proto Iface R Rmem User Inode\n")
        self.replace_owner = False
        self.delete_noop = False
        self.names = []

    def interface(self, name="wbmon12345678", owner=OWNER, kind="monitor"):
        p = self.root / "net" / name
        (p / "phy80211").mkdir(parents=True)
        for key, value in (("ifalias", owner), ("ifindex", "26"), ("phy80211/name", "phy0"), ("kind", kind)):
            (p / key).write_text(value)
        return p

    def run(self, name, script, **kwargs):
        self.names.append(name)
        def posix(p):
            text=p.as_posix()
            return "/"+text[0].lower()+text[2:] if os.name=="nt" else text
        root = posix(self.root)
        setup = "ROOT="+shlex.quote(root)+"\n"
        setup += "REPLACE="+str(int(self.replace_owner))+"\nNOOP="+str(int(self.delete_noop))+"\n"
        setup += r'''iw() {
 [ "$1" = dev ] || return 1
 d="$ROOT/net/$2"
 if [ "$3" = info ]; then
  printf 'type %s\n' "$(cat "$d/kind")"
  [ "$REPLACE" = 0 ] || printf foreign >"$d/ifalias"
  return 0
 fi
 [ "$3" = del ] || return 1
 [ "$NOOP" = 0 ] || return 0
 rm "$d/ifalias" "$d/ifindex" "$d/kind" "$d/phy80211/name" && rmdir "$d/phy80211" "$d"
}
'''
        script = script.replace("/sys/class/net", root+"/net").replace("/proc/", root+"/proc/")
        result = subprocess.run([SHELL], input=setup+script, text=True, encoding="utf-8", capture_output=True, timeout=8)
        return RemoteCommandResult(name, result.returncode, result.stdout, result.stderr)


@unittest.skipUnless(SHELL and Path(SHELL).is_file(), "POSIX shell unavailable")
class ResidualShellTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.router = Router(Path(temp.name))

    def test_idle_owned_interface_is_deleted_and_verified(self):
        p=self.router.interface()
        self.assertEqual(recover_idle_monitors(self.router), (1,0))
        self.assertFalse(p.exists())
        self.assertEqual(recover_idle_monitors(self.router), (0,0))

    def test_unknown_owner_and_non_capture_interfaces_remain(self):
        for name in ("wbmon12345678", "hcxmon0", "lan", "wan", "wwan", "mon0"):
            self.router.interface(name, "foreign")
        self.assertEqual(recover_idle_monitors(self.router), (0,1))
        self.assertEqual(len(list((self.router.root/"net").iterdir())),6)

    def test_active_packet_socket_keeps_interface(self):
        for index in (26,0):
            with self.subTest(index=index):
                if not (self.router.root/"net/wbmon12345678").exists():self.router.interface()
                (self.router.root/"proc/net/packet").write_text(f"sk RefCnt Type Proto Iface R Rmem User Inode\n0 3 3 0003 {index} 1 0 0 123\n")
                self.assertEqual(recover_idle_monitors(self.router), (0,1))

    def test_live_process_interface_argument_is_kept(self):
        self.router.interface()
        process=self.router.root/"proc/123";process.mkdir()
        (process/"cmdline").write_bytes(b"airodump-ng\0--interface=wbmon12345678\0")
        self.assertEqual(recover_idle_monitors(self.router), (0,1))

    def test_unreadable_process_and_invalid_packet_table_are_not_idle(self):
        self.router.interface()
        (self.router.root/"proc/123").mkdir()
        self.assertEqual(recover_idle_monitors(self.router), (0,1))
        (self.router.root/"proc/123").rmdir()
        (self.router.root/"proc/net/packet").write_text("unexpected format\n")
        self.assertEqual(recover_idle_monitors(self.router), (0,1))

    def test_empty_packet_table_is_not_proof_of_idle(self):
        self.router.interface()
        (self.router.root/"proc/net/packet").write_text("")
        self.assertEqual(recover_idle_monitors(self.router),(0,1))

    def test_protected_current_record_and_business_interface_are_kept(self):
        self.router.interface()
        self.assertEqual(recover_idle_monitors(self.router,protected=("wbmon12345678",)),(0,1))
        (self.router.root/"net/wbmon12345678/kind").write_text("AP")
        self.assertEqual(recover_idle_monitors(self.router),(0,1))

    def test_identity_replaced_during_inspection_is_not_deleted(self):
        self.router.interface();self.router.replace_owner=True
        with self.assertRaises(RuntimeError):recover_idle_monitors(self.router)
        self.assertTrue((self.router.root/"net/wbmon12345678").exists())

    def test_delete_success_without_actual_deletion_is_failure(self):
        self.router.interface();self.router.delete_noop=True
        with self.assertRaises(RuntimeError):recover_idle_monitors(self.router)
        self.assertTrue((self.router.root/"net/wbmon12345678").exists())


class ResidualBoundaryTests(unittest.TestCase):
    def test_incomplete_cancel_timeout_and_duplicate_results_fail(self):
        for result in (RemoteCommandResult('x',0,''),RemoteCommandResult('x',0,'done',timed_out=True),
                       RemoteCommandResult('x',0,'done',cancelled=True),
                       RemoteCommandResult('x',0,'removed=wbmon12345678\nremoved=wbmon12345678\ndone')):
            runner=Mock();runner.run.return_value=result
            with self.subTest(result=result),self.assertRaises((RuntimeError,InterruptedError,TimeoutError)):
                recover_idle_monitors(runner)

    def test_login_reports_failures_and_propagates_cancellation(self):
        backend=SimpleNamespace(radio_session=SimpleNamespace(record=None),command_runner=Mock())
        backend.command_runner.run.return_value=RemoteCommandResult('x',1)
        self.assertTrue(login_monitor_cleanup(backend))
        event=Event();event.set()
        with self.assertRaises(InterruptedError):login_monitor_cleanup(backend,event)
