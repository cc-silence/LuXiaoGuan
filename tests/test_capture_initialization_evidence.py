"""Initialization evidence survives export; retry state cannot target old artifacts."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile

from modules.capture_services.openwrt.capture_initialization_evidence import (
    TOPOLOGY_COMMAND, collect_scene, parse_topology, parse_engine_list, sanitize_scene)
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence

TOPOLOGY = """__RM_CAPTURE_BEGIN__
K|phy1|hcxstub1|51|0x1003|1
K|phy1|hcxmon1|52|0x1003|0
R|0
P|1
I|hcxstub1
D|51
W|1
T|monitor
I|hcxmon1
D|52
W|1
T|monitor
__RM_CAPTURE_END__
"""
ENGINE = """available wlan devices:
phy idx hw-mac       virtual-mac  m ifname           driver (protocol)
  1  51 001122334455 021122334455 * hcxstub1         driver (NETLINK)
"""


class InitializationEvidenceTests(unittest.TestCase):
    def fixture(self):
        connection=object();journal=OperationDiagnostics()
        ssh=NS(connected=True,_client=connection,host_fingerprint='same')
        runner=NS(operation_diagnostics=journal,ssh_client=ssh,run=Mock(side_effect=[
            RemoteCommandResult('kernel',0,TOPOLOGY),RemoteCommandResult('engine',0,ENGINE)]))
        lane=NS(runner=runner,ssh_client=ssh,radio_name='radio1',phy='phy1',mon_iface='hcxmon1',
                _hcx_bin='/usr/sbin/hcxdumptool',_bound_client=connection,_bound_fingerprint='same',_stop_event=threading.Event())
        return lane,journal

    def test_kernel_and_tool_disagreement_is_recorded_without_raw_identifiers(self):
        lane,journal=self.fixture();collect_scene(lane,'engine_failed',enumerate_engine=True)
        scene=journal.snapshot()['capture_initialization_scenes'][-1]
        self.assertTrue(scene['target_in_kernel']);self.assertTrue(scene['target_in_netlink'])
        self.assertFalse(scene['target_in_engine'])
        self.assertEqual(scene['engine'][0]['name'],'hcxstub1')
        self.assertTrue(scene['kernel'][0]['app_stub_marker'])
        self.assertNotIn('001122334455',json.dumps(scene))
        self.assertNotIn('021122334455',json.dumps(scene))
        command=lane.runner.run.call_args.args[1]
        self.assertIn(' -L ',command)
        self.assertIn('show INTERFACE list and terminate',command)
        for mutation in (' kill ', ' del', 'uci ', 'ip link set', '-i hcxmon'):
            self.assertNotIn(mutation,TOPOLOGY_COMMAND+command)
        for call in lane.runner.run.call_args_list:
            self.assertEqual(call.kwargs['timeout'],3)
            self.assertEqual(call.kwargs['output_limit'],16384)

    def test_feedback_zip_contains_sanitized_scene(self):
        from modules.log_services import export_logs
        from modules.display_privacy import masker_for
        from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
        from modules.capture_services.openwrt.diagnostic_context import router_context_json
        lane,journal=self.fixture();collect_scene(lane,'engine_failed',enumerate_engine=True)
        context=router_context_json(OpenWrtSessionSnapshot.initial(),masker_for([]),operation_evidence=journal.snapshot())
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'feedback.zip';export_logs(path,'capture',[],router_context=context)
            with zipfile.ZipFile(path) as archive:
                data=json.loads(archive.read('operation-diagnostics.json'))
                scene=data['capture_initialization_scenes'][-1]
                self.assertFalse(scene['target_in_engine'])
                self.assertEqual(scene['kernel'][1]['index'],52)

    def test_cancel_change_of_connection_and_epoch_do_not_probe_wrong_device(self):
        lane,journal=self.fixture();lane._stop_event.set()
        collect_scene(lane,'prepared');lane.runner.run.assert_not_called()
        self.assertEqual(journal.snapshot()['capture_initialization_scenes'][-1]['status'],'cancelled')
        lane._stop_event.clear();lane.ssh_client._client=object()
        collect_scene(lane,'prepared');lane.runner.run.assert_not_called()
        self.assertEqual(len(journal.snapshot()['capture_initialization_scenes']),1)
        epoch=journal.epoch;journal.bind_connection('new')
        journal.capture_initialization_scene({'phase':'prepared'},epoch)
        self.assertEqual(journal.snapshot()['capture_initialization_scenes'],[])

    def test_timeouts_bad_responses_and_tool_query_failures_are_secondary(self):
        for result in (RemoteCommandResult('kernel',-1,'',timed_out=True),
                       RemoteCommandResult('kernel',0,'incomplete'),
                       RemoteCommandResult('kernel',71,'')):
            lane,journal=self.fixture();lane.runner.run.side_effect=[result]
            collect_scene(lane,'prepared')
            self.assertIn(journal.snapshot()['capture_initialization_scenes'][-1]['status'],('timeout','invalid_response','failed'))
        lane,journal=self.fixture();lane.runner.run.side_effect=[RemoteCommandResult('kernel',0,TOPOLOGY),OSError('offline')]
        collect_scene(lane,'engine_failed',enumerate_engine=True)
        self.assertTrue(journal.snapshot()['capture_initialization_scenes'][-1]['target_in_kernel'])

    def test_failed_iw_query_does_not_claim_target_is_absent(self):
        lane,journal=self.fixture()
        lane.runner.run.side_effect=[RemoteCommandResult('kernel',0,TOPOLOGY.replace('R|0','R|1'))]
        collect_scene(lane,'prepared')
        scene=journal.snapshot()['capture_initialization_scenes'][-1]
        self.assertEqual(scene['status'],'partial')
        self.assertTrue(scene['target_in_kernel'])
        self.assertNotIn('target_in_netlink',scene)

    def test_app_marker_and_live_registration_are_recorded_separately(self):
        lane,journal=self.fixture()
        owner=NS(_bound_client=lane._bound_client,_bound_fingerprint='same',_created_stubs=['hcxstub1'])
        lane.service=NS(sessions={'radio0':owner})
        collect_scene(lane,'engine_failed',enumerate_engine=True)
        scene=journal.snapshot()['capture_initialization_scenes'][-1]
        self.assertTrue(scene['kernel'][0]['app_stub_marker'])
        self.assertEqual(scene['registered_placeholders'],['hcxstub1'])

    def test_scene_is_bounded_and_export_rejects_oversized_collection(self):
        lane,journal=self.fixture()
        lane.runner.run.side_effect=None;lane.runner.run.return_value=RemoteCommandResult('kernel',0,TOPOLOGY)
        for _ in range(12):collect_scene(lane,'prepared')
        self.assertEqual(len(journal.snapshot()['capture_initialization_scenes']),8)
        from modules.log_history import HistoryMasker
        exported=sanitized_operation_evidence(journal.snapshot(),HistoryMasker())
        self.assertEqual(len(exported['capture_initialization_scenes']),8)
        exported['capture_initialization_scenes']*=2
        with self.assertRaises(ValueError):sanitized_operation_evidence(exported,HistoryMasker())

    def test_strict_parser_and_sanitizer_reject_untrusted_fields(self):
        with self.assertRaises(ValueError):parse_topology(TOPOLOGY.replace('K|phy1|hcxmon1','K|phy1|bad name'))
        clean=sanitize_scene({'phase':[],'password':'secret','engine_errors':{},'kernel':[{'name':'bad name','phy':'phy1','ssid':'secret'}]})
        self.assertNotIn('secret',json.dumps(clean))
        self.assertEqual(parse_engine_list(ENGINE)[0]['index'],51)


class RetryLifecycleTests(unittest.TestCase):
    def fixture(self):
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
        from modules.capture_services.openwrt.models import OpenWrtRadioInfo
        ssh=NS(_client=object(),connected=True,host_fingerprint='same')
        runner=Mock();service=Mock()
        lane=AutoCaptureRadioSession(OpenWrtRadioInfo('radio1',interface='phy1'),runner,ssh,Path(tempfile.gettempdir()),service)
        lane.stats.state='error';lane.remote_cap_file='/root/cap/previous.pcapng'
        lane._monitor_creation_attempted=True
        lane._recovery=Mock();lane._recovery.recovered_on_reconnect.return_value=True
        lane.stats.file_size_bytes=100
        return lane,runner

    def test_early_failure_on_next_attempt_cannot_clean_or_download_previous_attempt(self):
        lane,runner=self.fixture();old_recovery=lane._recovery
        lane._pull_capture_result=Mock()
        runner.run.return_value=RemoteCommandResult('mkdir_cap',1,'','denied')
        with patch('modules.capture_services.openwrt.auto_capture_service.threading.Thread') as thread:
            lane.start('',False)
        thread.return_value.start.assert_called_once()
        self.assertEqual(lane.remote_cap_file,'');self.assertFalse(lane._monitor_creation_attempted)
        self.assertIsNone(lane._recovery);self.assertEqual(lane.stats.file_size_bytes,0)
        lane._run_capture_pipeline('',False)
        lane._pull_capture_result.assert_not_called();old_recovery.finish.assert_not_called()
        self.assertFalse(any(call.args[0] in ('del_mon_iface','kill_hcx_pid') for call in runner.run.call_args_list))

    def test_unconfirmed_recovery_is_not_forgotten(self):
        lane,runner=self.fixture();old=lane._recovery;old.recovered_on_reconnect.return_value=False
        with patch('modules.capture_services.openwrt.auto_capture_service.threading.Thread') as thread:lane.start('',False)
        thread.assert_not_called();self.assertIs(lane._recovery,old)
        self.assertEqual(lane.remote_cap_file,'/root/cap/previous.pcapng')

    def test_unreleased_old_device_resources_block_start_on_another_device(self):
        lane,runner=self.fixture();lane._created_stubs=['hcxstub2']
        lane.ssh_client.host_fingerprint='other-device'
        gate=Mock()
        with patch('modules.capture_services.openwrt.auto_capture_service.threading.Thread') as thread:lane.start('',False,gate)
        thread.assert_not_called();gate.cancel.assert_called_once()
        self.assertEqual(lane._created_stubs,['hcxstub2'])
        self.assertEqual(lane._bound_fingerprint,'same')

    def test_old_worker_must_finish_before_new_attempt_mutates_its_state(self):
        lane,runner=self.fixture();lane._worker_thread=Mock();lane._worker_thread.is_alive.return_value=True
        gate=Mock()
        with patch('modules.capture_services.openwrt.auto_capture_service.threading.Thread') as thread:lane.start('',False,gate)
        thread.assert_not_called();gate.cancel.assert_called_once()
        self.assertEqual(lane.remote_cap_file,'/root/cap/previous.pcapng')


class RetryPreparationTests(unittest.TestCase):
    def test_reset_requires_confirmed_monitor_phy_and_up_without_old_pid_kill_or_sleep(self):
        from modules.capture_services.openwrt.auto_capture_cleanup import finished_attempt_reset_command
        command=finished_attempt_reset_command('hcxmon7','phy2','/root/cap/hcxmon7_20261103_194211.pcapng')
        for forbidden in ('kill ', 'sleep ', 'interface add', 'interface del'):
            self.assertNotIn(forbidden,command)
        for required in ('wiphy 2','type monitor','ip link set','flags & 1','printf READY'):
            self.assertIn(required,command)
        with self.assertRaises(ValueError):
            finished_attempt_reset_command('hcxmon7','phy2','/root/cap/other.pcapng')

    def test_failed_reset_is_not_silently_treated_as_ready(self):
        from modules.capture_services.openwrt.auto_capture_cleanup import reset_finished_attempt
        runner=Mock();runner.run.return_value=RemoteCommandResult('reset_iface',48,'','device failed')
        with self.assertRaisesRegex(RuntimeError,'stage=set_up'):
            reset_finished_attempt(runner,'hcxmon7','phy2','/root/cap/hcxmon7_20261103_194211.pcapng')
        runner.run.return_value=RemoteCommandResult('reset_iface',0,'')
        with self.assertRaisesRegex(RuntimeError,'stage=response'):
            reset_finished_attempt(runner,'hcxmon7','phy2','/root/cap/hcxmon7_20261103_194211.pcapng')
        runner.run.return_value=RemoteCommandResult('reset_iface',0,'READY')
        reset_finished_attempt(runner,'hcxmon7','phy2','/root/cap/hcxmon7_20261103_194211.pcapng')


class ProcessCleanupIdentityTests(unittest.TestCase):
    def test_stop_command_checks_actual_tool_and_interface_and_bounds_cmdline(self):
        from modules.capture_services.openwrt.auto_capture_cleanup import owned_capture_stop_command
        command=owned_capture_stop_command('hcxmon7')
        self.assertIn('comm',command);self.assertIn('hcxdumptool',command)
        self.assertIn('previous=="-i"',command);self.assertIn('head -c 8193',command)
        self.assertIn('own_live && kill -TERM',command)
        self.assertIn('own_live && kill -KILL',command)
        self.assertNotIn('killall',command);self.assertNotIn('pkill',command)
        with self.assertRaises(ValueError):owned_capture_stop_command('other-interface')
