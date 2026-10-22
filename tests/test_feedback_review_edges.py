"""Additional review regressions for startup, cancellation and unknown versions."""
import unittest
from unittest.mock import Mock
from threading import Event
from tests import test_feedback_regressions as feedback
from modules.capture_services.common_bundle_service import CommonBundleDownloadService
from modules.capture_services.openwrt.dependency_resolution import InstalledPackages
from modules.capture_services.openwrt.snapshot_bundle import select_profile
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment


class ReviewEdgeTests(unittest.TestCase):
    def test_offline_worker_start_failure_releases_ui_state(self):
        owner=feedback.Owner();owner._start_openwrt_worker=Mock(side_effect=RuntimeError("launch failure"))
        service=CommonBundleDownloadService(owner);messages=[]
        owner.settingsMessageGenerated.connect(lambda text,level:messages.append(text))
        self.assertFalse(service.refresh_local())
        self.assertFalse(service.active);self.assertEqual(service.phase,"idle")
        self.assertTrue(messages)

    def test_signal_worker_start_failure_releases_task(self):
        owner,service=feedback.SchedulingTests().create_service()
        owner._start_openwrt_worker=Mock(side_effect=RuntimeError("launch failure"))
        self.assertFalse(service.refresh())
        self.assertFalse(service.active);self.assertFalse(owner.task_controller.busy)

    def test_wireless_worker_start_failure_releases_task(self):
        owner,service=feedback.SchedulingTests().create_service()
        owner._start_openwrt_worker=Mock(side_effect=RuntimeError("launch failure"))
        self.assertFalse(service.setup.check())
        self.assertFalse(service.setup.active);self.assertFalse(owner.task_controller.busy)

    def test_explicit_operation_after_cancel_resumes_one_refresh(self):
        case=feedback.SchedulingTests();owner,service=case.create_service()
        service.setup.manager.inspect.return_value=WirelessEnvironment(data={"revision":"a"*192})
        service._auto_refresh_blocked=True
        self.assertTrue(service.setup.check())
        case.drain(owner)
        self.assertTrue(service.manager.read.called)
        self.assertFalse(service.snapshot.stale)

    def test_busy_update_check_reports_reason(self):
        owner=feedback.Owner();service=CommonBundleDownloadService(owner)
        owner.task_controller.begin("wireless_setup","busy")
        self.assertFalse(service.inspect())
        self.assertTrue(service.message)
        owner.task_controller.finish()

    def test_idle_cancel_does_not_suppress_future_work(self):
        owner,service=feedback.SchedulingTests().create_service()
        self.assertFalse(service.cancel())
        self.assertFalse(service._auto_refresh_blocked)
        self.assertFalse(service.setup.cancel())
        self.assertFalse(service._auto_refresh_blocked)

    def test_rejected_refresh_does_not_resume_cancelled_work(self):
        owner,service=feedback.SchedulingTests().create_service()
        service._auto_refresh_blocked=True
        owner.task_controller.begin("other","busy")
        self.assertFalse(service.refresh())
        self.assertTrue(service._auto_refresh_blocked)
        owner.task_controller.finish()

    def test_native_thread_start_exception_does_not_leave_worker_references(self):
        from unittest.mock import patch
        from modules.capture_services.task_completion import start_capture_task
        from PySide6.QtCore import QCoreApplication,QEvent,QThread
        owner,service=feedback.SchedulingTests().create_service()
        owner._openwrt_workers={}
        owner._cleanup_openwrt_worker=lambda session:owner._openwrt_workers.pop(session,None)
        owner._start_openwrt_worker=lambda *args:start_capture_task(owner,*args)
        with patch("modules.capture_services.task_completion.QThread.start",side_effect=RuntimeError("start failed")):
            self.assertFalse(service.refresh())
        self.assertFalse(owner._openwrt_workers)
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.assertFalse(owner.findChildren(QThread))

    def test_indented_offline_plan_still_rejects_extra_packages(self):
        from modules.capture_services.openwrt.local_ipk_transaction import verify_local_plan
        with self.assertRaises(ValueError):
            verify_local_plan("  Installing extra (1) to root...",{"tmux":"1"})

    def test_cancelled_signal_install_still_runs_bounded_cleanup(self):
        from pathlib import Path
        from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
        manager=RouterSignalManager(Mock());cancel=Event()
        manager._execute_stage=Mock(return_value="")
        manager._execute=Mock()
        def upload(*_):
            cancel.set()
        with self.assertRaises(SignalError):
            manager._install_ipk_paths([Path("lua_1_all.ipk")],cancel,uploader=upload)
        name,command,timeout,cleanup_cancel=manager._execute.call_args.args
        self.assertEqual(name,"router_signal_cleanup")
        self.assertEqual(timeout,30)
        self.assertFalse(cleanup_cancel.is_set())
        self.assertTrue(cancel.is_set())
        self.assertFalse(any(call.args[0]=="router_signal_offline_install" for call in manager._execute_stage.call_args_list))

    def test_unknown_libc_version_is_not_treated_as_confirmed(self):
        repo,device,_=feedback.SnapshotTests().repository()
        with self.assertRaises(ValueError):
            select_profile(repo,device,["tmux"],InstalledPackages({"libc":""}),None,Event(),lambda *_:None)


if __name__=="__main__":unittest.main()
