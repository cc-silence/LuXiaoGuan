"""Execute Windows lease scripts against fake CIM only, never real adapters."""
import base64
import json
import os
import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
from pathlib import Path
import re
from modules.platform_services import lan_network_windows as windows
from modules.platform_services.network_diagnostics import observing
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.display_privacy import masker_for
from tests.test_lan_client_recovery import adapter


@unittest.skipUnless(os.name=='nt','Requires Windows PowerShell')
class LeaseRecoveryTests(unittest.TestCase):
    def execute(self,release=83,renew=0,changed=False,static=False,wireless=False):
        original=subprocess.run
        fake=r"""
$script:reads=0
function Get-CimInstance {
 $script:reads++
 [pscustomobject]@{DHCPEnabled=($script:reads -eq 1 -or !__STATIC__);
 InterfaceIndex=14;MACAddress=$(if($script:reads -gt 1 -and __CHANGED__){'00:00:00:00:00:01'}else{'00:50:56:2B:29:2D'});
 IPEnabled=($script:reads -eq 1 -or $script:reads -gt 2);IPAddress=$(if($script:reads -eq 1){@('192.168.11.123')}elseif($script:reads -gt 2){@('192.168.22.23')}else{@()})}
}
function Invoke-CimMethod {
 param($InputObject,$MethodName)
 if($MethodName -eq 'ReleaseDHCPLease'){[pscustomobject]@{ReturnValue=__RELEASE__}}
 elseif($MethodName -eq 'RenewDHCPLease'){[pscustomobject]@{ReturnValue=__RENEW__}}
 else {throw 'Unexpected method'}
}
""".replace('__RELEASE__',str(release)).replace('__RENEW__',str(renew)).replace('__CHANGED__','$true' if changed else '$false').replace('__STATIC__','$true' if static else '$false')
        def run(argv,**kwargs):
            source=base64.b64decode(argv[-1]).decode('utf-16-le')
            exe,args=windows.powershell(fake+source)
            return original([exe,*args],**kwargs)
        journal=OperationDiagnostics()
        def sink(row):journal.record('configuration',operation='client_network_renew',status='progress',**row)
        error=None
        with patch.object(windows.subprocess,'run',side_effect=run),observing(sink):
            try:windows.renew(adapter(wireless=wireless),20,lambda:True)
            except RuntimeError as exc:error=exc
        return error,sanitized_operation_evidence(journal.snapshot(),masker_for([]))['configuration']

    def test_release_83_still_renews_and_exports_real_phase_state(self):
        error,rows=self.execute()
        self.assertIsNone(error)
        released=next(r for r in rows if r['stage']=='lease_release')
        self.assertEqual(released['native_error'],83)
        after=next(r for r in rows if r['stage']=='lease_after_release')
        self.assertFalse(after['ip_enabled']);self.assertEqual(after['address_count'],0)
        renewed=next(r for r in rows if r['stage']=='lease_after_renew')
        self.assertTrue(renewed['ip_enabled']);self.assertEqual(renewed['address_count'],1)
        for secret in ('192.168.11.123','192.168.22.23','00:50:56',adapter()['id']):
            self.assertNotIn(secret,json.dumps(rows))

    def test_first_84_is_preserved_with_post_release_evidence(self):
        error,rows=self.execute(release=0,renew=84)
        self.assertIsInstance(error,RuntimeError)
        self.assertTrue(any(r['stage']=='lease_renew' and r.get('native_error')==84 for r in rows))
        self.assertTrue(any(r['stage']=='lease_after_release' and r.get('ip_enabled') is False for r in rows))

    def test_identity_or_dhcp_change_stops_before_renewal(self):
        for changed,static in ((True,False),(False,True)):
            with self.subTest(changed=changed,static=static):
                error,rows=self.execute(changed=changed,static=static)
                self.assertIsInstance(error,RuntimeError)
                self.assertFalse(any(r['stage']=='lease_renew' for r in rows))

    def test_unsupported_release_does_not_attempt_renewal(self):
        error,rows=self.execute(release=64)
        self.assertIsInstance(error,RuntimeError)
        self.assertFalse(any(r['stage']=='lease_renew' for r in rows))


    def test_wifi_direct_renewal_does_not_release_or_disable_ip(self):
        error,rows=self.execute(release=64,renew=0,wireless=True)
        self.assertIsNone(error)
        self.assertFalse(any(row['stage'] in {'lease_release','lease_after_release'} for row in rows))
        self.assertTrue(any(row['stage']=='lease_renew' and row.get('native_error')==0 for row in rows))

    def test_wifi_pending_84_waits_for_delayed_address_without_another_request(self):
        error,_=self.execute(release=0,renew=84,wireless=True)
        self.assertIsInstance(error,RuntimeError)
        self.assertTrue(error.network_retryable)
        from tests.test_network_readiness_diagnostics import NetworkReadinessTests
        old=adapter(connected=False,addresses=[],wireless=True)
        ready=adapter(addresses=['192.168.22.23/24'],wireless=True)
        samples=[dict(adapters=[old]) for _ in range(26)]+[dict(adapters=[ready])]
        result,records,execute,_,elapsed=NetworkReadinessTests().run_case(samples,error,timeout=60)
        self.assertEqual(result,ready)
        self.assertEqual(elapsed,13)
        execute.assert_called_once()
        self.assertEqual(records[-1]['reason'],'address_ready')

    def test_pending_84_uses_whole_budget_when_wifi_does_not_return(self):
        error,_=self.execute(renew=84,wireless=True)
        from tests.test_network_readiness_diagnostics import NetworkReadinessTests
        result,records,execute,_,elapsed=NetworkReadinessTests().run_case(
            lambda **kwargs:dict(adapters=[adapter(connected=False,addresses=[],wireless=True)]),error,timeout=60)
        self.assertIs(result,error)
        self.assertEqual(elapsed,60)
        self.assertEqual(records[-1]['stage'],'address_wait')
        execute.assert_called_once()



