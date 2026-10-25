"""Run process cleanup against a fake procfs; never signal host processes."""
import os,shlex,shutil,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.remote_process import RemoteProcessManager,RemoteProcessHandle
BASH=Path('C:/Program Files/Git/bin/bash.exe') if os.name=='nt' else Path(shutil.which('bash') or '/nonexistent/bash')

class ProcessFixture:
    def __init__(self,root):
        self.root=root;self.calls=[];self.reuse=False
        (root/'proc').mkdir();(root/'work/finished').mkdir(parents=True)
    def process(self,pid,argv,token='123'):
        path=self.root/'proc'/str(pid);path.mkdir(exist_ok=True)
        raw=self.root.as_posix();posix='/'+raw[0].lower()+raw[2:] if os.name=='nt' else raw
        argv=[a.replace('/tmp/winbox-capture',posix+'/work') for a in argv]
        (path/'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv)+b'\0')
        (path/'stat').write_text(str(pid)+' (fixture worker) '+' '.join(['S']+['0']*18+[token])+'\n',encoding='utf-8')
    def run(self,name,command,**kwargs):
        self.calls.append((name,command))
        raw=self.root.as_posix();posix='/'+raw[0].lower()+raw[2:] if os.name=='nt' else raw
        code=command.replace('/proc/',posix+'/proc/').replace('/tmp/winbox-capture',posix+'/work')
        setup='ROOT='+shlex.quote(posix)+'\nREUSE='+str(int(self.reuse))+'\n'+r'''
kill() {
 printf '%s\n' "$*" >>"$ROOT/signals"
 if [ "$1" = -9 ]; then
  rm -f "$ROOT/proc/$2/stat" "$ROOT/proc/$2/cmdline"
  rmdir "$ROOT/proc/$2"
 elif [ "$REUSE" = 1 ]; then
  printf '100 (replacement) S 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 456\n' >"$ROOT/proc/100/stat"
 fi
}
sleep(){ return 0; }
df(){ printf 'df\n' >>"$ROOT/budget"; printf 'header\nroot 100000 1 99999 1%% /tmp\n'; }
du(){ printf 'du\n' >>"$ROOT/budget"; printf '1\t%s\n' "$1"; }
'''
        result=subprocess.run([str(BASH),'--noprofile','--norc'],input=setup+code,text=True,encoding='utf-8',capture_output=True,timeout=10)
        return RemoteCommandResult(name,result.returncode,result.stdout,result.stderr)
    def signals(self):
        p=self.root/'signals';return p.read_text().splitlines() if p.exists() else []

@unittest.skipUnless(BASH.is_file(),'Bash unavailable')
class ProcessRecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.device=ProcessFixture(Path(self.temp.name));self.manager=RemoteProcessManager(self.device)
    def test_reader_of_capture_log_is_never_a_stale_capture_process(self):
        self.device.process(100,['/usr/bin/tail','-f','/tmp/winbox-capture/finished/hcxdumptool.log'])
        self.assertEqual(self.manager.reap_stale_processes(force_sids=('finished',)),0)
        self.assertEqual(self.device.signals(),[])
    def test_pid_reuse_after_term_never_receives_kill(self):
        self.device.process(100,['/usr/sbin/hcxdumptool','-w','/tmp/winbox-capture/finished/capture.pcapng'])
        self.device.reuse=True
        self.assertEqual(self.manager.reap_stale_processes(force_sids=('finished',)),1)
        self.assertEqual(self.device.signals(),['100'])
    def test_real_stale_tool_is_stopped_and_other_sessions_are_untouched(self):
        self.device.process(100,['/usr/sbin/hcxdumptool','-w','/tmp/winbox-capture/finished/capture.pcapng'])
        self.device.process(101,['airodump-ng','--write','/tmp/winbox-capture/active/scan'])
        self.assertEqual(self.manager.reap_stale_processes(force_sids=('finished',)),1)
        self.assertEqual(self.device.signals(),['100','-9 100'])
        self.assertTrue((self.device.root/'proc/101').exists())
    def test_empty_finished_list_never_signals_any_tool(self):
        self.device.process(100,['hcxdumptool','-w','/tmp/winbox-capture/finished/capture.pcapng'])
        self.assertEqual(self.manager.reap_stale_processes(),0)
        self.assertEqual(self.device.signals(),[])
    def test_normal_poll_reads_each_resource_budget_once(self):
        self.device.process(100,['airodump-ng','--write','/tmp/winbox-capture/finished/scan'])
        handle=RemoteProcessHandle(100,'123','airodump-ng','finished','/tmp/winbox-capture/finished')
        self.assertTrue(self.manager._identity_running(handle))
        self.assertEqual((self.device.root/'budget').read_text().splitlines(),['df','du'])
