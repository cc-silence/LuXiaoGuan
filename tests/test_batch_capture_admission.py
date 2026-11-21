"""An active batch owns its takeover; failed tuning cannot starve other targets."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend, HandshakeTargetResult
from modules.capture_services.openwrt.reconnect_cleanup import ReconnectCleanup
from modules.capture_services.openwrt.radio_takeover import RadioTakeoverRecord
from modules.capture_services.openwrt.handshake_capture import ChannelTuneError, HandshakeCaptureResult
from modules.capture_services.openwrt.models import AccessPointInfo


class ActiveBatchAdmissionTests(unittest.TestCase):
    def fixture(self):
        backend = type('Backend', (), {})()
        backend.ssh_client = NS(connected=True, host_fingerprint='device-A')
        backend.radio_takeover = NS(pending_record=RadioTakeoverRecord('session','radio0','phy0',(), 'token'))
        backend.radio_session = NS(record=NS(session_id='session',radio='phy0'))
        backend.password_recovery_store = None
        backend.reconnect_cleanup = ReconnectCleanup(backend)
        return backend

    def test_new_task_still_blocked_but_current_batch_can_continue(self):
        backend = self.fixture()
        with self.assertRaises(RuntimeError): backend.reconnect_cleanup.require_ready()
        backend.reconnect_cleanup.require_ready(active_session_id='session')
        self.assertIsNotNone(backend.radio_takeover.pending_record)

    def test_wrong_session_phy_or_missing_runtime_ownership_stays_blocked(self):
        for change in ('session','phy','missing'):
            with self.subTest(change=change):
                backend = self.fixture()
                if change=='session': backend.radio_session.record.session_id='other'
                elif change=='phy': backend.radio_session.record.radio='phy1'
                else: backend.radio_session.record=None
                with self.assertRaises(RuntimeError):
                    backend.reconnect_cleanup.require_ready(active_session_id='session')
        backend=self.fixture()
        with self.assertRaises(RuntimeError):backend.reconnect_cleanup.require_ready(active_session_id='other')

    def test_pending_cleanup_and_password_issue_cannot_be_bypassed(self):
        backend=self.fixture();backend.reconnect_cleanup.pending=object()
        with self.assertRaises(RuntimeError):backend.reconnect_cleanup.require_ready(active_session_id='session')
        backend=self.fixture();backend.reconnect_cleanup.set_password_check_issue('device-A',{'reason':'unavailable'})
        with self.assertRaises(RuntimeError):backend.reconnect_cleanup.require_ready(active_session_id='session')

    def test_real_target_path_passes_owned_session_through_space_check(self):
        backend=self.fixture()
        backend._TMP_MIN_FREE_MB=8
        backend._check_remote_tmp=Mock(return_value=64)
        backend._ensure_remote_tmp=lambda *a,**k: OpenWrtCaptureBackend._ensure_remote_tmp(backend,*a,**k)
        backend.workspace_manager=Mock()
        backend.handshake_capture=NS(capture_passive=Mock(side_effect=ChannelTuneError('wrong channel')))
        with TemporaryDirectory() as folder:
            workspace=NS(session_id='session',local_dir=Path(folder),remote_dir='/tmp/owned')
            batch=NS(workspace=workspace,record=backend.radio_session.record,
                takeover=backend.radio_takeover.pending_record,acquire=Mock(return_value=NS(interface='wbmon1')),
                release=Mock(return_value=[]))
            target=AccessPointInfo('02:11:22:33:44:55',band='2.4G',channel=2)
            entry,retry=OpenWrtCaptureBackend._capture_single_handshake(backend,workspace,'session',{},folder,
                target,0,1,1,Event(),None,batch_radio=batch)
        self.assertEqual(entry.error_code,'channel_tune_failed',entry.error)
        backend.handshake_capture.capture_passive.assert_called_once()
        self.assertFalse(entry.cleanup_errors)
        backend._check_remote_tmp.assert_called_once()


class BatchTuneContinuationTests(unittest.TestCase):
    def run_batch(self, grouped=True, rounds=5, cleanup=False, failure_code="channel_tune_failed"):
        targets=tuple(AccessPointInfo(mac,band='2.4G',channel=channel) for mac,channel in
            [('02:11:22:33:44:55',2),('02:11:22:33:44:66',6)])
        backend=NS(ssh_client=NS(connected=True),_ensure_remote_tmp=Mock(),reconnect_cleanup=Mock(),
            workspace_manager=Mock(),_finish_capture_task=Mock())
        calls=[];messages=[]
        def capture(*args,**kwargs):
            target,round_no=args[4],args[7];calls.append((target.bssid,round_no))
            if target==targets[0]:
                return HandshakeTargetResult(target,error='wrong channel',error_code=failure_code,
                    cleanup_errors=('cleanup failed',) if cleanup else ()),False
            return HandshakeTargetResult(target,HandshakeCaptureResult('',target.bssid,'',verdict='complete')),False
        backend._capture_single_handshake=capture
        with TemporaryDirectory() as folder:
            result=OpenWrtCaptureBackend.capture_handshakes_passive(backend,'session',
                {'resolved_output_dir':folder,'handshake_rounds':rounds},targets,Event(),
                message_callback=lambda *args:messages.append(args),group_same_channel=grouped)
        return targets,calls,result,messages

    def test_next_target_runs_before_retry_and_failed_target_stops_at_three(self):
        for grouped in (False,True):
            with self.subTest(grouped=grouped):
                targets,calls,result,_=self.run_batch(grouped)
                self.assertEqual(calls,[(targets[0].bssid,1),(targets[1].bssid,1),
                    (targets[0].bssid,2),(targets[0].bssid,3)])
                entries={entry.target.bssid:entry for entry in result.results}
                self.assertEqual(entries[targets[1].bssid].result.verdict,'complete')
                self.assertEqual(entries[targets[0].bssid].error_code,'channel_tune_failed')

    def test_round_limit_does_not_promise_nonexistent_next_round(self):
        targets,calls,result,_=self.run_batch(rounds=2)
        entry=next(entry for entry in result.results if entry.target.bssid==targets[0].bssid)
        self.assertEqual(len(calls),3)
        self.assertNotIn('\u4e0b\u4e00\u8f6e\u518d\u8bd5',entry.error)

    def test_real_cleanup_failure_still_stops_following_targets(self):
        targets,calls,result,_=self.run_batch(cleanup=True)
        self.assertEqual(calls,[(targets[0].bssid,1)])
        self.assertTrue(result.cleanup_errors)

    def test_exhausted_local_channel_failure_skips_target_and_keeps_next_result(self):
        targets,calls,result,_=self.run_batch(failure_code="capture_failed")
        self.assertEqual(calls,[(targets[0].bssid,1),(targets[1].bssid,1)])
        entries={entry.target.bssid:entry for entry in result.results}
        self.assertEqual(entries[targets[0].bssid].error_code,"capture_failed")
        self.assertEqual(entries[targets[1].bssid].result.verdict,"complete")
        self.assertFalse(result.cleanup_errors)
