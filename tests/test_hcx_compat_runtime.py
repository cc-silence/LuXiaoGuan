"""Exercise deployment shell locally; no router or radio access."""
import hashlib
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.hcx_compat_runtime import ensure_compatible_engine, local_variant, REMOTE
from tests.test_monitor_residuals import SHELL


def posix(path):
    text=path.as_posix()
    return '/'+text[0].lower()+text[2:] if os.name=='nt' else text


@unittest.skipUnless(SHELL and Path(SHELL).is_file(),'POSIX shell unavailable')
class CompatibilityDeploymentTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.remote=self.root/'router';self.remote.mkdir()
        self.source=self.root/'engine'
        self.source.write_text('#!/bin/sh\nprintf "hcxdumptool 6.3.4-rm-ifindex1\\n"\n',encoding='ascii')
        self.native=self.remote/'native';shutil.copyfile(self.source,self.native)
        subprocess.run([SHELL,'-c','chmod 700 '+shlex.quote(posix(self.native))],check=True)
        self.ssh=SimpleNamespace(connected=True,_client=object(),host_fingerprint='device-A')
        self.device=SimpleNamespace(version='23.05.4',kernel_version='5.15.162',package_architecture='x86_64')
        self.event=Event();self.calls=[];self.corrupt=False;self.cancel_upload=False;self.reconnect_upload=False
        self.fail_cleanup=False
        raw=self.source.read_bytes()
        self.row=dict(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        self.runner=SimpleNamespace(run=self.run_command)

    def run_command(self,name,script,**kwargs):
        self.calls.append(name)
        if name=='hcx_compat_stage_cleanup' and self.fail_cleanup:
            return RemoteCommandResult(name,1,'','cleanup failure')
        script=script.replace('/root',posix(self.remote))
        r=subprocess.run([SHELL],input=script,text=True,encoding='utf-8',capture_output=True,timeout=8)
        return RemoteCommandResult(name,r.returncode,r.stdout,r.stderr)

    def upload(self,path,destination,**kwargs):
        target=self.remote/destination.removeprefix('/root/')
        shutil.copyfile(path,target)
        if self.corrupt:target.write_text('broken',encoding='ascii')
        if self.cancel_upload:self.event.set()
        if self.reconnect_upload:self.ssh._client=object()

    def execute(self):
        with patch('modules.capture_services.openwrt.hcx_compat_runtime.local_variant',return_value=(self.source,self.row)),patch('modules.capture_services.openwrt.workspace.CaptureWorkspaceManager.upload_atomic',side_effect=self.upload):
            return ensure_compatible_engine(self.runner,self.ssh,self.device,self.event,source_bin='/root/native')

    def test_publish_checks_hash_and_reuses_verified_cache(self):
        self.assertEqual(self.execute(),REMOTE)
        installed=self.remote/'.luxiaoguan-tools/hcx-ifindex1'
        self.assertEqual(installed.read_bytes(),self.source.read_bytes())
        self.assertEqual(self.calls.count('hcx_compat_publish'),1)
        self.assertEqual(self.execute(),REMOTE)
        self.assertEqual(self.calls.count('hcx_compat_publish'),1)
        self.assertFalse(list(installed.parent.glob('.hcx-*')))

    def test_bad_upload_preserves_old_tool_and_cleans_stage(self):
        installed=self.remote/'.luxiaoguan-tools/hcx-ifindex1'
        installed.parent.mkdir();installed.write_text('previous',encoding='ascii')
        self.corrupt=True
        with self.assertRaisesRegex(RuntimeError,'hcx_compat_publish'):self.execute()
        self.assertEqual(installed.read_text(),'previous')
        self.assertFalse(list(installed.parent.glob('.hcx-*')))

    def test_cancellation_cleans_stage_without_publishing(self):
        self.cancel_upload=True
        with self.assertRaises(InterruptedError):self.execute()
        self.assertNotIn('hcx_compat_publish',self.calls)
        self.assertFalse(list((self.remote/'.luxiaoguan-tools').glob('.hcx-*')))

    def test_reconnect_never_publishes_or_cleans_new_connection(self):
        self.reconnect_upload=True
        with self.assertRaisesRegex(RuntimeError,'Connection changed'):self.execute()
        self.assertNotIn('hcx_compat_publish',self.calls)
        self.assertNotIn('hcx_compat_stage_cleanup',self.calls)

    def test_cleanup_error_does_not_replace_primary_failure(self):
        self.corrupt=True;self.fail_cleanup=True
        with self.assertRaisesRegex(RuntimeError,'hcx_compat_publish'):self.execute()

    def test_failed_version_command_cannot_authorize_deployment(self):
        self.native.write_text('#!/bin/sh\necho hcxdumptool 6.3.4\nexit 1\n',encoding='ascii')
        with self.assertRaisesRegex(RuntimeError,'version'):self.execute()
        self.assertEqual(self.calls,['hcx_compat_source'])

    def test_old_kernel_is_untouched(self):
        self.device.kernel_version='5.4.0';self.device.version='21.02.7'
        self.assertEqual(self.execute(),'');self.assertEqual(self.calls,[])

    def test_old_native_tool_is_untouched(self):
        self.native.write_text('#!/bin/sh\necho hcxdumptool 6.2.7\n',encoding='ascii')
        self.assertEqual(self.execute(),'')
        self.assertEqual(self.calls,['hcx_compat_source'])


@unittest.skipUnless(SHELL and Path(SHELL).is_file(),'POSIX shell unavailable')
class BackgroundSelectionTests(unittest.TestCase):
    def test_exact_background_functions_keep_old_and_newer_native_tools(self):
        root=Path(__file__).resolve().parents[1]
        script=(root/'tools/scripts/hcx_autocap.sh').read_text(encoding='utf-8')
        functions='\n'.join(re.search(r'^'+name+r'\(\) \{.*?^\}',script,re.M|re.S).group(0) for name in ('hcx_version_line','hcx_probe_help','hcx_compat_kernel_supported','detect_hcx_dialect'))
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);native=base/'hcxdumptool';compat=base/'compat'
            compat.write_text('#!/bin/sh\nprintf "hcxdumptool 6.3.4-rm-ifindex1\\nusage\\n-w output\\n"\n',encoding='ascii')
            for kernel,version,patched in [('5.4.0','6.3.4',False),('5.15.162','6.3.4',True),('6.6.0','6.3.5',False),('6.6.0','6.2.7',False),('unknown','6.3.4',False)]:
                with self.subTest(kernel=kernel,version=version):
                    native.write_text('#!/bin/sh\nprintf "hcxdumptool '+version+'\\nusage\\n-w output\\n"\n',encoding='ascii')
                    rewritten=functions.replace('/root/.luxiaoguan-tools/hcx-ifindex1',posix(compat)).replace('/root/.winbox-tools/bin/hcxdumptool',posix(base/'missing'))
                    setup='export PATH='+shlex.quote(posix(base))+':"$PATH"\nchmod 700 '+shlex.quote(posix(native))+' '+shlex.quote(posix(compat))+'\nuname() { printf "%s" '+shlex.quote(kernel)+'; }\nlog() { :; }\n'
                    result=subprocess.run([SHELL],input=setup+rewritten+'\ndetect_hcx_dialect\nprintf "%s" "$HCX_BIN"\n',text=True,encoding='utf-8',capture_output=True,timeout=8)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(result.stdout,posix(compat if patched else native))


class CompatibilityArtifactTests(unittest.TestCase):
    def test_unknown_architecture_is_rejected(self):
        with self.assertRaises(RuntimeError):local_variant(SimpleNamespace(package_architecture='unknown'))


if __name__=='__main__':unittest.main()
