"""Journal and real recovery-interface regressions without router access."""
import json
from pathlib import Path,PurePosixPath
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import unittest
from modules.capture_services.openwrt.capture_takeover import prepare_takeover,restore_takeover
from modules.capture_services.openwrt.radio_takeover import OpenWrtRadioTakeover,RadioTakeoverRecord
from modules.capture_services.openwrt.workspace import CaptureWorkspace
from modules.capture_services.openwrt.auto_capture_recovery import AutoCaptureRecovery,check_reconnect_resources
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
from modules.capture_services.openwrt.session_journal import SessionJournal


class TakeoverLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=CaptureWorkspace('a'*32,self.root/('a'*32),PurePosixPath('/tmp/owned'))
        self.ssh=NS(connected=True,host_fingerprint='device-A')
        self.takeover=OpenWrtRadioTakeover(Mock())
        self.takeover.snapshot_wireless=Mock(return_value="wireless.home.key='secret'\n")
        self.takeover.resolve_radio=Mock(return_value='radio7')
        self.takeover.prepare_config_takeover=Mock(side_effect=lambda phy,radio,sid,cancel:
            RadioTakeoverRecord(sid,radio,phy,('business',),'b'*32))
        self.takeover._config_command=Mock()
        self.takeover._wait_phy_interfaces=Mock(return_value=())
        self.takeover._verify_restored_interfaces=Mock(return_value=([],False))
        self.takeover.audit_restore=Mock(return_value='')
        self.notify=Mock(return_value=True)

    def prepare(self):
        return prepare_takeover(self.takeover,self.ssh,self.workspace,self.workspace.session_id,
                                'phy2',Event(),self.notify,expected_radio='radio7')

    def journal(self):
        return json.loads((self.workspace.local_dir/'takeover-journal.json').read_text(encoding='utf-8'))

    def test_journal_is_saved_before_disable_and_snapshot_is_redacted(self):
        def config(action,record,cancel):
            self.assertEqual(action,'takeover_disable')
            self.assertEqual(self.journal()['config_token'],record.config_token)
            self.assertEqual(self.journal()['device_fingerprint'],'device-A')
        self.takeover._config_command.side_effect=config
        self.prepare()
        self.assertNotIn('secret',(self.workspace.local_dir/'wireless-snapshot.txt').read_text(encoding='utf-8'))
        self.notify.assert_called_once()

    def test_disable_verification_failure_preserves_pending_for_real_restore(self):
        self.takeover._wait_phy_interfaces.side_effect=RuntimeError('down check failed')
        with self.assertRaisesRegex(RuntimeError,'down check failed'):self.prepare()
        record=self.takeover.pending_record
        self.assertIsInstance(record,RadioTakeoverRecord)
        self.assertEqual(restore_takeover(self.takeover,self.ssh,self.workspace,None),[])
        self.assertEqual(self.takeover._config_command.call_args.args[0],'takeover_restore')
        self.assertEqual(self.journal()['cleanup_stage'],'restored')
        self.assertIsNone(self.takeover.pending_record)

    def test_refresh_failure_still_has_recoverable_journal(self):
        self.notify.return_value=False
        with self.assertRaises(RuntimeError):self.prepare()
        self.assertIsNotNone(self.takeover.pending_record)
        self.assertNotEqual(self.journal()['cleanup_stage'],'restored')

    def test_failed_journal_write_prevents_disable(self):
        original=Path.write_text
        def write(path,*args,**kwargs):
            if path.name.startswith('takeover-journal'):raise OSError('disk full')
            return original(path,*args,**kwargs)
        with patch.object(Path,'write_text',write),self.assertRaises(RuntimeError):self.prepare()
        self.takeover._config_command.assert_not_called()

    def test_audit_warning_keeps_journal_pending_and_is_written(self):
        record=self.prepare();self.takeover.audit_restore.return_value='business still missing'
        errors=restore_takeover(self.takeover,self.ssh,self.workspace,record)
        self.assertIn('business still missing',errors)
        self.assertNotEqual(self.journal()['cleanup_stage'],'restored')
        self.assertIs(self.takeover.pending_record,record)
        report=(self.workspace.local_dir/'takeover-restore-audit.txt').read_text(encoding='utf-8')
        self.assertIn('business still missing',report)
        receipt=json.loads((self.workspace.local_dir/'takeover-recovery-result.json').read_text())
        self.assertEqual(receipt['status'],'pending')

    def test_audit_exception_never_becomes_success(self):
        record=self.prepare();self.takeover.audit_restore.side_effect=RuntimeError('probe failed')
        self.assertIn('probe failed',restore_takeover(self.takeover,self.ssh,self.workspace,record))
        self.assertNotEqual(self.journal()['cleanup_stage'],'restored')

    def test_changed_device_never_receives_restore_command(self):
        record=self.prepare();self.takeover._config_command.reset_mock()
        self.ssh.host_fingerprint='device-B'
        self.assertTrue(restore_takeover(self.takeover,self.ssh,self.workspace,record))
        self.takeover._config_command.assert_not_called()

    def test_disconnection_preserves_recovery_without_remote_commands(self):
        record=self.prepare();self.takeover._config_command.reset_mock();self.ssh.connected=False
        self.assertTrue(restore_takeover(self.takeover,self.ssh,self.workspace,record))
        self.takeover._config_command.assert_not_called()

    def test_wrong_radio_ownership_prevents_prepare(self):
        self.takeover.resolve_radio.return_value='radio8'
        with self.assertRaises(RuntimeError):self.prepare()
        self.takeover.prepare_config_takeover.assert_not_called()

    def auto(self):
        backend=NS(ssh_client=self.ssh,_notify_signal_changed=self.notify,note_capture_finished=Mock())
        recovery=AutoCaptureRecovery(backend,self.takeover)
        with patch('modules.capture_services.openwrt.auto_capture_recovery.local_session_root',return_value=self.root):
            recovery.prepare('phy2','radio7','hcxmon7',Event())
        self.addCleanup(lambda:recovery.finish(False) if recovery.workspace.local_dir.exists() and recovery.record else None)
        return recovery

    def test_auto_uses_standard_journal_and_existing_output_folder_for_report(self):
        recovery=self.auto()
        data=json.loads((recovery.workspace.local_dir/'takeover-journal.json').read_text())
        self.assertEqual(data['task_type'],'auto_capture');self.assertEqual(data['monitor_section'],'hcxmon7')
        self.assertEqual(recovery.finish(),[])
        report=Path(recovery.publish(self.root/'output','radio7'))
        self.assertEqual(report.name,'latest-radio7.txt')
        self.assertIn('recovery-reports',str(report))
        self.assertTrue(report.is_file())
        self.assertIsNone(recovery.record)

    def test_auto_unconfirmed_resources_defer_wireless_restore(self):
        recovery=self.auto();self.takeover._config_command.reset_mock()
        self.assertTrue(recovery.finish(resources_confirmed=False))
        self.takeover._config_command.assert_not_called()
        self.assertIsNotNone(recovery.record)
        self.assertTrue(recovery.report_path.is_file())

    def test_reconnect_uses_same_restore_and_persists_receipt(self):
        recovery=self.auto();backend=OpenWrtCaptureBackend();backend.ssh_client=self.ssh
        backend.radio_takeover=self.takeover
        path=recovery.workspace.local_dir/'takeover-journal.json'
        journal=SessionJournal.from_dict(json.loads(path.read_text()))
        with patch('modules.capture_services.openwrt.auto_capture_recovery.check_reconnect_resources') as check:
            error=backend._recover_single_takeover(journal,Event(),journal_path=path)
        self.assertIsNone(error);check.assert_called_once()
        self.assertTrue(recovery.recovered_on_reconnect())
        self.assertIsNone(recovery.record)

    def test_pending_auto_recovery_does_not_overwrite_original_session(self):
        recovery=self.auto();original=recovery.workspace
        with self.assertRaises(RuntimeError):recovery.prepare('phy2','radio7','hcxmon7',Event())
        self.assertIs(recovery.workspace,original)

    def test_fresh_pending_record_is_available_to_export_before_reconnect_retry(self):
        recovery=self.auto();backend=OpenWrtCaptureBackend();backend.ssh_client=self.ssh
        backend.capture_diagnostic_callback=Mock()
        backend.radio_takeover.restore=Mock()
        with patch('modules.capture_services.backends.openwrt_backend.local_session_root',return_value=self.root):
            backend._recover_crashed_takeovers(Event())
        backend.capture_diagnostic_callback.assert_called_once()
        workspace=backend.capture_diagnostic_callback.call_args.args[0]
        self.assertEqual(workspace.local_dir,recovery.workspace.local_dir)
        backend.radio_takeover.restore.assert_not_called()

    def test_live_process_blocks_reconnect_restore_before_interface_removal(self):
        runner=Mock();runner.run.return_value=RemoteCommandResult('process',42)
        journal=NS(monitor_section='hcxmon7',phy='phy2')
        with patch('modules.capture_services.openwrt.auto_capture_cleanup.remove_monitor') as remove:
            with self.assertRaises(RuntimeError):check_reconnect_resources(runner,journal)
        remove.assert_not_called()
        command=runner.run.call_args.args[1]
        self.assertIn('kill -0 "$p"',command)
        self.assertNotIn('kill -TERM',command)
