"""Decision lifetime and main-thread presentation contracts."""
from threading import Event,Thread
import time
import unittest
from unittest.mock import Mock,patch
from PySide6.QtCore import Qt,QThread
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.capture_services.dependency_confirmation import PackageConfirmationGate
from modules.capture_services.openwrt.hcx_candidate import HcxReview
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.main_pages.packet_capture.openwrt.dependency_candidate_prompt import DependencyCandidatePrompt

REVIEW=HcxReview('6.3.4-r1','aarch64_cortex-a53','https://mirrors.ustc.edu.cn:443',37681,37675,'0'*64,'1'*64)

class DecisionGateTests(unittest.TestCase):
    def gate(self):
        gate=PackageConfirmationGate();gate.available=True
        self.addCleanup(gate.deleteLater)
        return gate

    def test_accept_and_decline_are_per_request(self):
        gate=self.gate();answers=iter([True,False])
        gate.requested.connect(lambda request:request.answer(next(answers)),Qt.DirectConnection)
        self.assertTrue(gate.request(REVIEW,None,lambda:True))
        self.assertFalse(gate.request(REVIEW,None,lambda:True))
        self.assertIsNone(gate.pending)

    def test_cancel_wins_over_queued_approval(self):
        gate=self.gate();cancel=Event()
        def reply(request):cancel.set();request.answer(True)
        gate.requested.connect(reply,Qt.DirectConnection)
        with self.assertRaises(SignalError) as caught:gate.request(REVIEW,cancel,lambda:True)
        self.assertEqual(caught.exception.code,'cancelled')
        self.assertIsNone(gate.pending)

    def test_connection_change_wins_over_approval(self):
        gate=self.gate();connected=[True]
        def reply(request):connected[0]=False;request.answer(True)
        gate.requested.connect(reply,Qt.DirectConnection)
        with self.assertRaises(SignalError) as caught:gate.request(REVIEW,None,lambda:connected[0])
        self.assertEqual(caught.exception.code,'disconnected')

    def test_timeout_rejects_late_approval_and_emits_close(self):
        gate=self.gate();requests=[];closed=[]
        gate.requested.connect(requests.append,Qt.DirectConnection)
        gate.closed.connect(closed.append,Qt.DirectConnection)
        with self.assertRaises(SignalError):gate.request(REVIEW,None,lambda:True,timeout=0)
        requests[0].answer(True)
        self.assertFalse(requests[0].accepted)
        self.assertEqual(closed,requests)
        self.assertIsNone(gate.pending)

    def test_cancel_pending_wakes_worker(self):
        gate=self.gate();ready=Event();result=[]
        gate.requested.connect(lambda request:ready.set(),Qt.DirectConnection)
        thread=Thread(target=lambda:result.append(gate.request(REVIEW,None,lambda:True)))
        thread.start()
        try:
            self.assertTrue(ready.wait(1));gate.cancel_pending();thread.join(1)
            self.assertFalse(thread.is_alive());self.assertEqual(result,[False])
        finally:gate.cancel_pending();thread.join(1)

    def test_presenter_failure_is_reported_and_does_not_wait(self):
        gate=self.gate()
        gate.requested.connect(lambda request:request.answer(False,'dialog unavailable'),Qt.DirectConnection)
        with self.assertRaisesRegex(SignalError,'dialog unavailable'):
            gate.request(REVIEW,None,lambda:True)
        self.assertIsNone(gate.pending)

    def test_service_sets_cancel_event_before_waking_confirmation(self):
        from types import SimpleNamespace
        from modules.capture_services.dependency_service import OpenWrtDependencyService
        cancel=Event()
        def stop():cancel.set();return True
        def wake():self.assertTrue(cancel.is_set())
        fake=SimpleNamespace(task=SimpleNamespace(session_id='current'),
                             owner=SimpleNamespace(task_controller=SimpleNamespace(cancel=stop)),
                             package_confirmation=SimpleNamespace(cancel_pending=wake))
        self.assertTrue(OpenWrtDependencyService.cancel(fake,'current'))

    def test_unavailable_ui_does_not_wait_or_install(self):
        gate=self.gate();gate.available=False
        with self.assertRaises(SignalError):gate.request(REVIEW,None,lambda:True)
        self.assertIsNone(gate.pending)

class CandidatePromptThreadTests(unittest.TestCase):
    def test_worker_requests_but_only_main_thread_builds_dialog(self):
        window=QWidget();window.show()
        gate=PackageConfirmationGate()
        presenter=DependencyCandidatePrompt(window,gate)
        result=[];errors=[];threads=[]
        dialog=Mock();dialog.exec.return_value=1
        def make_dialog(*args,**kwargs):threads.append(QThread.currentThread());return dialog
        def run():
            try:result.append(gate.request(REVIEW,None,lambda:True,timeout=2))
            except Exception as exc:errors.append(exc)
        try:
            with patch('modules.main_pages.packet_capture.openwrt.dependency_candidate_prompt.ConfirmDialog',side_effect=make_dialog) as factory:
                factory.Accepted=1
                thread=Thread(target=run);thread.start();deadline=time.monotonic()+3
                while thread.is_alive() and time.monotonic()<deadline:
                    APP.processEvents();thread.join(0.01)
                gate.cancel_pending();thread.join(1)
            self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(result,[True])
            self.assertEqual(threads,[APP.thread()]);dialog.deleteLater.assert_called_once()
        finally:
            presenter.dispose();window.hide();window.deleteLater();gate.deleteLater()
