"""Shared radio preflight must never claim the handshake action button."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest
from PySide6.QtCore import QObject,Signal
from tests.test_file_selection import APP
from modules.capture_services.task_controller import CaptureTaskController
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.openwrt.models import AccessPointInfo,OpenWrtSessionSnapshot,OpenWrtConnectionState,CaptureTaskType
from modules.main_pages.packet_capture.openwrt.capture_tool_panel import CaptureToolPanel


class Service(QObject):
    openwrtSnapshotChanged=Signal(object)
    takeoverProbeFinished=Signal(str,object,str,bool)
    def __init__(self):
        super().__init__()
        self._shutting_down=False
        self._set_state=Mock()
        self.openwrt_backend=NS(snapshot=OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            access_points=(AccessPointInfo('02:11:22:33:44:55',ssid='fixture',channel=1,selected=True),)))
        self.task_controller=CaptureTaskController(self)
        self.task_controller.stateChanged.connect(lambda *args:CaptureService._on_task_state_changed(self,*args))
        self._start_openwrt_worker=Mock()


class PreflightIdentityTests(unittest.TestCase):
    def test_success_and_cancel_do_not_claim_handshake_button(self):
        for cancelled in (False,True):
            service=Service();panel=CaptureToolPanel({})
            seen=[]
            def observe(snapshot):
                panel.set_action_availability(True,service.task_controller.busy,
                    dict(scan=True,handshake=True,wps=True,password=True),
                    snapshot.task_type.value,snapshot.task_state,wps_entry=snapshot.wps_entry)
                seen.append(panel.handshake_button.property('captureAction'))
            service.openwrtSnapshotChanged.connect(observe)
            probe=CaptureService.probe_openwrt_takeover_async(service,{},('02:11:22:33:44:55',))
            self.assertTrue(probe)
            self.assertEqual(service.openwrt_backend.snapshot.task_type,CaptureTaskType.DEVICE_PROBE)
            self.assertTrue(service.task_controller.busy)
            self.assertFalse(panel.handshake_button.isEnabled())
            if cancelled:service.task_controller.cancel()
            CaptureService._on_takeover_probe_succeeded(service,probe,(),cancelled)
            self.assertTrue(all(action=='handshake' for action in seen))
            self.assertFalse(service.task_controller.busy)
            for entry in ('quick','advanced'):
                panel.set_action_availability(True,True,dict(wps=True),'wps','capturing',wps_entry=entry)
                self.assertEqual(panel.handshake_button.property('captureAction'),'handshake')
                active=panel.wps_button if entry=='quick' else panel.advanced_wps_button
                other=panel.advanced_wps_button if entry=='quick' else panel.wps_button
                self.assertEqual(active.property('captureAction'),'stop')
                self.assertNotEqual(other.property('captureAction'),'stop')
            panel.close();panel.deleteLater();service.deleteLater();APP.processEvents()


if __name__=='__main__':
    unittest.main()
