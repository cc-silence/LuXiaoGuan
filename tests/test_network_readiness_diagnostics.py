"""Delayed DHCP readiness and private diagnostic export; no real adapters."""
from tests.platform_fixtures import mock_windows_elevation
import errno
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from modules.platform_services import lan_network as facade
from modules.platform_services import lan_network_windows as windows
from modules.platform_services.network_diagnostics import observing, failure_fields
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.apply_evidence import ApplyEvidence
from modules.display_privacy import masker_for
from tests.test_lan_client_recovery import adapter

class NetworkReadinessTests(unittest.TestCase):
    def run_case(self, rows, error=None, timeout=5, permit=lambda:True):
        clock=[100.0];records=[]
        def wait(seconds):clock[0]+=seconds
        old=adapter(expected_addresses=['192.168.22.1/24'])
        with patch.object(facade,'current_os',return_value=facade.WINDOWS), \
             patch.object(facade.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(facade,'Event',return_value=NS(wait=wait)), \
             patch.object(windows,'renew',side_effect=error) as execute, \
             patch.object(facade,'inspect',side_effect=rows) as inspect, observing(records.append):
            try:result=facade.renew(old,timeout,permit)
            except Exception as exc:result=exc
        return result,records,execute,inspect,clock[0]-100

    def test_system_success_waits_for_late_target_address(self):
        ready=adapter(addresses=['192.168.22.23/24'],gateways=['192.168.22.1'])
        rows=[dict(adapters=[adapter()]),dict(adapters=[adapter()]),dict(adapters=[ready])]
        result,records,execute,inspect,elapsed=self.run_case(rows)
        self.assertEqual(result,ready);execute.assert_called_once();self.assertEqual(inspect.call_count,3)
        self.assertTrue(records[0]['snapshot_cached'])
        self.assertEqual(elapsed,1);self.assertEqual(records[-1]['reason'],'address_ready')
        self.assertFalse(records[1]['target_subnet_match']);self.assertTrue(records[-1]['target_subnet_match'])

    def test_transient_renew_failure_still_observes_late_address_without_resubmitting(self):
        error=RuntimeError('private error');error.network_retryable=True;error.network_reason='lease_failed'
        ready=adapter(addresses=['192.168.22.23/24'])
        result,_,execute,_,_=self.run_case([dict(adapters=[]),dict(adapters=[ready])],error)
        self.assertEqual(result,ready);execute.assert_called_once()

    def test_wrong_subnet_expires_and_does_not_retry_mutation(self):
        result,records,execute,_,elapsed=self.run_case(lambda **kw:dict(adapters=[adapter()]))
        self.assertIsInstance(result,TimeoutError);self.assertEqual(elapsed,5)
        execute.assert_called_once();self.assertEqual(records[-1]['snapshot_attempts'],10)
        self.assertLessEqual(len(records),4)

    def test_permission_denied_and_cancellation_do_not_wait_or_report_success(self):
        result,_,execute,inspect,_=self.run_case([],PermissionError('secret'))
        self.assertIsInstance(result,PermissionError);inspect.assert_called_once()
        result,_,_,inspect,_=self.run_case([],permit=lambda:False)
        self.assertIsInstance(result,InterruptedError);inspect.assert_not_called()

    def test_diagnostics_sink_failure_does_not_break_recovery(self):
        ready=adapter(addresses=['192.168.22.23/24'])
        with observing(Mock(side_effect=OSError('disk full'))):
            result=facade._wait_for_address
            with patch.object(facade,'inspect',return_value=dict(adapters=[ready])):
                import time
                self.assertEqual(result(adapter(expected_addresses=['192.168.22.1/24']),5,time.monotonic(),lambda:True),ready)

class TargetedInspectionTests(unittest.TestCase):
    def test_unknown_dhcp_state_waits_for_a_fresh_read(self):
        ready=adapter(addresses=['192.168.22.23/24'])
        result,records,execute,inspect,_=NetworkReadinessTests().run_case([
            dict(adapters=[adapter(dhcp=None)]),dict(adapters=[ready])])
        self.assertEqual(result,ready);execute.assert_called_once()
        self.assertEqual(records[1]['reason'],'inspection_failed')
        self.assertTrue(all(call.kwargs['adapter_id']==adapter()['id'] for call in inspect.call_args_list))
        self.assertTrue(all(call.kwargs['peer']=='192.168.22.1' for call in inspect.call_args_list))

    def test_many_linux_adapters_only_query_selected_networkmanager_device(self):
        from modules.platform_services import lan_network_linux as linux
        inventory=[dict(ifindex=i,ifname='nic'+str(i),address='00:00:00:00:00:01',
                        flags=['UP'],addr_info=[]) for i in range(1,202)]
        calls=[]
        def command(args,timeout):
            calls.append(args)
            if args[:3]==['ip','-j','-4']:return json.dumps(inventory)
            if 'GENERAL.CON-UUID' in args:return 'profile'
            if 'ipv4.method' in args:return 'auto'
            return ''
        with patch.object(linux,'command',side_effect=command),patch.object(linux.shutil,'which',return_value='/usr/bin/nmcli'):
            rows=linux.inspect(timeout=5,adapter_id='200')
        self.assertEqual([r['id'] for r in rows],['200'])
        self.assertEqual(len(calls),6)
        self.assertEqual([args for args in calls if "DHCP4.OPTION" in args],
                         [["nmcli","-g","DHCP4.OPTION","device","show","nic200"]])
        self.assertTrue(all(args[-1]=='nic200' for args in calls[1:] if 'device' in args))

class AdversarialReadinessTests(unittest.TestCase):
    def test_selected_adapter_identity_change_stops_without_waiting(self):
        changed=adapter(mac='11:22:33:44:55:66',addresses=['192.168.22.23/24'])
        result,records,execute,_,elapsed=NetworkReadinessTests().run_case(lambda **kw:dict(adapters=[changed]))
        self.assertIsInstance(result,RuntimeError);self.assertEqual(elapsed,0)
        self.assertEqual(records[-1]['reason'],'adapter_changed');execute.assert_called_once()

    def test_router_address_alias_on_client_is_not_accepted(self):
        changed=adapter(addresses=['192.168.22.23/24','192.168.22.1/24'])
        result,records,execute,_,_=NetworkReadinessTests().run_case(lambda **kw:dict(adapters=[changed]))
        self.assertIsInstance(result,TimeoutError)
        self.assertEqual(records[-1]['reason'],'address_conflict');execute.assert_called_once()

    def test_same_target_subnet_with_host_only_mask_is_not_directly_ready(self):
        changed=adapter(addresses=['192.168.22.23/32'],route=False)
        result,_,execute,_,_=NetworkReadinessTests().run_case(lambda **kw:dict(adapters=[changed]))
        self.assertIsInstance(result,TimeoutError);execute.assert_called_once()

    def test_valid_explicit_route_and_broader_on_link_mask_are_preserved(self):
        for changed in (adapter(addresses=['192.168.22.23/32'],route=True),
                        adapter(addresses=['192.168.22.23/16'],route=False)):
            with self.subTest(addresses=changed['addresses']):
                result,_,execute,_,_=NetworkReadinessTests().run_case([dict(adapters=[changed])])
                self.assertEqual(result,changed);execute.assert_called_once()

    def test_cancel_during_inspection_cannot_publish_ready(self):
        state=[True]
        def inspect(**kwargs):
            state[0]=False
            return dict(adapters=[adapter(addresses=['192.168.22.23/24'])])
        result,_,execute,_,_=NetworkReadinessTests().run_case(inspect,permit=lambda:state[0])
        self.assertIsInstance(result,InterruptedError);execute.assert_called_once()

class NetworkEvidenceTests(unittest.TestCase):
    def test_native_error_and_stage_survive_export_without_private_data(self):
        journal=OperationDiagnostics()
        def sink(fields):journal.record('configuration',operation='client_network_renew',status='progress',**fields)
        with patch.object(windows.subprocess,'run',return_value=NS(returncode=2082)),observing(sink):
            with self.assertRaises(RuntimeError) as raised:windows.renew(adapter(),20,lambda:True)
        self.assertTrue(raised.exception.network_retryable)
        exported=sanitized_operation_evidence(journal.snapshot(),masker_for([]))
        rows=exported['configuration'];native=next(r for r in rows if r.get('native_error')==82)
        self.assertEqual(native['stage'],'lease_renew')
        text=json.dumps(exported)
        for secret in ('192.168.11.123','00:50:56','Ethernet0',adapter()['id']):self.assertNotIn(secret,text)

    def test_error_chain_retains_cause_code_but_no_error_text(self):
        cause=ConnectionRefusedError(errno.ECONNREFUSED,'password=private 192.168.1.1')
        outer=RuntimeError('secret');outer.__cause__=cause
        journal=OperationDiagnostics();evidence=ApplyEvidence(journal);evidence.emit('connect','retry',error=outer)
        row=sanitized_operation_evidence(journal.snapshot(),masker_for([]))['configuration'][0]
        self.assertEqual(row['connection_reason'],'connection_refused');self.assertEqual(row['errno'],errno.ECONNREFUSED)
        self.assertNotIn('private',json.dumps(row));self.assertEqual(failure_fields(outer)['connection_reason'],'connection_refused')

    def test_elevated_error_retains_native_release_code_and_is_not_waitable(self):
        records=[]
        with patch.object(windows.subprocess,'run',return_value=NS(returncode=1091)), \
             mock_windows_elevation(return_value=(True,1064)), observing(records.append):
            with self.assertRaises(RuntimeError) as raised:windows.renew(adapter(),20,lambda:True)
        self.assertFalse(raised.exception.network_retryable)
        self.assertTrue(any(r.get('native_error')==64 and r.get('elevated') for r in records))

class DiagnosticBoundaryTests(unittest.TestCase):
    def test_local_wait_timeout_is_not_misreported_as_ssh_timeout(self):
        from modules.capture_services.management_diagnostics import failure_metadata
        fields=failure_metadata(TimeoutError('Local address did not arrive'))
        self.assertEqual(fields['reason'],'timeout')
        self.assertNotIn('connection_reason',fields)

    def test_authorization_cancel_keeps_its_own_reason_and_return_code(self):
        records=[]
        with patch.object(windows.subprocess,'run',return_value=NS(returncode=5)), \
             mock_windows_elevation(return_value=(False,1223)), observing(records.append):
            with self.assertRaises(RuntimeError) as raised:windows.renew(adapter(),20,lambda:True)
        self.assertEqual(raised.exception.network_reason,'authorization_failed')
        self.assertFalse(raised.exception.network_retryable)
        self.assertTrue(any(r.get('system_exit_code')==1223 and r['reason']=='authorization_failed' for r in records))

class RecoveryPhaseIsolationTests(unittest.TestCase):
    def test_previous_phase_worker_cannot_start_network_operation_during_rollback(self):
        import time
        from tests.test_router_policy_worker import Owner
        from modules.capture_services.lan_recovery_service import LanRecoveryService
        from modules.capture_services.lan_client_recovery import RenewalGate,client_plan
        from tests.test_lan_client_recovery import review,NEW
        from PySide6.QtCore import Signal
        class RecoveryOwner(Owner):configurationProgressChanged=Signal(str,object)
        owner=RecoveryOwner(Mock(),Mock());owner._openwrt_workers['apply']=object()
        broker=LanRecoveryService(owner)
        gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90);gate.arm(automatic=True)
        try:
            with patch('modules.capture_services.task_completion.start_capture_task',return_value=True) as start:
                broker.observe('apply',dict(network_recovery=gate.payload()))
                operation=start.call_args.args[2]
                gate.start(time.monotonic()+25,restoring=True)
                gate.arm()
                from threading import Event
                with patch('modules.platform_services.lan_network.renew') as renew:
                    with self.assertRaises(InterruptedError):operation(Event())
                renew.assert_not_called()
        finally:
            owner._openwrt_workers.clear();broker.release('apply');owner.deleteLater()

    def test_diagnostic_context_is_released_after_worker_scope(self):
        from modules.platform_services.network_diagnostics import emit
        records=[]
        with observing(records.append):emit('lease_process','system_accepted')
        emit('lease_process','system_error')
        self.assertEqual(len(records),1)

