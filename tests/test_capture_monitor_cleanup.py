"""Manual and forced recovery execute the shared POSIX implementation."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from pathlib import Path
import tempfile
from types import SimpleNamespace
from threading import Event
import unittest
from unittest.mock import Mock
from tests.test_monitor_residuals import Router, OWNER, SHELL
from modules.capture_services.openwrt.monitor_cleanup import inspect,cleanup
from modules.capture_services.openwrt.signal_manager import SignalError


@unittest.skipUnless(SHELL and Path(SHELL).is_file(),"POSIX shell unavailable")
class ManualMonitorTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.router=Router(Path(t.name))
        self.backend=SimpleNamespace(command_runner=self.router,radio_session=SimpleNamespace(record=None),
            ssh_client=SimpleNamespace(connected=True,host_fingerprint="device-A"))
        self.cancel=Event()

    def test_preview_is_read_only_and_cleanup_is_exact_selection(self):
        a=self.router.interface();b=self.router.interface('hcxmon0','')
        review=inspect(self.backend,self.cancel)
        self.assertTrue(a.exists() and b.exists())
        self.assertEqual(len(review['rows']),2)
        self.assertTrue(all(r['eligible'] for r in review['rows']))
        self.assertEqual(cleanup(self.backend,review,['hcxmon0'],self.cancel),dict(removed=['hcxmon0']))
        self.assertTrue(a.exists());self.assertFalse(b.exists())

    def test_busy_selection_requires_explicit_force(self):
        a=self.router.interface()
        (self.router.root/'proc/net/packet').write_text('sk RefCnt Type Proto Iface R Rmem User Inode\n0 3 3 0003 26 1 0 0 42\n')
        review=inspect(self.backend,self.cancel)
        self.assertFalse(review['rows'][0]['eligible'])
        self.assertTrue(review['rows'][0]['force_eligible'])
        with self.assertRaises(SignalError):cleanup(self.backend,review,[a.name],self.cancel)
        self.assertTrue(a.exists())
        self.assertEqual(cleanup(self.backend,review,[a.name],self.cancel,force=True)['removed'],[a.name])
        self.assertFalse(a.exists())

    def test_force_does_not_override_unknown_owner_or_business_type(self):
        self.router.interface(owner='foreign')
        review=inspect(self.backend,self.cancel)
        with self.assertRaises(SignalError):cleanup(self.backend,review,['wbmon12345678'],self.cancel,force=True)

    def test_replaced_identity_and_reconnected_device_are_refused(self):
        a=self.router.interface();review=inspect(self.backend,self.cancel)
        (a/'ifindex').write_text('27')
        with self.assertRaises(SignalError):cleanup(self.backend,review,[a.name],self.cancel,force=True)
        (a/'ifindex').write_text('26')
        self.backend.ssh_client=SimpleNamespace(connected=True,host_fingerprint='device-A')
        with self.assertRaises(SignalError):cleanup(self.backend,review,[a.name],self.cancel)
        self.assertTrue(a.exists())

    def test_reconnect_during_preview_invalidates_review(self):
        self.router.interface()
        self.backend.ssh_client._client=object()
        original=self.router.run
        def run(*args,**kwargs):
            result=original(*args,**kwargs)
            self.backend.ssh_client._client=object()
            return result
        self.router.run=run
        with self.assertRaises(SignalError):inspect(self.backend,self.cancel)

    def test_active_record_is_not_force_selectable(self):
        self.router.interface()
        self.backend.radio_session.record=SimpleNamespace(interface='wbmon12345678')
        review=inspect(self.backend,self.cancel)
        self.assertFalse(review['rows'][0]['force_eligible'])
        with self.assertRaises(SignalError):cleanup(self.backend,review,['wbmon12345678'],self.cancel,force=True)


class MonitorDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_force_toggle_only_unlocks_busy_capture_rows(self):
        from PySide6.QtCore import Qt
        from modules.main_pages.packet_capture.openwrt.capture_monitor_actions import MonitorReviewDialog
        rows=[dict(section=n,eligible=idle,force_eligible=force,source=source,zones=[],reasons=[] if idle else ['busy' if force else 'protected'])
              for n,idle,force,source in [('hcxmon0',True,False,'auto'),('wbmon12345678',False,True,'capture'),('wbmonunknown',False,False,'capture')]]
        dialog=MonitorReviewDialog(dict(rows=rows))
        self.addCleanup(dialog.deleteLater)
        item=dialog.tree.topLevelItem(1)
        self.assertFalse(item.flags() & Qt.ItemIsUserCheckable)
        dialog.force_box.setChecked(True)
        self.assertTrue(item.flags() & Qt.ItemIsUserCheckable)
        self.assertFalse(dialog.tree.topLevelItem(2).flags() & Qt.ItemIsUserCheckable)
        item.setCheckState(0,Qt.Checked)
        self.assertEqual(dialog.selected(),['wbmon12345678'])
        dialog.force_box.setChecked(False)
        self.assertEqual(dialog.selected(),[])
        self.assertFalse(dialog.clean_button.isEnabled())

    def test_service_refuses_manual_cleanup_during_active_capture(self):
        from modules.capture_services.wireless_setup_service import WirelessSetupService
        service=SimpleNamespace(owner=SimpleNamespace(is_busy=True),_start=Mock())
        self.assertFalse(WirelessSetupService.cleanup_capture_monitors(service,{},['hcxmon0'],force=True))
        service._start.assert_not_called()

    def test_auto_capture_start_entries_wait_for_cleanup_task(self):
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
        service=SimpleNamespace(owner=SimpleNamespace(task_controller=SimpleNamespace(busy=True)),
            messageGenerated=Mock(),get_or_create_session=Mock(),boot_manager=Mock())
        service._other_router_task_active=lambda: AutoCaptureService._other_router_task_active(service)
        AutoCaptureService.start_radio_capture(service,Mock())
        AutoCaptureService.start_boot_script(service)
        AutoCaptureService.install_and_start_boot_script(service)
        service.get_or_create_session.assert_not_called()
        service.boot_manager.start_script.assert_not_called()
        self.assertEqual(service.messageGenerated.emit.call_count,3)

    def test_inspection_entry_remains_available_during_capture(self):
        from tests.common_settings_fixture import create_page
        page,_=create_page(self)
        page.capture_service.is_busy=True
        page._sync()
        self.assertTrue(page.monitor_actions.button.isEnabled())
        page.monitor_actions.start()
        self.assertEqual(page.manager.submit.call_args.args,('capture_monitor_preview',))

    def test_running_capture_window_is_view_only_until_task_stops(self):
        from tests.common_settings_fixture import create_page
        from modules.main_pages.packet_capture.openwrt.capture_monitor_actions import MonitorReviewDialog
        from PySide6.QtCore import Qt
        page,_=create_page(self)
        page.capture_service.is_busy=True
        row=dict(section='hcxmon0',eligible=True,force_eligible=False,source='auto',zones=[],reasons=[])
        dialog=MonitorReviewDialog(dict(rows=[row]),page);self.addCleanup(dialog.deleteLater)
        dialog.tree.topLevelItem(0).setCheckState(0,Qt.Checked)
        dialog.force_box.setChecked(True)
        self.assertFalse(dialog.clean_button.isEnabled())
        page.capture_service.is_busy=False;page.capture_service.activityChanged.emit()
        self.assertTrue(dialog.clean_button.isEnabled())

    def test_readonly_queue_preserves_running_capture_task(self):
        from tests.test_router_policy_worker import Owner,wait_for
        from modules.capture_services.router_management_service import RouterManagementService
        from PySide6.QtTest import QSignalSpy
        from unittest.mock import patch
        ssh=SimpleNamespace(connected=True,_client=object(),host_fingerprint='device-A')
        owner=Owner(ssh,Mock());service=RouterManagementService(owner)
        owner.task_controller.begin('handshake','running-capture')
        before=owner.task_controller.snapshot()
        done=QSignalSpy(service.completed)
        try:
            with patch('modules.capture_services.openwrt.monitor_cleanup.inspect',return_value=dict(rows=[])):
                service.submit('capture_monitor_preview');wait_for(done,1)
            self.assertEqual(done.count(),1)
            self.assertEqual(owner.task_controller.snapshot(),before)
            self.assertFalse(owner._openwrt_workers)
        finally:owner.task_controller.finish();owner.deleteLater()
