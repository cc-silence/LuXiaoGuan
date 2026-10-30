"""Execute generated preflight deletion against local fake iw, never a router."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.auto_capture_cleanup import monitor_removal_command,verified_monitor_result
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
from modules.capture_services.openwrt.models import OpenWrtRadioInfo

BASH=shutil.which('bash') or ('C:/Program Files/Git/bin/bash.exe' if Path('C:/Program Files/Git/bin/bash.exe').is_file() else None)

class MonitorPreflightTests(unittest.TestCase):
    @unittest.skipUnless(BASH,'POSIX test shell unavailable')
    def test_real_shell_absent_wrong_type_phy_delete_and_postcheck(self):
        cases=[('absent',0,'ABSENT'),('present',0,'REMOVED'),('info_failure',41,'query denied'),
               ('wrong_type',42,''),('wrong_phy',43,''),('delete_failure',44,'delete denied'),
               ('still_present',45,'')]
        for mode,code,expected in cases:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve()
                target=root/'hcxmon1'
                self.assertEqual(target.parent.resolve(),root)
                if mode!='absent':target.touch()
                posix=target.as_posix()
                if target.drive:posix='/'+target.drive[0].lower()+posix[2:]
                quoted="'"+posix.replace("'","'\\''")+"'"
                command=monitor_removal_command('hcxmon1','phy7').replace('/sys/class/net/hcxmon1',posix)
                # Only a disposable test file can be removed by the fake executable.
                prelude="""iw() {
case "$3" in
 info)
  if [ "$mode" = info_failure ]; then printf 'query denied' >&2; return 1; fi
  printf 'Interface hcxmon1\\n'
  if [ "$mode" = wrong_type ]; then printf ' type managed\\n'; else printf ' type monitor\\n'; fi
  if [ "$mode" = wrong_phy ]; then printf ' wiphy 8\\n'; else printf ' wiphy 7\\n'; fi;;
 del)
  if [ "$mode" = delete_failure ]; then printf 'delete denied' >&2; return 1; fi
  if [ "$mode" != still_present ]; then command rm -f -- """+quoted+"""; fi;;
 *) printf 'unexpected iw call' >&2; return 99;;
esac
}
"""
                raw=subprocess.run([BASH,'-c','mode='+mode+'; '+prelude+command],capture_output=True,text=True,timeout=10)
                self.assertEqual(raw.returncode,code,raw.stderr)
                self.assertIn(expected,raw.stdout+raw.stderr)
                result=RemoteCommandResult('sweep_stale',raw.returncode,raw.stdout,raw.stderr)
                if code:
                    with self.assertRaises(RuntimeError) as error:verified_monitor_result(result)
                    self.assertIn('exit='+str(code),str(error.exception))
                    if expected:self.assertIn(expected,str(error.exception))
                else:self.assertEqual(verified_monitor_result(result),expected)

    def test_true_preflight_error_stops_before_new_interface_is_created(self):
        runner=Mock()
        def run(name,*args,**kwargs):
            if name=='find_hcx_bin':return RemoteCommandResult(name,0,'/usr/sbin/hcxdumptool')
            if name=='sweep_stale':return RemoteCommandResult(name,44,'','permission denied')
            return RemoteCommandResult(name,0)
        runner.run.side_effect=run
        service=Mock()
        session=AutoCaptureRadioSession(OpenWrtRadioInfo(name='radio1',interface='phy7'),runner,Mock(),Path('.'),service)
        with patch('modules.capture_services.openwrt.auto_capture_service.OpenWrtRadioTakeover') as takeover:
            takeover.return_value.resolve_phy.return_value=('phy7',())
            session._run_capture_pipeline('',False)
        self.assertEqual(session.stats.state,'error')
        self.assertIn('permission denied',session.stats.error_message)
        names=[call.args[0] for call in runner.run.call_args_list]
        self.assertNotIn('add_mon_iface',names)
        self.assertNotIn('cleanup_stale_ap',names)
        self.assertFalse(session._monitor_creation_attempted)
        service.diagnostics.recovery.assert_called()

    def test_timeout_and_cancel_cannot_be_treated_as_absent(self):
        for result,error in ((RemoteCommandResult('sweep_stale',0,'ABSENT',cancelled=True),InterruptedError),
                             (RemoteCommandResult('sweep_stale',0,'ABSENT',timed_out=True),TimeoutError)):
            with self.assertRaises(error):verified_monitor_result(result)
