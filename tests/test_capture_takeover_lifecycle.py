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


    def test_concurrent_prepare_takeovers_are_serialized(self):
        import threading
        import time
        active_sections = []
        max_concurrent = [0]
        lock = threading.Lock()

        def slow_prepare_config(phy, radio, sid, cancel):
            with lock:
                active_sections.append(radio)
                if len(active_sections) > max_concurrent[0]:
                    max_concurrent[0] = len(active_sections)
            time.sleep(0.05)
            with lock:
                active_sections.remove(radio)
            return RadioTakeoverRecord(sid, radio, phy, ('business',), 'token_' + radio)

        ws1 = CaptureWorkspace('1'*32, self.root/('1'*32), PurePosixPath('/tmp/owned1'))
        ws2 = CaptureWorkspace('2'*32, self.root/('2'*32), PurePosixPath('/tmp/owned2'))

        t1_takeover = OpenWrtRadioTakeover(Mock())
        t1_takeover.snapshot_wireless = Mock(return_value="wireless.home.key='secret'\n")
        t1_takeover.resolve_radio = Mock(return_value='radio0')
        t1_takeover.prepare_config_takeover = Mock(side_effect=slow_prepare_config)
        t1_takeover.takeover_prepared = Mock()

        t2_takeover = OpenWrtRadioTakeover(Mock())
        t2_takeover.snapshot_wireless = Mock(return_value="wireless.home.key='secret'\n")
        t2_takeover.resolve_radio = Mock(return_value='radio1')
        t2_takeover.prepare_config_takeover = Mock(side_effect=slow_prepare_config)
        t2_takeover.takeover_prepared = Mock()

        res = [None, None]
        errors = []

        def run1():
            try:
                res[0] = prepare_takeover(t1_takeover, self.ssh, ws1, ws1.session_id, 'phy0', Event(), self.notify, expected_radio='radio0')
            except Exception as e:
                errors.append(e)

        def run2():
            try:
                res[1] = prepare_takeover(t2_takeover, self.ssh, ws2, ws2.session_id, 'phy1', Event(), self.notify, expected_radio='radio1')
            except Exception as e:
                errors.append(e)

        th1 = threading.Thread(target=run1)
        th2 = threading.Thread(target=run2)
        th1.start(); th2.start()
        th1.join(); th2.join()

        self.assertEqual(errors, [])
        self.assertEqual(max_concurrent[0], 1)
        self.assertEqual(res[0].radio, 'radio0')
        self.assertEqual(res[1].radio, 'radio1')

    def test_takeover_transaction_cancels_cleanly(self):
        from modules.capture_services.openwrt.capture_takeover import takeover_transaction, _TAKEOVER_MUTEX
        import threading
        cancel = Event()
        error = []

        with _TAKEOVER_MUTEX:
            cancel.set()
            def worker():
                try:
                    with takeover_transaction(cancel):
                        pass
                except Exception as e:
                    error.append(e)
            th = threading.Thread(target=worker)
            th.start()
            th.join()

        self.assertEqual(len(error), 1)
        self.assertIn("操作已取消", str(error[0]))

    def test_takeover_lua_scopes_to_target_radio(self):
        from modules.capture_services.openwrt.takeover_config import TAKEOVER_LUA
        self.assertIn("local function radio_sections(sections, target_radio)", TAKEOVER_LUA)
        self.assertIn("local current_radio = radio_sections(current, request.radio)", TAKEOVER_LUA)
        self.assertIn("local original_radio = radio_sections(original, request.radio)", TAKEOVER_LUA)
        self.assertIn("local expected_radio = radio_sections(expected, request.radio)", TAKEOVER_LUA)
        self.assertIn("if not same(current_radio, original_radio) and not same(current_radio, expected_radio) then fail(\"changed\") end", TAKEOVER_LUA)
        self.assertIn("if not same(current_radio, desired_radio) then", TAKEOVER_LUA)
        self.assertIn("if not same(radio_sections(all(isolated), request.radio), desired_radio) then fail(\"changed\") end", TAKEOVER_LUA)
        self.assertIn("if not same(radio_sections(all(cursor()), request.radio), desired_radio) then fail(\"unconfirmed\") end", TAKEOVER_LUA)

    def test_concurrent_restore_takeovers_are_serialized_with_notify(self):
        import threading
        import time
        timeline = []
        lock = threading.Lock()

        ws1 = CaptureWorkspace('3'*32, self.root/('3'*32), PurePosixPath('/tmp/owned3'))
        ws2 = CaptureWorkspace('4'*32, self.root/('4'*32), PurePosixPath('/tmp/owned4'))
        ws1.local_dir.mkdir(parents=True, exist_ok=True)
        ws2.local_dir.mkdir(parents=True, exist_ok=True)

        j1 = SessionJournal('3'*32, 'auto_capture', '/tmp/owned3', [], '', 'snap', 'takeover:radio0', 'radio0', ('phy0-ap0',), 'phy0', 'tok1', 'device-A')
        j2 = SessionJournal('4'*32, 'auto_capture', '/tmp/owned4', [], '', 'snap', 'takeover:radio1', 'radio1', ('phy1-ap0',), 'phy1', 'tok2', 'device-A')
        (ws1.local_dir / 'takeover-journal.json').write_text(json.dumps(j1.to_dict()), encoding='utf-8')
        (ws2.local_dir / 'takeover-journal.json').write_text(json.dumps(j2.to_dict()), encoding='utf-8')

        r1 = RadioTakeoverRecord('3'*32, 'radio0', 'phy0', ('phy0-ap0',), 'tok1')
        r2 = RadioTakeoverRecord('4'*32, 'radio1', 'phy1', ('phy1-ap0',), 'tok2')

        t1 = OpenWrtRadioTakeover(Mock())
        def slow_restore1(record, cancel_event=None):
            with lock: timeline.append(('restore_start', record.radio))
            time.sleep(0.04)
            with lock: timeline.append(('restore_end', record.radio))
            return True
        t1.restore = Mock(side_effect=slow_restore1)
        t1.audit_restore = Mock(return_value='')

        t2 = OpenWrtRadioTakeover(Mock())
        t2.restore = Mock(side_effect=slow_restore1)
        t2.audit_restore = Mock(return_value='')

        def notify1(cancel):
            with lock: timeline.append(('notify', 'radio0'))
            return True
        def notify2(cancel):
            with lock: timeline.append(('notify', 'radio1'))
            return True

        errors = []
        def run1():
            errs = restore_takeover(t1, self.ssh, ws1, r1, notify=notify1)
            errors.extend(errs)
        def run2():
            errs = restore_takeover(t2, self.ssh, ws2, r2, notify=notify2)
            errors.extend(errs)

        th1 = threading.Thread(target=run1)
        th2 = threading.Thread(target=run2)
        th1.start(); th2.start()
        th1.join(); th2.join()

        self.assertEqual(errors, [])
        # Verify complete atomicity: restore_start -> restore_end -> notify of one radio happens BEFORE the other radio starts
        first_radio = timeline[0][1]
        second_radio = 'radio1' if first_radio == 'radio0' else 'radio0'
        expected_sequence = [
            ('restore_start', first_radio),
            ('restore_end', first_radio),
            ('notify', first_radio),
            ('restore_start', second_radio),
            ('restore_end', second_radio),
            ('notify', second_radio),
        ]
        self.assertEqual(timeline, expected_sequence)

    def test_capture_startup_coordinator_syncs_multi_radio(self):
        """验证多射频启动协调门禁在所有射频 ready 后同时放行。"""
        import threading, time
        from modules.capture_services.openwrt.auto_capture_service import CaptureStartupCoordinator
        coord = CaptureStartupCoordinator(["radio0", "radio1"])
        order = []

        def worker(radio):
            time.sleep(0.05 if radio == "radio0" else 0.1)
            coord.mark_ready(radio)
            ok = coord.wait_ready(radio, timeout=2.0)
            order.append((radio, ok, time.monotonic()))

        t0 = threading.Thread(target=worker, args=("radio0",))
        t1 = threading.Thread(target=worker, args=("radio1",))
        t0.start(); t1.start()
        t0.join(); t1.join()

        self.assertEqual(len(order), 2)
        self.assertTrue(order[0][1])
        self.assertTrue(order[1][1])
        # Both workers should be released around the same time (difference < 0.1s)
        self.assertLess(abs(order[0][2] - order[1][2]), 0.15)

    def test_capture_startup_coordinator_releases_on_failure(self):
        """验证某一射频准备失败时立即唤醒其他等待射频，防止死锁。"""
        import threading, time
        from modules.capture_services.openwrt.auto_capture_service import CaptureStartupCoordinator
        coord = CaptureStartupCoordinator(["radio0", "radio1"])
        order = []

        def worker0():
            coord.mark_ready("radio0")
            ok = coord.wait_ready("radio0", timeout=2.0)
            order.append(("radio0", ok))

        t0 = threading.Thread(target=worker0)
        t0.start()
        time.sleep(0.05)
        coord.mark_failed("radio1", "Device busy")
        coord.cleanup_finished("radio1", True)
        t0.join(timeout=1.0)

        self.assertFalse(t0.is_alive())
        self.assertEqual(order, [("radio0", True)])

    def test_recovery_finish_with_detail_retains_error_message(self):
        """验证未确认资源时 finish 准确记录具体未确认原因，不吞异常。"""
        from modules.capture_services.openwrt.auto_capture_recovery import AutoCaptureRecovery
        from modules.capture_services.openwrt.workspace import CaptureWorkspaceFactory, local_session_root
        from unittest.mock import MagicMock

        backend = MagicMock()
        backend.ssh_client.host_fingerprint = "test-fp"
        takeover = MagicMock()
        takeover.pending_record = None

        rec = AutoCaptureRecovery(backend, takeover)
        ws = CaptureWorkspaceFactory.create("test_session_detail", local_session_root(), "/tmp/winbox-capture")
        ws.local_dir.mkdir(parents=True, exist_ok=True)
        rec.workspace = ws
        rec.record = MagicMock()

        detail_msg = "Capture process cleanup failed: exit code 45"
        errors = rec.finish(resources_confirmed=False, detail=detail_msg)

        self.assertEqual(len(errors), 1)
        self.assertIn("Capture process cleanup failed", errors[0])
        audit_text = rec.report_path.read_text(encoding="utf-8")
        self.assertIn("Capture process cleanup failed", audit_text)
