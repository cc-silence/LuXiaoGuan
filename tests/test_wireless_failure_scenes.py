"""Failure scenes are automatic, bounded, private and retained across later success."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
import zipfile
from tests.test_ap_kernel_readiness import KernelApDevice
from tests.test_wireless_setup import LuaRuntime
from modules.capture_services.openwrt.wireless_failure_commands import failure_scene_command
from modules.capture_services.openwrt.wireless_failure_evidence import collect_wireless_failure,capture_on_failure
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics,sanitized_operation_evidence
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
from modules.capture_services.openwrt.diagnostic_context import router_context_json
from modules.display_privacy import masker_for
from modules.log_services import export_logs


@unittest.skipUnless(LuaRuntime,"Lua unavailable")
class NativeFailureSceneTests(unittest.TestCase):
    def scene(self,device,log):
        original=device.read
        device.lua.globals().bridge_read=lambda command:log if command.startswith('logread ') else original(command)
        command=failure_scene_command('radio7','home')
        body=command.split("<<'ROUTERMANAGER_FAILURE_SCENE'\n",1)[1].rsplit('\nROUTERMANAGER_FAILURE_SCENE',1)[0]
        device._output=[];device.lua.execute(body)
        return json.loads(''.join(device._output))

    def test_carrier_zero_does_not_prevent_independent_kernel_scene(self):
        device=KernelApDevice();device.kernel_carrier='0'
        data=self.scene(device,'hostapd: Could not configure driver mode Home\nnetifd: Failed to start AP\n')
        proof=data['interfaces'][0]
        self.assertFalse(proof['ap_carrier'])
        self.assertTrue(proof['kernel_info_available'])
        self.assertTrue(proof['kernel_name_present'])
        self.assertTrue(proof['ssid_matches'])
        self.assertEqual(proof['frequency'],5180)
        self.assertIn('Could not configure driver mode',data['service_log'][0])
        raw=json.dumps(data)
        self.assertNotIn('Home',raw);self.assertNotIn('original-password',raw)
        self.assertNotIn('ssid',data['configs'][0]);self.assertNotIn('key',data['configs'][0])

    def test_transaction_scene_is_captured_before_rollback_and_keeps_original_error(self):
        from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        device=KernelApDevice();device.kernel_carrier='0'
        device.config['wireless']['radio7']['disabled']='1'
        device.config['wireless']['home']['disabled']='1'
        device.operation_diagnostics=OperationDiagnostics()
        original_read=device.read
        device.lua.globals().bridge_read=lambda command:'hostapd: Failed to start AP\n' if command.startswith('logread ') else original_read(command)
        original=device.run
        observations=[]
        def run(name,command,timeout,cancel_event=None,**kwargs):
            if name=='wireless_failure_scene':
                observations.append(device.config['wireless']['home']['disabled'])
                body=command.split("<<'ROUTERMANAGER_FAILURE_SCENE'\n",1)[1].rsplit('\nROUTERMANAGER_FAILURE_SCENE',1)[0]
                device._output=[];device.lua.execute(body)
                return RemoteCommandResult(name,0,''.join(device._output))
            return original(name,command,timeout,cancel_event)
        device.run=run;manager=WirelessSetupManager(device)
        error=SignalError('authentication','original readiness failure')
        with patch.object(manager,'_wait_ready',side_effect=error):
            with self.assertRaises(SignalError) as caught:manager.toggle('home',True,threading.Event(),enable_parent=True)
        self.assertIs(caught.exception,error)
        self.assertEqual(observations,['0'])
        self.assertEqual(device.config['wireless']['home']['disabled'],'1')
        self.assertEqual(device.config['wireless']['radio7']['disabled'],'1')
        scene=device.operation_diagnostics.snapshot()['wireless_failures'][0]
        self.assertEqual(scene['collection_status'],'completed')
        self.assertFalse(scene['configs'][0]['disabled'])
        self.assertIn(('uci','rollback'),device.calls)

    def test_only_latest_relevant_lines_are_retained_and_sensitive_lines_removed(self):
        device=KernelApDevice()
        log='\n'.join('hostapd: event '+str(i) for i in range(180))+'\nhostapd: password=leaked\nother-process: private\n'
        data=self.scene(device,log)
        self.assertEqual(len(data['service_log']),32)
        self.assertIn('event 179',data['service_log'][-2])
        self.assertNotIn('leaked',json.dumps(data));self.assertNotIn('other-process',json.dumps(data))


class FailureRetentionTests(unittest.TestCase):
    def runner(self,result=None):
        journal=OperationDiagnostics();journal.bind_connection('device-a')
        runner=NS(operation_diagnostics=journal,ssh_client=NS(connected=True),run=Mock())
        runner.run.return_value=result or NS(cancelled=False,timed_out=False,exit_code=0,
            stdout=json.dumps(dict(schema=1,collection_status='completed',service_log=['hostapd: Failed to set beacon parameters'])) )
        return runner,journal

    def test_actual_zip_keeps_router_service_error_after_successful_refresh(self):
        runner,journal=self.runner()
        collect_wireless_failure(runner,'radio1','home','direct_toggle',threading.Event())
        for _ in range(100):journal.record('workflow',operation='wireless_status',status='completed')
        context=router_context_json(OpenWrtSessionSnapshot.initial(),masker_for([]),operation_evidence=journal.snapshot())
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'feedback.zip';export_logs(target,'signal',[],router_context=context)
            with zipfile.ZipFile(target) as z:
                data=json.loads(z.read('operation-diagnostics.json'))
                self.assertIn('Failed to set beacon',data['wireless_failures'][0]['service_log'][0])
        self.assertEqual(runner.run.call_args.kwargs['timeout'],3)
        self.assertEqual(runner.run.call_args.kwargs['output_limit'],49152)

    def test_collection_failure_does_not_replace_original_operation_exception(self):
        runner,journal=self.runner();runner.run.side_effect=RuntimeError('secondary failure password=secret')
        original=SignalError('unconfirmed','original failure')
        with self.assertRaises(SignalError) as caught:
            with capture_on_failure(runner,'radio1','home','repair',threading.Event()):raise original
        self.assertIs(caught.exception,original)
        self.assertEqual(journal.snapshot()['wireless_failures'][0]['collection_status'],'failed')
        self.assertNotIn('secret',json.dumps(journal.snapshot()))

    def test_cancel_does_not_run_an_additional_command(self):
        runner,journal=self.runner();cancel=threading.Event();cancel.set()
        collect_wireless_failure(runner,'radio1','home','repair',cancel)
        runner.run.assert_not_called()
        self.assertEqual(journal.snapshot()['wireless_failures'][0]['collection_status'],'cancelled')

    def test_timeout_is_explicit_and_oversized_or_unapproved_fields_are_dropped(self):
        runner,journal=self.runner(NS(cancelled=False,timed_out=True,exit_code=-1,stdout='secret'))
        collect_wireless_failure(runner,'radio1','home','repair',threading.Event())
        self.assertEqual(journal.snapshot()['wireless_failures'][0]['collection_status'],'timeout')
        from modules.capture_services.openwrt.wireless_failure_evidence import sanitize_scene
        data=sanitize_scene(dict(collection_status='completed',configs=[dict(ssid='secret',key='secret')]*100,
                                 interfaces=[dict(index=1,password='secret')]*100,service_log=['password=secret']*100,
                                 radio_config=dict(channel='secret',country='secret')),masker_for([]))
        self.assertEqual((len(data['configs']),len(data['interfaces']),len(data['service_log'])),(32,32,32))
        self.assertNotIn('secret',json.dumps(data))

    def test_failure_ring_and_connection_epoch_are_enforced(self):
        runner,journal=self.runner()
        for _ in range(12):collect_wireless_failure(runner,'radio1','home','repair',threading.Event())
        self.assertEqual(len(journal.snapshot()['wireless_failures']),8)
        epoch=journal.epoch;journal.bind_connection('device-b')
        journal.wireless_failure(dict(collection_status='completed'),epoch)
        self.assertEqual(journal.snapshot()['wireless_failures'],[])

    def test_command_failures_survive_background_polling_noise(self):
        runner,journal=self.runner()
        journal.record('command',operation='radio_identity_probe',status='failed',stage='exec_request',
                       reason='channel_closed',request_bytes=123,transport='exec')
        for _ in range(100):journal.record('workflow',operation='wireless_status',status='cancelled')
        data=sanitized_operation_evidence(journal.snapshot(),masker_for([]))
        row=data['command_failures'][0]
        self.assertEqual((row['reason'],row['request_bytes'],row['transport']),('channel_closed',123,'exec'))

    def test_capture_failure_survives_next_success_and_is_scoped(self):
        from PySide6.QtCore import QObject,Signal
        from modules.capture_services.local_capture_diagnostics import AutoCaptureDiagnostics
        runner,journal=self.runner()
        class Service(QObject):
            messageGenerated=Signal(str,str)
            radioStreamOutput=Signal(str,str)
            radioStateChanged=Signal(str,str)
        class Owner(QObject):
            openwrtSnapshotChanged=Signal(object)
        owner=Owner();owner.openwrt_snapshot=NS(host_fingerprint='device-a')
        owner.openwrt_backend=NS(command_runner=runner)
        service=Service();service.owner=owner
        diagnostics=AutoCaptureDiagnostics(service)
        diagnostics.begin('radio0');diagnostics.stream('radio0','first failure Channel closed.\n')
        diagnostics.state('radio0','error')
        diagnostics.begin('radio0');diagnostics.stream('radio0','second capture succeeds\n');diagnostics.state('radio0','idle')
        rows=journal.snapshot()['capture_failures']
        self.assertEqual(len(rows),1);self.assertIn('Channel closed',rows[0]['first_log'])
        diagnostics.begin('radio0');journal.bind_connection('device-b');diagnostics.state('radio0','error')
        self.assertEqual(journal.snapshot()['capture_failures'],[])
        service.deleteLater();owner.deleteLater()
