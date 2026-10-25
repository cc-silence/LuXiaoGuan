"""Real POSIX ownership commands and simulated lost SSH acknowledgements."""
import os,re,shlex,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from modules.capture_services.openwrt.radio_session import OpenWrtRadioSession
from modules.capture_services.openwrt.command_runner import RemoteCommandResult,CommandExecutionTimeout

BASH=Path("C:/Program Files/Git/bin/bash.exe")


class KernelFixture:
    def __init__(self,root):
        self.root=root;root.mkdir();self.calls=[];self.timeout=False;self.cancel=False;self.execute_add=True;self.fail_add=False
    def run(self,name,command,timeout=10,cancel_event=None):
        self.calls.append(name)
        if name=="create_monitor" and not self.execute_add:
            return RemoteCommandResult(name,-1,timed_out=True)
        text=self.root.as_posix();posix="/"+text[0].lower()+text[2:] if os.name=="nt" else text
        setup="ROOT="+shlex.quote(posix)+"\n"+r'''
iw() {
 if [ "$1" = phy ]; then
  [ "$FAIL_ADD" != 1 ] || return 1
  p="$ROOT/$5"; mkdir -p "$p/phy80211" || return 1
  printf %s "$2" >"$p/phy80211/name"
 elif [ "$1" = dev ] && [ "$3" = del ]; then
  p="$ROOT/$2"
  rm -f "$p/ifalias" "$p/phy80211/name"
  rmdir "$p/phy80211" && rmdir "$p"
 else return 1; fi
}
ip(){ return 0; }
'''
        setup+=("FAIL_ADD=1\n" if self.fail_add else "")
        code=command.replace("/sys/class/net",posix)
        result=subprocess.run([str(BASH),"--noprofile","--norc"],input=setup+code,text=True,encoding="utf-8",capture_output=True,timeout=10)
        if name=="create_monitor" and (self.timeout or self.cancel):
            return RemoteCommandResult(name,-1,timed_out=self.timeout,cancelled=self.cancel)
        return RemoteCommandResult(name,result.returncode,result.stdout,result.stderr)


@unittest.skipUnless(BASH.is_file(),"Git Bash unavailable")
class MonitorCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.device=KernelFixture(Path(self.temp.name)/"net");self.session=OpenWrtRadioSession(self.device)
        self.cap=patch("modules.capture_services.openwrt.radio_session.probe_monitor_capability",return_value=(True,"supported"));self.cap.start();self.addCleanup(self.cap.stop)
        self.status=patch("modules.capture_services.openwrt.radio_session.read_monitor_status",return_value="ready");self.status_mock=self.status.start();self.addCleanup(self.status.stop)
    def create(self):return self.session.create("audit1234","phy0","2.4G")

    def test_success_marks_owner_and_guarded_cleanup_removes_only_own_interface(self):
        record=self.create();path=self.device.root/record.interface
        self.assertEqual((path/"ifalias").read_text(),record.ownership_token)
        self.assertFalse(record.creation_pending)
        self.assertTrue(self.session.restore());self.assertFalse(path.exists());self.assertIsNone(self.session.record)

    def test_lost_add_ack_keeps_record_and_actual_owner_can_be_cleaned(self):
        self.device.timeout=True
        with self.assertRaises(CommandExecutionTimeout):self.create()
        record=self.session.record
        self.assertIsNotNone(record);self.assertTrue(record.creation_pending)
        self.assertTrue((self.device.root/record.interface).exists())
        self.assertTrue(self.session.restore());self.assertIsNone(self.session.record)

    def test_cancelled_add_ack_keeps_recovery_record(self):
        self.device.cancel=True
        with self.assertRaises(InterruptedError):self.create()
        self.assertIsNotNone(self.session.record)
        self.assertTrue(self.session.restore())

    def test_unconfirmed_add_with_no_visible_interface_does_not_claim_restored(self):
        self.device.execute_add=False
        with self.assertRaises(CommandExecutionTimeout):self.create()
        record=self.session.record
        with self.assertRaises(RuntimeError):self.session.restore()
        self.assertIs(self.session.record,record)

    def test_existing_name_is_never_claimed_or_deleted(self):
        path=self.device.root/"wbmonaudit123";path.mkdir();(path/"ifalias").write_text("foreign")
        with self.assertRaises(RuntimeError):self.create()
        self.assertNotIn("create_monitor",self.device.calls);self.assertIsNone(self.session.record)
        self.assertEqual((path/"ifalias").read_text(),"foreign")

    def test_replaced_interface_owner_or_phy_is_never_deleted(self):
        for field,value in (("ifalias","foreign"),("phy80211/name","phy9")):
            with self.subTest(field=field):
                record=self.create();path=self.device.root/record.interface
                original=(path/field).read_text();(path/field).write_text(value)
                with self.assertRaises(RuntimeError):self.session.restore()
                self.assertTrue(path.exists());self.assertIs(self.session.record,record)
                (path/field).write_text(original);self.session.restore()

    def test_known_iw_failure_does_not_leave_false_owned_interface(self):
        self.device.fail_add=True
        with self.assertRaises(RuntimeError):self.create()
        self.assertIsNone(self.session.record);self.assertFalse(any(self.device.root.iterdir()))

    def test_missing_confirmed_interface_is_cleaned_but_pending_is_retained(self):
        record=self.create();path=self.device.root/record.interface
        (path/"ifalias").unlink();(path/"phy80211/name").unlink();(path/"phy80211").rmdir();path.rmdir()
        self.assertTrue(self.session.restore());self.assertIsNone(self.session.record)

    def test_recovery_recreates_and_restores_ownership_marker(self):
        record=self.create();path=self.device.root/record.interface
        (path/"ifalias").unlink();(path/"phy80211/name").unlink();(path/"phy80211").rmdir();path.rmdir()
        self.status_mock.return_value="missing"
        def state(*args,**kwargs):
            return "ready" if "recover_monitor_up" in self.device.calls else "missing"
        self.status_mock.side_effect=state
        self.assertTrue(self.session.recover()[0])
        self.assertEqual((path/"ifalias").read_text(),record.ownership_token)
        self.assertTrue(self.session.restore())

    def _assert_replacement_refused(self,field,value):
        run=self.device.run
        def replace_before_activation(name,command,**kwargs):
            if name=="activate_monitor":
                path=self.device.root/self.session.record.interface
                (path/field).write_text(value,encoding="utf-8")
            return run(name,command,**kwargs)
        self.device.run=replace_before_activation
        with self.assertRaises(RuntimeError):self.create()
        record=self.session.record
        self.assertIsNotNone(record)
        path=self.device.root/record.interface
        self.assertEqual((path/field).read_text(encoding="utf-8"),value)
        self.assertTrue(path.exists())
        # Restore fixture ownership to verify ordinary recovery still works.
        (path/field).write_text(record.ownership_token if field=="ifalias" else record.radio,encoding="utf-8")
        self.device.run=run
        self.assertTrue(self.session.restore())

    def test_replaced_owner_before_initial_activation_is_not_used_or_deleted(self):
        self._assert_replacement_refused("ifalias","foreign")

    def test_replaced_phy_before_initial_activation_is_not_used_or_deleted(self):
        self._assert_replacement_refused("phy80211/name","phy9")