class PowerShellNativeCodeTests(unittest.TestCase):
    def test_actual_script_preserves_release_and_renew_native_codes(self):
        import base64
        import os
        import subprocess
        from pathlib import Path
        if os.name!='nt':self.skipTest('Requires the Windows PowerShell parser')
        for release,renew,expected in ((0,0,0),(83,0,0),(0,82,2082),(91,0,1091),(0,64,2064)):
            with self.subTest(release=release,renew=renew):
                # Each native-code case receives its own normal request deadline.
                with patch.object(windows.subprocess,'run',return_value=NS(returncode=0)) as mocked:
                    windows.renew(adapter(),20,lambda:True)
                script=base64.b64decode(mocked.call_args.args[0][-1]).decode('utf-16-le')
                prefix="function Get-CimInstance { [pscustomobject]@{DHCPEnabled=$true;InterfaceIndex=14;MACAddress='00:50:56:2B:29:2D'} };"
                prefix+="function Invoke-CimMethod { param($InputObject,$MethodName) if($MethodName -eq 'ReleaseDHCPLease') { [pscustomobject]@{ReturnValue="+str(release)+"} } else { [pscustomobject]@{ReturnValue="+str(renew)+"} } };"
                executable,args=windows.powershell(prefix+script)
                result=subprocess.run([executable,*args],capture_output=True,timeout=10)
                self.assertEqual(result.returncode,expected,result.stderr.decode('utf-8',errors='replace'))

if __name__=='__main__':unittest.main()
