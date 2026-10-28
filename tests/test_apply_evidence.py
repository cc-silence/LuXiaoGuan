"""Application evidence must reach the real feedback ZIP without logging secrets."""
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from tests.test_checked_apply import CheckedApplyTests,run_checked,fast_deadlines,LuaRuntime
from modules.capture_services.openwrt.apply_evidence import ApplyEvidence
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics,sanitized_operation_evidence
from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner,RemoteCommandResult
from modules.capture_services.openwrt.configuration_runtime import network_ready
from modules.capture_services.openwrt.diagnostic_context import router_context_json
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.display_privacy import masker_for
from modules.log_services import export_logs


class ApplyEvidenceTests(unittest.TestCase):
    def fixture(self):
        if not LuaRuntime:self.fail('Lua runtime required')
        device,channel,event,stage,verify=CheckedApplyTests().fixture()
        journal=OperationDiagnostics();journal.bind_connection('device-a')
        evidence=ApplyEvidence(journal)
        return device,channel,event,stage,verify,journal,evidence

    def test_connection_failure_keeps_original_cause_after_successful_restore(self):
        device,channel,event,stage,verify,journal,evidence=self.fixture()
        def connect(host,timeout):
            if host=='new':raise ConnectionRefusedError('password=do-not-store 192.168.123.234')
        channel.connect=connect
        with fast_deadlines(),self.assertRaises(RuntimeError):
            run_checked(channel,stage,verify,event,candidates=('new',),original_host='old',evidence=evidence)
        final=journal.snapshot()['configuration_failures'][-1]
        self.assertEqual(final['last_stage'],'connect');self.assertEqual(final['exception_type'],'ConnectionRefusedError')
        self.assertTrue(final['restored']);self.assertGreater(final['connect_attempts'],1)
        self.assertNotIn('do-not-store',json.dumps(journal.snapshot()));self.assertNotIn('192.168.123.234',json.dumps(journal.snapshot()))

    def test_native_apply_error_code_is_retained_without_resubmit(self):
        device,channel,event,stage,verify,journal,evidence=self.fixture();original=channel.call
        def call(action,**options):
            if action=='checked_apply':raise SignalError('rpc_uci_apply_6','private remote error')
            return original(action,**options)
        channel.call=call
        with self.assertRaises(SignalError):
            run_checked(channel,stage,verify,event,candidates=('new',),original_host='old',evidence=evidence)
        final=journal.snapshot()['configuration_failures'][-1]
        self.assertEqual(final['last_stage'],'apply');self.assertEqual(final['native_error'],6)
        self.assertNotIn('private remote error',json.dumps(final));self.assertNotIn('checked_rollback',channel.calls)

    def test_runtime_mismatch_survives_generic_false_and_recovery(self):
        device,channel,event,stage,verify,journal,evidence=self.fixture()
        def mismatch(remote,sid):
            evidence.mismatch('runtime_ipv4_mask');return False
        with fast_deadlines(),self.assertRaises(RuntimeError):
            run_checked(channel,stage,mismatch,event,candidates=('new',),original_host='old',evidence=evidence)
        final=journal.snapshot()['configuration_failures'][-1]
        self.assertEqual(final['last_reason'],'runtime_ipv4_mask');self.assertEqual(final['last_stage'],'verify')
        self.assertTrue(final['restored']);self.assertGreater(final['verify_attempts'],1)

    def test_independent_runner_commands_are_correlated_to_original_task(self):
        device,channel,event,stage,verify,journal,evidence=self.fixture()
        channel.runner=OpenWrtCommandRunner(NS(connected=True))
        channel.runner._run=Mock(return_value=RemoteCommandResult('test',0,'secret-output'))
        original=channel.call
        def call(action,**values):
            channel.runner.run('wireless_setup_'+action,'secret-command',1)
            return original(action,**values)
        channel.call=call
        with journal.management_scope(321,journal.epoch):
            result=run_checked(channel,stage,verify,event,candidates=('new',),original_host='old',evidence=evidence)
        self.assertTrue(result['applied']);self.assertIs(channel.runner.operation_diagnostics,journal)
        commands=[r for r in journal.snapshot()['management'] if r['event']=='command']
        self.assertTrue(commands);self.assertTrue(all(r['task_id']==321 for r in commands))
        self.assertTrue(all(r['task_id']==321 for r in journal.snapshot()['configuration']))
        self.assertNotIn('secret-',json.dumps(journal.snapshot()))

    def test_failure_summary_survives_unrelated_tasks_and_actual_zip_export(self):
        device,channel,event,stage,verify,journal,evidence=self.fixture()
        with fast_deadlines(),self.assertRaises(RuntimeError):
            run_checked(channel,stage,lambda *_:False,event,candidates=('new',),original_host='old',evidence=evidence)
        for _ in range(600):
            journal.record('management',operation='common_read',status='failed')
            journal.record('command',operation='scan',status='completed')
        context=router_context_json(OpenWrtSessionSnapshot.initial(),masker_for([]),operation_evidence=journal.snapshot())
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'feedback.zip';export_logs(output,'capture',[],router_context=context)
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                data=json.loads(archive.read('operation-diagnostics.json'))
        final=data['configuration_failures'][-1]
        self.assertEqual(final['last_reason'],'verification_false');self.assertTrue(final['restored'])
        self.assertLessEqual(len(data['configuration']),128)
        self.assertIn('trace_id',final)

    def test_retention_is_bounded_and_switched_device_rejects_late_rows(self):
        journal=OperationDiagnostics();journal.bind_connection('device-a')
        for _ in range(40):
            trace=ApplyEvidence(journal);trace.emit('verify','retry','runtime_ipv4');trace.finish('failed')
        data=journal.snapshot();self.assertEqual(len(data['configuration_failures']),16)
        for key,limit in (('configuration',128),('configuration_failures',16)):
            with self.assertRaises(ValueError):
                sanitized_operation_evidence(dict(schema_version=1,**{key:[{}]*(limit+1)}),masker_for([]))
        stale=ApplyEvidence(journal);journal.bind_connection('device-b')
        stale.emit('apply','retry','reply_lost');stale.finish('failed')
        self.assertFalse(journal.snapshot()['configuration']);self.assertFalse(journal.snapshot()['configuration_failures'])

    def test_runtime_checks_report_the_specific_mismatch(self):
        values=dict(proto='static',ipaddr='192.168.22.1',netmask='255.255.255.0',device='br-lan')
        good=dict(up=True,pending=False,proto='static',device='br-lan',**{'ipv4-address':[dict(address='192.168.22.1',mask=24)]})
        cases=[(None,'runtime_missing'),(dict(good,pending=True),'runtime_pending'),(dict(good,up=False),'runtime_up'),
               (dict(good,device='wrong'),'runtime_device'),(dict(good,**{'ipv4-address':[]}), 'runtime_ipv4'),
               (dict(good,**{'ipv4-address':[dict(address='192.168.22.1',mask=16)]}),'runtime_ipv4_mask')]
        for state,reason in cases:
            journal=OperationDiagnostics();evidence=ApplyEvidence(journal)
            self.assertFalse(network_ready(state,values,evidence=evidence))
            self.assertEqual(journal.snapshot()['configuration'][-1]['reason'],reason)
        self.assertTrue(network_ready(good,values))
