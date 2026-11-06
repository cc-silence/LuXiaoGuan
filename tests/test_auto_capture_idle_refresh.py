"""Event-driven post-cleanup reads publish only fresh main-thread evidence."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import threading
import unittest
from PySide6.QtCore import QObject, Signal
from tests.test_standalone import APP
from modules.capture_services.openwrt.auto_capture_idle_refresh import RadioIdleRefresh
from modules.capture_services.openwrt.models import OpenWrtRadioInfo

MODULE = 'modules.capture_services.openwrt.auto_capture_idle_refresh.'


class Owner(QObject):
    openwrtSnapshotChanged = Signal(object)


class Service(QObject):
    radioStateChanged = Signal(str, str)
    radioIdleStatusUpdated = Signal(dict)


def make_service():
    service = Service()
    service.owner = Owner()
    service.owner.openwrt_snapshot = NS(connection_state='connected', device=NS(radios=(OpenWrtRadioInfo(name='radio7'),)))
    service.owner.task_controller = NS(busy=False)
    service.owner.update_openwrt_radio_idle_status = Mock()
    service.ssh = NS(_client=object(), connected=True)
    service.runner = Mock()
    service._disposed = False
    service.is_busy = False
    service._idle_refresh = RadioIdleRefresh(service)
    return service


class IdleRefreshTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.jobs = []
        def thread(**kwargs):
            return NS(start=lambda:self.jobs.append(kwargs['target']))
        self.real_thread = threading.Thread
        self.patch = patch(MODULE+'threading.Thread', side_effect=thread)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.updates = []
        self.service.radioIdleStatusUpdated.connect(self.updates.append)

    def finish(self):
        self.jobs.pop(0)()
        APP.processEvents()

    def test_terminal_state_requests_read_and_preserves_actual_conflict(self):
        with patch(MODULE+'read_radio_usage', return_value={'radio7':False}) as read:
            self.service.radioStateChanged.emit('radio7','capturing')
            self.assertFalse(self.jobs)
            self.service.radioStateChanged.emit('radio7','idle')
            self.finish()
        self.assertEqual(self.updates,[{'radio7':False}])
        self.assertEqual(read.call_args.kwargs,{})

    def test_cleanup_during_old_read_discards_it_and_runs_one_fresh_read(self):
        with patch(MODULE+'read_radio_usage', side_effect=[{'radio7':False},{'radio7':True}]):
            self.service._idle_refresh.request()
            for _ in range(4):self.service.radioStateChanged.emit('radio7','idle')
            self.finish()
            self.assertEqual(self.updates,[])
            self.assertEqual(len(self.jobs),1)
            self.finish()
        self.assertEqual(self.updates,[{'radio7':True}])
        self.assertFalse(self.service._is_probing_idle)

    def test_waits_for_other_capture_and_foreground_task_completion(self):
        with patch(MODULE+'read_radio_usage', return_value={'radio7':True}):
            self.service.is_busy=True
            self.service.radioStateChanged.emit('radio7','idle')
            self.assertFalse(self.jobs)
            self.service.is_busy=False
            self.service.owner.task_controller.busy=True
            self.service.radioStateChanged.emit('radio7','error')
            self.assertFalse(self.jobs)
            self.service.owner.task_controller.busy=False
            self.service.owner.openwrtSnapshotChanged.emit(self.service.owner.openwrt_snapshot)
            self.finish()
        self.assertEqual(self.updates,[{'radio7':True}])

    def test_foreground_task_start_and_finish_invalidates_inflight_read(self):
        with patch(MODULE+'read_radio_usage', side_effect=[{'radio7':True},{'radio7':False}]):
            self.service._idle_refresh.request()
            self.service.owner.task_controller.busy=True
            self.service.owner.openwrtSnapshotChanged.emit(self.service.owner.openwrt_snapshot)
            self.service.owner.task_controller.busy=False
            self.service.owner.openwrtSnapshotChanged.emit(self.service.owner.openwrt_snapshot)
            self.finish()
            self.assertEqual(self.updates,[])
            self.assertEqual(len(self.jobs),1)
            self.finish()
        self.assertEqual(self.updates,[{'radio7':False}])

    def test_failed_read_publishes_unknown_and_does_not_assert_idle(self):
        with patch(MODULE+'read_radio_usage', side_effect=RuntimeError('probe failed')):
            self.service.radioStateChanged.emit('radio7','idle')
            self.finish()
        self.assertEqual(self.updates,[{'radio7':None}])

    def test_disposed_cancelled_and_replaced_connection_results_are_discarded(self):
        for condition in ('disposed','cancelled','replaced'):
            self.service=make_service();updates=[]
            self.service.radioIdleStatusUpdated.connect(updates.append)
            with patch(MODULE+'read_radio_usage', return_value={'radio7':True}):
                self.service._idle_refresh.request()
                if condition=='disposed':self.service._disposed=True
                elif condition=='cancelled':self.service._idle_probe_cancel.set()
                else:self.service.ssh._client=object()
                self.finish()
            self.assertEqual(updates,[])

    def test_queued_result_is_discarded_if_task_starts_before_delivery(self):
        with patch(MODULE+'read_radio_usage', return_value={'radio7':True}):
            self.service._idle_refresh.request()
            self.jobs.pop(0)()
            self.service.owner.task_controller.busy=True
            self.service.owner.openwrtSnapshotChanged.emit(self.service.owner.openwrt_snapshot)
            APP.processEvents()
            self.assertEqual(self.updates,[])
            self.assertFalse(self.jobs)
            self.service.owner.task_controller.busy=False
            self.service.owner.openwrtSnapshotChanged.emit(self.service.owner.openwrt_snapshot)
            self.finish()
        self.assertEqual(self.updates,[{'radio7':True}])

    def test_queued_result_cannot_publish_after_disposal(self):
        with patch(MODULE+'read_radio_usage', return_value={'radio7':True}):
            self.service._idle_refresh.request()
            self.jobs.pop(0)()
            self.service._disposed=True
            APP.processEvents()
        self.assertEqual(self.updates,[])
        self.assertFalse(self.service._is_probing_idle)

    def test_worker_result_updates_owner_only_on_main_thread(self):
        main=threading.get_ident();threads=[]
        self.service.owner.update_openwrt_radio_idle_status.side_effect=lambda _:threads.append(threading.get_ident())
        with patch(MODULE+'read_radio_usage', return_value={'radio7':True}):
            self.service._idle_refresh.request()
            job=self.jobs.pop()
            worker=self.real_thread(target=job)
            worker.start();worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(threads,[])
            APP.processEvents()
        self.assertEqual(threads,[main])
