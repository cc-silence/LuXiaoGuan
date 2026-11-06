"""Owned source address must match Reaver; failures block launch and restore resources."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.command_runner import RemoteCommandResult as R
from modules.capture_services.openwrt.models import AccessPointInfo
from modules.capture_services.openwrt.radio_session import RadioSessionRecord
from modules.capture_services.openwrt.wps_source import ensure_wps_source
from modules.capture_services.openwrt.wps_protocol_evidence import WpsProtocolEvidence


class WpsSourceTests(unittest.TestCase):
    def setUp(self):
        self.record = RadioSessionRecord('owned', 'phy1', 'ownedmon', '', ownership_token='routermanager:owned')
        self.options = dict(spoof_mac_enabled=True, spoof_mac='02:11:22:33:44:55')

    def info(self, mac='00:11:22:33:44:55', kind='monitor', code=0):
        return R('fixture', code, stdout=f'Interface ownedmon\n addr {mac}\n type {kind}\n')

    def test_disabled_allows_empty_without_touching_interface(self):
        runner = Mock()
        self.assertFalse(ensure_wps_source(runner, self.record, dict(spoof_mac_enabled=False, spoof_mac='')))
        runner.run.assert_not_called()

    def test_existing_address_is_read_only(self):
        runner = Mock(); runner.run.return_value = self.info('02:11:22:33:44:55')
        self.assertTrue(ensure_wps_source(runner, self.record, self.options))
        runner.run.assert_called_once()

    def test_apply_and_readback_are_guarded_and_only_touch_owned_interface(self):
        runner = Mock(); runner.run.side_effect = [self.info(), R('down',0), R('address',0), R('up',0), self.info('02:11:22:33:44:55')]
        self.assertTrue(ensure_wps_source(runner, self.record, self.options))
        commands = [call.args[1] for call in runner.run.call_args_list]
        for command in commands:
            self.assertIn('ifalias',command); self.assertIn('phy80211',command)
            self.assertIn('routermanager:owned',command)
            self.assertNotIn('wifi reload',command)
        for command,suffix in zip(commands[1:4], ['down','address 02:11:22:33:44:55','up']):
            self.assertTrue(command.endswith('ip link set dev ownedmon ' + suffix))

    def test_each_failed_mutation_stops_without_further_commands(self):
        for index in range(3):
            runner=Mock(); runner.run.side_effect=[self.info()]+[R('ok',0)]*index+[R('fail',1)]
            with self.assertRaises(RuntimeError): ensure_wps_source(runner,self.record,self.options)
            self.assertEqual(runner.run.call_count,index+2)

    def test_cancelled_change_does_not_continue(self):
        runner=Mock();runner.run.side_effect=[self.info(),R('cancel',-1,cancelled=True)]
        with self.assertRaises(InterruptedError): ensure_wps_source(runner,self.record,self.options)
        self.assertEqual(runner.run.call_count,2)

    def test_failed_readback_or_wrong_type_blocks_launch(self):
        for info in [self.info(),self.info(kind='managed')]:
            runner=Mock();runner.run.side_effect=[self.info(),R('down',0),R('address',0),R('up',0),info]
            with self.assertRaises(RuntimeError): ensure_wps_source(runner,self.record,self.options)
        for info in [self.info(kind='managed'),self.info(code=83),R('bad',0,stdout='type monitor')]:
            runner=Mock();runner.run.return_value=info
            with self.assertRaises(RuntimeError): ensure_wps_source(runner,self.record,self.options)
            runner.run.assert_called_once()

    def test_missing_ownership_token_never_mutates(self):
        runner=Mock()
        with self.assertRaises(RuntimeError): ensure_wps_source(runner,RadioSessionRecord('owned','phy1','ownedmon',''),self.options)
        runner.run.assert_not_called()

    def test_backend_source_failure_restores_without_tuning_or_launch(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        backend=object.__new__(OpenWrtCaptureBackend)
        backend.ssh_client=NS(connected=True);backend.reconnect_cleanup=Mock();backend.workspace_manager=Mock()
        backend.command_runner=Mock();backend.wps_capture=Mock();backend.wps_capture.skipped_result.return_value=None
        backend._find_scan_radio=Mock(return_value='phy1');backend._maybe_takeover=Mock(return_value=None)
        backend.radio_session=Mock();backend.radio_session.create.return_value=self.record
        backend._restore_task_wireless=Mock(return_value=[]);backend._finish_capture_task=Mock()
        with TemporaryDirectory() as folder, patch('modules.capture_services.backends.openwrt_backend.CaptureWorkspaceFactory.create',return_value=NS(local_dir=Path(folder),remote_dir='/tmp/owned')), patch('modules.capture_services.openwrt.wps_source.ensure_wps_source',side_effect=RuntimeError('source failed')), patch('modules.capture_services.openwrt.wps_radio.ensure_wps_channel') as tune:
            results=backend.run_wps('owned',dict(resolved_output_dir=folder,wps_defaults=self.options),(AccessPointInfo('54:A0:50:75:D2:40',channel=1,wps_supported=True),),'normal')
        tune.assert_not_called();backend.wps_capture.run_targets.assert_not_called()
        backend._restore_task_wireless.assert_called_once();backend._finish_capture_task.assert_called_once()
        self.assertIn('source failed',results[0].note)
        order=[]
        with TemporaryDirectory() as folder, patch('modules.capture_services.backends.openwrt_backend.CaptureWorkspaceFactory.create',return_value=NS(local_dir=Path(folder),remote_dir='/tmp/owned')), patch('modules.capture_services.openwrt.wps_source.ensure_wps_source',side_effect=lambda *args: order.append('source') or True), patch('modules.capture_services.openwrt.wps_radio.ensure_wps_channel',side_effect=lambda *args: (order.append('channel'), (_ for _ in ()).throw(RuntimeError('channel failed')))):
            backend.run_wps('owned',dict(resolved_output_dir=folder,wps_defaults=self.options),(AccessPointInfo('54:A0:50:75:D2:40',channel=1,wps_supported=True),),'normal')
        self.assertEqual(order,['source','channel'])
        backend.wps_capture.run_targets.assert_not_called()

    def test_candidate_evidence_is_bounded_and_survives_later_status(self):
        evidence=WpsProtocolEvidence()
        for value in range(1000): evidence.feed(f'Trying pin "{value:08d}"')
        evidence.feed('Associated with fixture\nWPS PIN: 87654321\nWPA PSK: privatepass')
        self.assertEqual(evidence.current_pin,'00000999')
        self.assertIn('00000999',evidence.describe())
        self.assertNotIn('87654321',repr(vars(evidence)))
        self.assertNotIn('privatepass',repr(vars(evidence)))
