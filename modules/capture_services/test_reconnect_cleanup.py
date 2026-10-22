"""Reconnect recovery uses temporary workspaces and offline SSH substitutes."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path, PurePosixPath
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
from modules.capture_services.openwrt.models import CaptureCleanupError, OpenWrtDeviceInfo
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.remote_process import RemoteProcessHandle
from modules.capture_services.openwrt.radio_session import RadioSessionRecord
from modules.capture_services.openwrt.workspace import CaptureWorkspace, CaptureWorkspaceManager


class ReconnectCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = CaptureWorkspace("a" * 32, Path(temporary.name)/("a"*32), PurePosixPath("/tmp/winbox-capture")/("a"*32))
        self.workspace.local_dir.mkdir()
        self.backend = OpenWrtCaptureBackend()
        self._set_ssh_client(SimpleNamespace(connected=True))
        self.backend.command_runner.run = Mock(return_value=RemoteCommandResult("fixture",0))
        self.backend.workspace_manager.cleanup_remote = Mock(return_value=True)
        self.backend.reconnect_cleanup.recover("device-A")
        self.handle = RemoteProcessHandle(123,"456","airodump-ng",self.workspace.session_id,str(self.workspace.remote_dir))
        self.backend.process_manager.register(self.handle)
        self.backend.radio_session.record = RadioSessionRecord(self.workspace.session_id,"phy0","wbmonfixture","")
        self.backend.reconnect_cleanup.remember(self.workspace,None)

    def _set_ssh_client(self, client):
        """模拟连接必须由后端、命令和传输组件共同持有。"""
        self.backend.ssh_client = client
        self.backend.command_runner.ssh_client = client
        self.backend.workspace_manager.ssh_client = client

    def test_fixture_shares_one_connection_with_all_collaborators(self):
        client = Mock(connected=True)
        self._set_ssh_client(client)
        self.assertIs(self.backend.ssh_client, client)
        self.assertIs(self.backend.dependency_manager.command_runner.ssh_client, client)
        self.assertIs(self.backend.workspace_manager.ssh_client, client)

    def test_sftp_disconnect_is_still_an_error(self):
        client = Mock(connected=False)
        client.open_sftp.side_effect = RuntimeError('fixture disconnected')
        self._set_ssh_client(client)
        with self.assertRaises(SignalError):
            self.backend.dependency_manager.check_sftp()
        client.open_sftp.assert_called_once_with(timeout=10, cancel_event=None)

    def test_sftp_connection_replacement_is_still_an_error(self):
        client = Mock(connected=True)
        self._set_ssh_client(client)
        def replace_connection(*args, **kwargs):
            client._client = object()
        client.open_sftp.side_effect = replace_connection
        with self.assertRaises(SignalError):
            self.backend.dependency_manager.check_sftp()

    def test_same_device_recovers_process_then_interface_then_directory(self):
        calls=[]
        self.backend.command_runner.run.side_effect=lambda action,*a,**k: (calls.append(action) or RemoteCommandResult(action,0))
        self.backend.workspace_manager.cleanup_remote.side_effect=lambda *a,**k: (calls.append("workspace") or True)
        with patch("modules.capture_services.local_cache_cleanup.acknowledge_session") as acknowledge:
            note=self.backend.reconnect_cleanup.recover("device-A")
        self.assertTrue(note)
        self.assertEqual(calls,["stop_airodump-ng","remove_monitor","workspace"])
        self.assertFalse(self.backend.radio_session.active)
        self.assertEqual(self.backend.process_manager.handles(),())
        self.assertIsNone(self.backend.reconnect_cleanup.pending)
        acknowledge.assert_called_once_with(self.workspace,True)
        self.backend.reconnect_cleanup.require_ready()

    def test_device_mismatch_and_unknown_identity_make_no_remote_calls(self):
        for fingerprint in ("device-B", ""):
            with self.subTest(fingerprint=fingerprint), self.assertRaises(RuntimeError):
                self.backend.reconnect_cleanup.recover(fingerprint)
        self.backend.command_runner.run.assert_not_called()
        self.backend.workspace_manager.cleanup_remote.assert_not_called()
        self.assertIsNotNone(self.backend.reconnect_cleanup.pending)

    def test_process_stop_failure_keeps_all_records_and_blocks_new_scan(self):
        self.backend.command_runner.run.return_value=RemoteCommandResult("stop",1)
        with self.assertRaises(RuntimeError):self.backend.reconnect_cleanup.recover("device-A")
        self.assertTrue(self.backend.radio_session.active)
        self.assertEqual(self.backend.process_manager.handles(),(self.handle,))
        self.backend.workspace_manager.cleanup_remote.assert_not_called()
        with self.assertRaises(RuntimeError):self.backend._ensure_remote_tmp()
        self.assertIsNotNone(self.backend.reconnect_cleanup.pending)

    def test_directory_failure_keeps_gate_then_retry_succeeds(self):
        self.backend.workspace_manager.cleanup_remote.return_value=False
        with self.assertRaises(RuntimeError):self.backend.reconnect_cleanup.recover("device-A")
        self.assertFalse(self.backend.radio_session.active)
        with self.assertRaises(RuntimeError):self.backend.reconnect_cleanup.require_ready()
        self.backend.workspace_manager.cleanup_remote.return_value=True
        with patch("modules.capture_services.local_cache_cleanup.acknowledge_session"):
            self.backend.reconnect_cleanup.recover("device-A")
        self.backend.reconnect_cleanup.require_ready()

    def test_cancel_before_or_during_recovery_keeps_record(self):
        event=Event();event.set()
        with self.assertRaises(InterruptedError):self.backend.reconnect_cleanup.recover("device-A",event)
        self.backend.command_runner.run.assert_not_called()
        event.clear()
        def stop(*a,**k):event.set();return RemoteCommandResult("stop",0)
        self.backend.command_runner.run.side_effect=stop
        with self.assertRaises(InterruptedError):self.backend.reconnect_cleanup.recover("device-A",event)
        self.assertIsNotNone(self.backend.reconnect_cleanup.pending)
        self.backend.workspace_manager.cleanup_remote.assert_not_called()

    def test_invalid_directory_is_not_deleted(self):
        cleanup=self.backend.reconnect_cleanup
        cleanup.pending=replace(cleanup.pending,workspace=replace(self.workspace,remote_dir=PurePosixPath("/tmp")))
        with self.assertRaises(RuntimeError):cleanup.recover("device-A")
        self.backend.command_runner.run.assert_not_called()
        self.backend.workspace_manager.cleanup_remote.assert_not_called()

    def test_disconnected_finally_registers_original_workspace(self):
        self.backend.reconnect_cleanup.pending=None
        self.backend.ssh_client.connected=False
        with patch("modules.capture_services.local_cache_cleanup.acknowledge_session"),self.assertRaises(CaptureCleanupError):
            self.backend._finish_capture_task(self.workspace)
        pending=self.backend.reconnect_cleanup.pending
        self.assertIs(pending.workspace,self.workspace)
        self.assertEqual(pending.device_fingerprint,"device-A")
        self.backend.command_runner.run.assert_not_called()

    def test_login_runs_pending_recovery_before_probe_and_reaper(self):
        b=self.backend
        self._set_ssh_client(Mock(connected=True))
        b.ssh_client.connect.return_value=SimpleNamespace(fingerprint="device-A",key_type="fixture")
        b.device_probe.probe=Mock(return_value=OpenWrtDeviceInfo())
        b.dependency_manager.check=Mock(return_value=())
        b.offline_packages.match_device=Mock(return_value=SimpleNamespace(matched=True))
        b.radio_session.reap_stale_monitors=Mock(return_value=0)
        b.process_manager.reap_stale_processes=Mock(return_value=0)
        b._recycle_remote_residuals=Mock(return_value="")
        b._recover_crashed_takeovers=Mock(return_value="")
        def probe(*a):
            self.assertIsNone(b.reconnect_cleanup.pending)
            self.assertFalse(b.radio_session.active)
            return OpenWrtDeviceInfo()
        b.device_probe.probe.side_effect=probe
        with patch("modules.capture_services.local_cache_cleanup.acknowledge_session"):
            result=b.connect({"host":"router.example","port":22,"user":"root"},"mock-password")
        self.assertEqual(result.status,"connected")
        b.ssh_client.open_sftp.assert_called_once_with(timeout=10, cancel_event=None)
        self.assertIn("清理完成",result.detail)

    def test_login_cleanup_failure_closes_new_connection_without_losing_pending(self):
        b=self.backend;self._set_ssh_client(Mock(connected=True))
        b.ssh_client.connect.return_value=SimpleNamespace(fingerprint="device-A",key_type="fixture")
        b.device_probe.probe=Mock()
        b.workspace_manager.cleanup_remote.return_value=False
        with self.assertRaises(RuntimeError):b.connect({"host":"router.example","user":"root","port":22},"mock-password")
        b.ssh_client.close.assert_called_once()
        b.device_probe.probe.assert_not_called()
        b.ssh_client.open_sftp.assert_not_called()
        self.assertIsNotNone(b.reconnect_cleanup.pending)

    def test_recovery_budget_stops_before_further_cleanup(self):
        with patch("modules.capture_services.openwrt.reconnect_cleanup.time.monotonic", side_effect=[0, 121]):
            with self.assertRaises(TimeoutError):
                self.backend.reconnect_cleanup.recover("device-A")
        self.backend.command_runner.run.assert_not_called()
        self.assertIsNotNone(self.backend.reconnect_cleanup.pending)

    def test_parallel_collaborator_has_the_same_gate(self):
        from modules.capture_services.parallel_handshake import _RadioBackend
        ssh = SimpleNamespace(connected=False, host_fingerprint="device-A")
        unit = _RadioBackend(ssh, SimpleNamespace(phy="phy0", channels=()))
        unit.reconnect_cleanup.remember(self.workspace, None)
        with self.assertRaises(RuntimeError):
            unit.reconnect_cleanup.require_ready()

    def test_monitor_delete_timeout_retains_ownership_record(self):
        record = self.backend.radio_session.record
        self.backend.command_runner.run.return_value = RemoteCommandResult("remove", 0, timed_out=True)
        with self.assertRaises(TimeoutError):
            self.backend.radio_session.restore()
        self.assertIs(self.backend.radio_session.record, record)

    def test_failed_absence_probe_is_not_treated_as_missing_interface(self):
        record = self.backend.radio_session.record
        self.backend.command_runner.run.side_effect = [RemoteCommandResult("remove", 1), RemoteCommandResult("check", 127)]
        with self.assertRaises(RuntimeError):
            self.backend.radio_session.restore()
        self.assertIs(self.backend.radio_session.record, record)

    def test_cleanup_timeout_or_cancel_is_not_success(self):
        runner=Mock();manager=CaptureWorkspaceManager(Mock(),runner)
        for result in (RemoteCommandResult("cleanup",0,timed_out=True),RemoteCommandResult("cleanup",0,cancelled=True)):
            runner.run.return_value=result
            self.assertFalse(manager.cleanup_remote(self.workspace))


if __name__ == "__main__":unittest.main()