class FailureSnapshotTests(unittest.TestCase):
    def test_terminal_84_exports_fresh_state_without_hiding_original_failure(self):
        from modules.platform_services import lan_network as facade
        failure=RuntimeError('private');failure.network_retryable=False
        rows=[]
        current=adapter(addresses=[],gateways=[],connected=False)
        with patch.object(facade,'current_os',return_value=facade.WINDOWS), \
             patch.object(windows,'renew',side_effect=failure), \
             patch.object(facade,'inspect',return_value=dict(adapters=[current],error='')) as read, \
             observing(rows.append):
            with self.assertRaises(RuntimeError) as raised:facade.renew(adapter(),20)
        self.assertIs(raised.exception,failure)
        read.assert_called_once()
        self.assertEqual(read.call_args.kwargs['adapter_id'],adapter()['id'])
        self.assertLessEqual(read.call_args.kwargs['timeout'],2)
        after=next(row for row in rows if row['stage']=='adapter_after')
        self.assertFalse(after['snapshot_cached']);self.assertFalse(after['adapter_connected'])
        self.assertEqual(after['address_count'],0)

    def test_elevation_preserves_protocol_and_cleans_temporary_file(self):
        paths=[];rows=[]
        def elevate(exe,args,**kwargs):
            source=base64.b64decode(args.split()[-1]).decode('utf-16-le')
            path=Path(re.search(r"AppendAllText\('([^']+)'",source)[1])
            paths.append(path)
            path.write_bytes(b'LEASE:{"stage":"lease_release","native_error":83}\n'
                             b'LEASE:{"stage":"lease_renew","native_error":0}\n')
            return True,0
        with patch.object(windows.subprocess,'run',return_value=NS(returncode=5)), \
             patch('modules.platform_services.update_elevation.run_elevated_update',side_effect=elevate),observing(rows.append):
            windows.renew(adapter(),20,lambda:True)
        self.assertTrue(any(row['stage']=='lease_release' and row.get('native_error')==83 for row in rows))
        self.assertTrue(any(row['stage']=='lease_renew' and row.get('native_error')==0 for row in rows))
        self.assertFalse(paths[0].parent.exists())


if __name__=='__main__':unittest.main()
