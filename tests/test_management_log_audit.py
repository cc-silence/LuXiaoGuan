"""Regression checks for task evidence coverage, retention and actual ZIP export."""
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from modules.capture_services.configuration_progress import observing, reporting, phase
from modules.capture_services.management_diagnostics import ManagementTrace
from modules.capture_services.management_diagnostic_schema import ACTIONS
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.diagnostic_context import router_context_json
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
from modules.capture_services.wireless_setup_service import WirelessSetupService
from modules.display_privacy import masker_for
from modules.log_services import export_logs
from tests import test_wireless_service_edges as edges


class ManagementLogAuditTests(unittest.TestCase):
    def setUp(self):
        self.journal=OperationDiagnostics()
        self.journal.bind_connection('device-a')
        self.runner=NS(operation_diagnostics=self.journal)

    def fail(self,action='relocate'):
        trace=ManagementTrace(self.runner,action,{})
        def operation(event,report):
            try:raise ConnectionRefusedError(10061,'password=private-error')
            except OSError as exc:raise SignalError('recovery','private-summary') from exc
        with self.assertRaises(SignalError):trace.execute(operation,threading.Event(),Mock())
        return trace

    def test_original_transport_error_and_terminal_recovery_both_survive_export(self):
        self.fail()
        data=sanitized_operation_evidence(self.journal.snapshot(),masker_for(['recovery']))
        row=data['action_failures'][0]
        self.assertEqual(row['reason'],'recovery')
        self.assertEqual(row['cause_reason'],'transport')
        self.assertEqual(row['cause_type'],'ConnectionRefusedError')
        self.assertEqual(row['errno'],10061)
        self.assertEqual(row['status'],'uncertain')
        self.assertNotIn('private',json.dumps(data))

    def test_failure_per_action_survives_noise_and_actual_zip_export(self):
        self.fail()
        for _ in range(600):
            ManagementTrace(self.runner,'common_read',{}).failed(ValueError('private'))
            self.journal.record('command',operation='scan',status='completed')
        context=router_context_json(OpenWrtSessionSnapshot.initial(),masker_for([]),operation_evidence=self.journal.snapshot())
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'feedback.zip'
            export_logs(output,'capture',[],router_context=context)
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                data=json.loads(archive.read('operation-diagnostics.json'))
        self.assertNotIn('relocate',{r.get('operation') for r in data['management']})
        rows={r['operation']:r for r in data['action_failures']}
        self.assertEqual(rows['relocate']['cause_reason'],'transport')
        self.assertEqual(len(rows),2)
        self.assertNotIn('private',json.dumps(data))

    def test_phase_observer_records_without_duplicate_ui_delivery_and_resets(self):
        ui=Mock();outer=Mock()
        trace=ManagementTrace(self.runner,'relocate',{})
        def operation(event,report):
            phase('static-private-stage',seconds=90)
            return {}
        with reporting(ui),observing(outer):
            trace.execute(operation,threading.Event(),Mock())
            phase('after')
        self.assertEqual(ui.call_count,2)
        self.assertEqual(outer.call_count,1)
        stages=[r for r in self.journal.snapshot()['management'] if r['status']=='progress']
        self.assertEqual(len(stages),1)
        self.assertNotIn('static-private',json.dumps(stages))

    def test_retention_is_bounded_and_device_switch_clears_per_action_failures(self):
        for action in ACTIONS:
            for _ in range(4):ManagementTrace(self.runner,action,{}).failed(ValueError('private'))
        self.assertEqual(len(self.journal.snapshot()['action_failures']),len(ACTIONS))
        with self.assertRaises(ValueError):
            sanitized_operation_evidence(dict(schema_version=1,action_failures=[{}]*(len(ACTIONS)+1)),masker_for([]))
        self.journal.bind_connection('device-a')
        self.assertTrue(self.journal.snapshot()['action_failures'])
        self.journal.bind_connection('device-b')
        self.assertFalse(self.journal.snapshot()['action_failures'])

    def test_wireless_save_and_scan_are_traced_without_breaking_scan_callback(self):
        for action in ('save','save_only','delete','scan','password','pending_commit'):
            state=edges.WirelessServiceEdgeTests().context()
            state.owner.openwrt_backend=NS(command_runner=self.runner)
            operation=Mock(return_value={})
            self.assertTrue(WirelessSetupService._start(state,action,operation))
            run=state.owner._start_openwrt_worker.call_args.args[1]
            result=run(threading.Event(),Mock())
            self.assertFalse(result.error)
            self.assertEqual(len(operation.call_args.args),3 if action=='scan' else 2)
            if action=='scan':self.assertTrue(callable(operation.call_args.args[2]))
            final=self.journal.snapshot()['management'][-1]
            self.assertEqual(final['operation'],'wireless_'+action)
            self.assertEqual(final['status'],'completed')

    def test_file_tool_and_maintenance_actions_have_task_journals(self):
        for action in ('delete','file_batch','download_directory','tool_install','maintenance_apply','password_change'):
            trace=ManagementTrace(self.runner,action,{'kind':'reset','path':'private-path','password':'private-password'})
            trace.execute(lambda event,report:{'verified':True},threading.Event(),Mock())
            final=self.journal.snapshot()['management'][-1]
            self.assertEqual(final['operation'],action)
            self.assertEqual(final['task_id'],trace.task_id)
        self.assertNotIn('private',json.dumps(self.journal.snapshot()))

    def test_actual_batch_partial_failure_and_cancellation_are_not_completed(self):
        from modules.capture_services.router_file_batch import run_file_batch
        for error,status in ((PermissionError(13,'private-path'),'failed'),
                             (InterruptedError('private-path'),'cancelled')):
            manager=NS(mutate=Mock(side_effect=[{},error]))
            jobs=[dict(operation='delete',arguments={},name='private-name',size=1)]*2
            trace=ManagementTrace(self.runner,'file_batch',dict(jobs=jobs))
            result=trace.execute(lambda event,report:run_file_batch(manager,jobs,event,report,lambda:True),
                                 threading.Event(),Mock())
            self.assertEqual(len(result['results']),1)
            row=self.journal.snapshot()['action_failures'][-1]
            self.assertEqual(row['status'],status)
            self.assertEqual(row['completed_count'],1)
            self.assertEqual(row['selected_count'],2)
            self.assertEqual(row['cause_type'],type(error).__name__)
            self.assertNotIn('private',json.dumps(self.journal.snapshot()))
