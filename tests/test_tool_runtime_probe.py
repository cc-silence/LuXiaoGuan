"""Execute the real POSIX probe against usable/missing/broken local fake binaries."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.tool_runtime_probe import tool_probe_command
from modules.capture_services.openwrt.common_tool_dependencies import check_tools
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager

BASH = "C:/Program Files/Git/usr/bin/bash.exe" if os.name == "nt" else shutil.which("bash")


@unittest.skipUnless(BASH and Path(BASH).is_file(),"Local POSIX shell unavailable")
class RuntimeProbeTests(unittest.TestCase):
    def execute(self,key,body=None):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            if body is not None:
                target=root/key;target.write_text("#!/bin/sh\n"+body,encoding="utf-8",newline="\n")
                target.chmod(0o755)
            location=root.as_posix()
            if os.name=="nt":location="/"+location[0].lower()+location[2:]
            script="PATH="+shlex.quote(location)+"\n"+tool_probe_command(key)
            result=subprocess.run([BASH,"--noprofile","--norc","-s"],input=script,text=True,
                                  encoding="utf-8",capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            return result.stdout.strip()

    def test_only_version_or_help_calls_are_used(self):
        for key,body in (("iw",'test "$1" = --version || exit 7; echo "iw version 5.19"'),
                         ("wpa_cli",'test "$1" = -v || exit 7; echo "wpa_cli v2.10"'),
                         ("iwinfo",'test "$1" = -h || exit 7; echo "Usage: iwinfo device info" >&2; exit 1')):
            with self.subTest(key=key):self.assertEqual(self.execute(key,body),"READY")

    def test_hcx_probe_only_reads_help_and_does_not_broaden_capture_support(self):
        for version in ('6.0.1','6.2.4','6.3.4','6.3.5','7.0.0'):
            body='test "$#" = 1 && test "$1" = -h || exit 7; echo "hcxdumptool '+version+' (C) ZerBea"'
            self.assertEqual(self.execute('hcxdumptool',body),'READY')
        self.assertEqual(self.execute('hcxdumptool','echo unrelated; exit 0'),'BROKEN')

    def test_missing_is_distinct_from_loader_failure(self):
        for key in ("iw","wpa_cli","iwinfo","curl","hcxdumptool"):
            with self.subTest(key=key):
                self.assertEqual(self.execute(key),"MISSING")
                self.assertEqual(self.execute(key,'echo "Error loading shared library" >&2; exit 127'),"BROKEN")
                self.assertEqual(self.execute(key,'exit 0'),"BROKEN")

    def test_rpc_helper_probe_executes_read_only_against_actual_posix_stubs(self):
        from modules.capture_services.openwrt.runtime_helper_dependencies import WIRELESS_RPC_PROBE
        with TemporaryDirectory() as folder:
            root=Path(folder)
            for key,body in (('ubus','echo iwinfo'),('rpcd','exit 99')):
                target=root/key;target.write_text('#!/bin/sh\n'+body,encoding='utf-8',newline='\n');target.chmod(0o755)
            location=root.as_posix()
            if os.name=='nt':location='/'+location[0].lower()+location[2:]
            script='PATH='+shlex.quote(location)+'\n'+WIRELESS_RPC_PROBE
            result=subprocess.run([BASH,'--noprofile','--norc','-s'],input=script,text=True,encoding='utf-8',capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('rpcd=1',result.stdout);self.assertIn('iwinfo=1',result.stdout)

    def test_curl_requires_a_usable_https_build_without_contacting_a_website(self):
        good='test "$1" = --version || exit 7; printf "curl 8.0\\nProtocols: http https\\n"'
        self.assertEqual(self.execute('curl',good),'READY')
        self.assertEqual(self.execute('curl','printf "curl 8.0\\nProtocols: http\\n"'),'BROKEN')

    def test_broken_program_is_not_reported_ready_or_missing(self):
        class Runner:
            ssh_client=NS(connected=True)
            def run(inner,name,command,**kwargs):
                status=self.execute("wpa_cli",'exit 127')
                return RemoteCommandResult(name,0,status)
        row=check_tools(Runner(),{"wpa_cli"},Event())[0]
        self.assertEqual(row.state.value,"unknown")
        self.assertFalse(row.missing_packages)
        self.assertTrue(row.detail)


class WirelessRuntimeStateTests(unittest.TestCase):
    def test_existing_but_unusable_cli_blocks_management_without_reinstall_claim(self):
        manager=WirelessSetupManager(NS())
        manager.signal.probe_dependencies=Mock(return_value=dict(lua=True,uci=True,ubus=True,jsonc=True,
                                                               root=True,sha256sum=True,lua_bin="lua"))
        keys=("iw_runtime","wpa_cli_runtime","uci","ubus","iw","wpa_cli","hostapd","wpa_supplicant","rpcd",
              "bus_uci","bus_session","bus_iwinfo","bus_network.wireless","iwinfo_module","rollback",
              "ap_sae","ap_acs","ap_11ac","ap_11ax","ap_11be","ap_11r","ap_eap","ap_owe","sta_owe","sta_sae","relayd")
        text="\n".join(key+"="+("0" if key=="wpa_cli_runtime" else "1") for key in keys)
        manager.signal._execute=Mock(return_value=text)
        manager._call=Mock(side_effect=AssertionError("Unusable environment must not proceed"))
        result=manager.inspect(Event())
        self.assertFalse(result.ready)
        self.assertFalse(result.missing)
        self.assertTrue(any("wpa_cli" in issue for issue in result.issues))
        manager._call.assert_not_called()


if __name__=="__main__":unittest.main()
