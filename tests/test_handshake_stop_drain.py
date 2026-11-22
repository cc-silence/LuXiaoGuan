"""Cancellation releases radio before draining already downloaded results."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from dataclasses import replace
from modules.capture_services.handshake_processing import HandshakeProcessingQueue, DeferredHandshake
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend, HandshakeTargetResult
from modules.capture_services.openwrt.handshake_capture import HandshakeCaptureResult, HandshakeCaptureUpdate
from modules.capture_services.openwrt.models import AccessPointInfo, OpenWrtSessionSnapshot, CaptureTaskType
from modules.capture_services.capture_service import CaptureService

class StopDrainTests(unittest.TestCase):
    def test_cancel_wait_keeps_downloaded_job_for_later_completion(self):
        queue=HandshakeProcessingQueue();gate=Event();cancel=Event()
        def finish():
            if not gate.wait(3):raise RuntimeError('fixture gate expired')
            return (('saved',False),)
        queue.submit(('target',),DeferredHandshake(finish));cancel.set()
        try:
            self.assertIsNone(queue.pop(wait=True,cancel_event=cancel))
            self.assertTrue(queue.pending)
            gate.set()
            self.assertEqual(queue.pop(wait=True)[1],(('saved',False),))
        finally:gate.set();queue.close()

    def test_batch_restores_before_waiting_and_saves_after_stop(self):
        target=AccessPointInfo('02:11:22:33:44:55',band='2.4G',channel=6)
        cancel=Event();released=Event();order=[];stages=[]
        backend=NS(ssh_client=NS(connected=True),_ensure_remote_tmp=Mock(),reconnect_cleanup=Mock(),workspace_manager=Mock(),
                   _finish_capture_task=Mock(side_effect=lambda *a:order.append('finish')))
        class Radio:
            def __init__(self,*a):self.record=object();self.takeover=object()
            def release(self,*a):
                order.append('restore');released.set();self.record=None;self.takeover=None;return []
        def capture(*a,**k):
            cancel.set()
            def finish():
                if not released.wait(3):raise RuntimeError('radio was not restored before waiting')
                order.append('save')
                return ((HandshakeTargetResult(target,HandshakeCaptureResult('',target.bssid,'',verdict='complete')),False),)
            return DeferredHandshake(finish)
        backend._capture_single_handshake=capture
        with TemporaryDirectory() as folder,patch('modules.capture_services.openwrt.handshake_batch_radio.HandshakeBatchRadio',Radio):
            result=OpenWrtCaptureBackend.capture_handshakes_passive(backend,'session',
                {'resolved_output_dir':folder,'handshake_rounds':1},(target,),cancel,
                progress_callback=lambda u:stages.append(u.stage))
        self.assertEqual(order,['restore','save','finish'])
        self.assertTrue(result.cancelled)
        self.assertEqual(result.results[0].result.verdict,'complete')
        self.assertIn('restoring',stages);self.assertIn('saving_existing',stages)

    def test_stopping_snapshot_still_displays_saving_stage(self):
        snapshot=replace(OpenWrtSessionSnapshot(),task_type=CaptureTaskType.HANDSHAKE,task_state='stopping')
        service=NS(openwrt_backend=NS(snapshot=snapshot),openwrtSnapshotChanged=Mock())
        CaptureService._on_openwrt_handshakes_progress(service,HandshakeCaptureUpdate(0,0,stage='saving_existing'))
        self.assertEqual(service.openwrt_backend.snapshot.task_state,'stopping')
        self.assertNotEqual(service.openwrt_backend.snapshot.progress,snapshot.progress)
        service.openwrtSnapshotChanged.emit.assert_called_once()
