"""Device evidence survives disconnect, stays bounded and remains redacted in ZIPs."""
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot,OpenWrtConnectionState,OpenWrtDeviceInfo,OpenWrtRadioInfo
from modules.capture_services.openwrt.diagnostic_history import RouterDiagnosticHistory
from modules.capture_services.openwrt.diagnostic_context import router_context_json
from modules.display_privacy import masker_for
from modules.log_services import export_logs

class DiagnosticHistoryTests(unittest.TestCase):
    def connected(self,model="Router A",fingerprint="device-A"):
        return replace(OpenWrtSessionSnapshot.initial(),connection_state=OpenWrtConnectionState.CONNECTED,
            host_fingerprint=fingerprint,device=OpenWrtDeviceInfo(model=model,board_name="board-a",version="23.05.4",
                hostname="hostname-secret",radios=(OpenWrtRadioInfo("radio1",bands=("5G",),interfaces=("phy1-ap0",)),)))

    def test_disconnected_zip_keeps_last_device_and_marks_it_historical(self):
        history=RouterDiagnosticHistory()
        history.observe(self.connected("TP-Link TL-XDR6088"))
        timestamp=history.latest.observed_at
        history.observe(OpenWrtSessionSnapshot.initial())
        masker=masker_for([])
        context=router_context_json(OpenWrtSessionSnapshot.initial(),masker,device_history=history)
        with tempfile.TemporaryDirectory() as folder:
            for category in ("signal","capture","files","router-terminal"):
                output=Path(folder)/(category+".zip")
                export_logs(output,category,[],router_context=context,masker=masker)
                with zipfile.ZipFile(output) as archive:
                    data=json.loads(archive.read("router-info.json"))
                    self.assertEqual(data["device"]["model"],"TP-Link TL-XDR6088")
                    self.assertEqual(data["connection_state"],"disconnected")
                    self.assertEqual(data["device_information_source"],"last_successful_connection")
                    self.assertEqual(data["device_observed_at"],timestamp)
                    self.assertNotIn("hostname-secret",archive.read("router-info.json").decode())
                    self.assertIsNone(archive.testzip())

    def test_new_device_replaces_previous_history_and_never_retains_task_data(self):
        history=RouterDiagnosticHistory();a=self.connected()
        history.observe(a)
        self.assertFalse(hasattr(history.latest.snapshot,"access_points"))
        self.assertFalse(hasattr(history.latest.snapshot,"host_fingerprint"))
        self.assertFalse(hasattr(history.latest.snapshot,"last_error"))
        before=history.latest
        history.observe(a);self.assertIs(history.latest,before)
        history.observe(self.connected("Router B","device-B"))
        data=json.loads(router_context_json(OpenWrtSessionSnapshot.initial(),masker_for([]),device_history=history))
        self.assertEqual(data["device"]["model"],"Router B")
        self.assertNotIn("Router A",json.dumps(data))

    def test_no_successful_connection_does_not_invent_device(self):
        history=RouterDiagnosticHistory();history.observe(OpenWrtSessionSnapshot.initial())
        data=json.loads(router_context_json(OpenWrtSessionSnapshot.initial(),masker_for([]),device_history=history))
        self.assertIsNone(data["device"])
        self.assertIsNone(history.latest)

    def test_current_device_overrides_history_and_export_still_masks_sensitive_values(self):
        history=RouterDiagnosticHistory();history.observe(self.connected("Old"))
        current=self.connected("New","device-B")
        data=json.loads(router_context_json(current,masker_for(["New"]),device_history=history))
        self.assertEqual(data["device_information_source"],"current_connection")
        self.assertNotIn("New",json.dumps(data))

class DiagnosticHistoryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_standalone as standalone
        standalone.StandaloneTests.setUpClass()
        cls.runtime=standalone.StandaloneTests.runtime
        cls.window=standalone.StandaloneTests.window
    @classmethod
    def tearDownClass(cls):
        from tests import test_standalone as standalone
        standalone.StandaloneTests.tearDownClass()

    def test_all_information_panels_use_service_history_after_disconnect(self):
        service=self.runtime.capture_service
        device=DiagnosticHistoryTests().connected("Feedback Router")
        service.diagnostic_history.observe(device)
        page=self.window.packet_capture_page
        for panel in page.info_panels:
            context=json.loads(panel._export_context_provider(masker_for([])))
            self.assertEqual(context["device"]["model"],"Feedback Router")
            self.assertEqual(context["device_information_source"],"last_successful_connection")
            self.assertEqual(context["connection_state"],"disconnected")
