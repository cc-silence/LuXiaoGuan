"""Actual selected-radio business evidence and consent policy."""
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.radio_business import read_radio_business,require_radio_business_consent
from modules.capture_services.scan_management_service import ScanManagementPlan
from modules.capture_services.openwrt.management_link import ManagementLink

def runner(rows="",code=0,**flags):
    value=Mock()
    text="__RADIO_BUSINESS_BEGIN__\n"+rows+"__RADIO_BUSINESS_END__\n"
    value.run.return_value=RemoteCommandResult("radio_business_probe",code,text,**flags)
    return value

class RadioBusinessTests(unittest.TestCase):
    def test_wired_management_does_not_warn_about_active_relay(self):
        modes=read_radio_business(runner("phy0-sta0|managed|1\n"),"phy0")
        plan=ScanManagementPlan("phy0",ManagementLink("wired","eth0"),"fingerprint",modes)
        self.assertFalse(plan.at_risk);self.assertFalse(plan.management_at_risk)
        self.assertEqual(plan.business_modes,("managed",))

    def test_disabled_interfaces_and_monitor_are_not_business(self):
        self.assertEqual(read_radio_business(runner("sta0|managed|0\nmon0|monitor|1\n"),"phy0"),())
        self.assertEqual(read_radio_business(runner(),"phy0"),())

    def test_business_requires_explicit_consent_and_new_modes_invalidate_it(self):
        value=runner("sta0|managed|1\nap0|AP|1\n")
        for approved in ((),["managed"],None,"managed"):
            with self.assertRaises(RuntimeError):require_radio_business_consent(value,"phy0",approved)
        self.assertEqual(require_radio_business_consent(value,"phy0",["managed","ap"]),("ap","managed"))

    def test_malformed_failed_cancelled_and_timeout_never_claim_idle(self):
        for value in (runner("bad\n"),runner(code=81),runner(cancelled=True),runner(timed_out=True)):
            with self.assertRaises((RuntimeError,InterruptedError,TimeoutError)):read_radio_business(value,"phy0")
        with self.assertRaises(ValueError):read_radio_business(runner(),"phy0;reboot")

    def test_probe_targets_the_requested_physical_radio(self):
        value=runner()
        read_radio_business(value,"phy7")
        command=value.run.call_args.args[1]
        self.assertIn("phy=phy7",command)
        self.assertIn('test "$owner" = "$phy"',command)
        self.assertIn('test "phy$index" = "$phy"',command)
        self.assertNotIn("uci ",command)

class RadioBusinessShellTests(unittest.TestCase):
    def test_native_probe_only_reports_matching_phy_and_actual_up_flags(self):
        import os,subprocess,tempfile,shlex
        from pathlib import Path
        from modules.capture_services.openwrt.radio_business import PROBE
        bash=Path("C:/Program Files/Git/bin/bash.exe")
        if not bash.is_file():self.skipTest("Git Bash not installed")
        with tempfile.TemporaryDirectory(prefix="radio-business-") as folder:
            root=Path(folder);(root/"phys/phy7").mkdir(parents=True)
            for name,phy,flags in (("sta7","phy7","0x1003"),("ap7","phy7","0x1002"),("sta8","phy8","0x1003")):
                path=root/"net"/name
                (path/"phy80211").mkdir(parents=True)
                (path/"phy80211/name").write_text(phy+"\n",encoding="ascii")
                (path/"flags").write_text(flags+"\n",encoding="ascii")
            native=root.as_posix()
            posix="/"+native[0].lower()+native[2:] if os.name=="nt" else native
            command=PROBE.replace("__PHY__","phy7").replace("/sys/class/ieee80211/",shlex.quote(posix+"/phys")+"/").replace(
                "/sys/class/net/*",shlex.quote(posix+"/net")+"/*")
            setup='iw(){ case "$2" in sta7) printf "Interface sta7\\n wiphy 7\\n type managed\\n";; ap7) printf "Interface ap7\\n wiphy 7\\n type AP\\n";; *) return 1;; esac; }\n'
            result=subprocess.run([str(bash),"--noprofile","--norc"],input=setup+command,
                text=True,capture_output=True,encoding="utf-8",timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn("sta7|managed|1",result.stdout)
            self.assertIn("ap7|AP|0",result.stdout)
            self.assertNotIn("sta8",result.stdout)
            fixture=Mock()
            fixture.run.return_value=RemoteCommandResult("radio_business_probe",0,result.stdout)
            self.assertEqual(read_radio_business(fixture,"phy7"),("managed",))
