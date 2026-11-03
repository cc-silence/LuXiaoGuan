"""Conflict state survives every display route and exports bounded native evidence."""
from dataclasses import replace
import json
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from tests.test_radio_usage import snapshot, runtime
from modules.capture_services.openwrt.radio_usage import parse_radio_usage, usage_for_phy, read_radio_usage
from modules.capture_services.openwrt.radio_usage_state import radio_usage_fields
from modules.capture_services.openwrt.models import OpenWrtRadioInfo
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.radio_usage_evidence import process_probe
from modules.log_history import HistoryMasker


class UsageFeedbackTests(unittest.TestCase):
    def fixture(self):
        journal=OperationDiagnostics()
        runner=NS(operation_diagnostics=journal,ssh_client=NS(connected=True,_client=object()))
        usage=parse_radio_usage(snapshot(),runtime("I|phy2|mon0|monitor|0|0\n"))["radio7"]
        runner.run=Mock(return_value=RemoteCommandResult("scene",0,
            "__RM_PROCESS_BEGIN__\nP|mon0|412|tcpdump\nS|0|0\n__RM_PROCESS_END__\n"))
        return runner,journal,usage

    def test_blocked_and_unknown_are_not_business_in_model_or_badge(self):
        from PySide6.QtWidgets import QApplication
        app=QApplication.instance() or QApplication([])
        from modules.main_pages.packet_capture.openwrt.auto_capture_radio_card import AutoCaptureRadioCard
        from modules.main_pages.packet_capture.openwrt.auto_capture_page import AutoCapturePage
        runner,journal,usage=self.fixture()
        card=AutoCaptureRadioCard(OpenWrtRadioInfo("radio7",free=False,**{
            k:v for k,v in radio_usage_fields(usage).items() if k!="free"}))
        self.assertIn("\u76d1\u542c\u63a5\u53e3\u51b2\u7a81",card.status_badge.text())
        card.apply_state("idle")
        self.assertNotIn("\u4e1a\u52a1",card.status_badge.text())
        page=NS(radio_cards={"radio7":card})
        AutoCapturePage._on_radio_idle_status_updated(page,{"radio7":None})
        self.assertIn("\u72b6\u6001\u5f85\u786e\u8ba4",card.status_badge.text())
        AutoCapturePage._on_radio_idle_status_updated(page,{"radio7":usage})
        self.assertEqual(card.radio_info.usage_state,"blocked")
        card.resize(1000,650);card.show();app.processEvents()
        self.assertFalse(card.grab().isNull())
        card.close()

    def test_backend_updates_even_when_free_boolean_stays_false(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        runner,journal,usage=self.fixture()
        backend=object.__new__(OpenWrtCaptureBackend)
        backend.snapshot=NS(device=NS())  # replace with the actual immutable model
        from modules.capture_services.openwrt.models import OpenWrtDeviceInfo,OpenWrtSessionSnapshot
        backend.snapshot=replace(OpenWrtSessionSnapshot.initial(),device=OpenWrtDeviceInfo(
            radios=(OpenWrtRadioInfo("radio7",free=False,usage_state="business"),)))
        after=backend.update_radio_idle_status({"radio7":usage})
        self.assertFalse(after.device.radios[0].free)
        self.assertEqual(after.device.radios[0].usage_state,"blocked")

    def test_capture_refusal_collects_readonly_scene_without_claiming_owner(self):
        runner,journal,usage=self.fixture()
        with self.assertRaises(RuntimeError):
            usage_for_phy({usage.radio:usage},usage.phy,runner=runner,cancel_event=threading.Event())
        scene=journal.snapshot()["radio_usage_failures"][-1]
        self.assertEqual(scene["interfaces"][0],dict(name="mon0",mode="monitor",up=False,linked=False,conflict=True))
        self.assertEqual(scene["processes"][0]["tool"],"tcpdump")
        self.assertEqual(scene["ownership"],"unconfirmed")
        command=runner.run.call_args.args[1]
        for mutation in ("kill ", "iw dev ", "uci ", "rm -"):
            self.assertNotIn(mutation,command)
        exported=sanitized_operation_evidence(journal.snapshot(),HistoryMasker())
        self.assertEqual(exported["radio_usage_failures"][-1]["processes"][0]["pid"],412)

    def test_feedback_zip_keeps_interface_and_process_evidence(self):
        import tempfile,zipfile
        from modules.log_services import export_logs
        from modules.display_privacy import masker_for
        from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
        from modules.capture_services.openwrt.diagnostic_context import router_context_json
        runner,journal,usage=self.fixture()
        with self.assertRaises(RuntimeError):
            usage_for_phy({usage.radio:usage},usage.phy,runner=runner)
        context=router_context_json(OpenWrtSessionSnapshot.initial(),masker_for([]),operation_evidence=journal.snapshot())
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"feedback.zip"
            export_logs(path,"capture",[],router_context=context)
            with zipfile.ZipFile(path) as archive:
                values=json.loads(archive.read("operation-diagnostics.json"))
                scene=values["radio_usage_failures"][-1]
                self.assertEqual(scene["interfaces"][0]["name"],"mon0")
                self.assertEqual(scene["processes"][0]["pid"],412)
                self.assertEqual(scene["ownership"],"unconfirmed")

    def test_timeout_and_malformed_process_scene_preserve_original_refusal(self):
        for result in (RemoteCommandResult("scene",0,"",timed_out=True),
                       RemoteCommandResult("scene",0,"__RM_PROCESS_BEGIN__\n__RM_PROCESS_END__\n"),
                       RemoteCommandResult("scene",0,"__RM_PROCESS_BEGIN__\nP|mon0|4|tcpdump --password=secret\nS|0|0\n__RM_PROCESS_END__\n")):
            runner,journal,usage=self.fixture();runner.run.return_value=result
            with self.assertRaises(RuntimeError):
                usage_for_phy({usage.radio:usage},usage.phy,runner=runner)
            scene=journal.snapshot()["radio_usage_failures"][-1]
            self.assertIn(scene["process_scan"],("timeout","failed"))
            self.assertEqual(scene["processes"],[])
            self.assertNotIn("secret",json.dumps(scene))

    def test_bounded_history_and_device_epoch_discard_stale_diagnostics(self):
        runner,journal,usage=self.fixture()
        for i in range(12):
            with self.assertRaises(RuntimeError):
                usage_for_phy({usage.radio:usage},usage.phy,runner=runner)
        self.assertEqual(len(journal.snapshot()["radio_usage_failures"]),8)
        old=journal.epoch;journal.bind_connection("different-device")
        stale=replace(usage,evidence_epoch=old)
        runner.run.reset_mock()
        with self.assertRaises(RuntimeError):
            usage_for_phy({stale.radio:stale},stale.phy,runner=runner)
        self.assertEqual(journal.snapshot()["radio_usage_failures"],[])
        runner.run.assert_not_called()

    def test_readonly_refresh_never_runs_process_diagnostics(self):
        runner,journal,usage=self.fixture()
        runner.run.return_value=RemoteCommandResult("radio_usage",0,
            runtime("I|phy2|mon0|monitor|0|0\n"))
        values=read_radio_usage(runner,snapshot=snapshot())
        self.assertEqual(values["radio7"].state,"blocked")
        self.assertEqual(runner.run.call_count,1)
        self.assertEqual(journal.snapshot()["radio_usage_failures"],[])

    def test_shared_batch_snapshot_collects_once_per_radio_not_per_target(self):
        runner,journal,usage=self.fixture()
        usage=replace(usage,probe_token="a"*32,evidence_epoch=journal.epoch,connection_id=id(runner.ssh_client._client))
        for _ in range(30):
            with self.assertRaises(RuntimeError):
                usage_for_phy({usage.radio:usage},usage.phy,runner=runner)
        self.assertEqual(runner.run.call_count,1)
        self.assertEqual(len(journal.snapshot()["radio_usage_failures"]),1)
        fresh=replace(usage,probe_token="b"*32)
        with self.assertRaises(RuntimeError):
            usage_for_phy({fresh.radio:fresh},fresh.phy,runner=runner)
        self.assertEqual(runner.run.call_count,2)

    def test_native_process_command_is_bounded_and_only_returns_metadata(self):
        command=process_probe(("mon0","hcxmon0"))
        self.assertIn("1024",command)
        self.assertIn("8193",command)
        self.assertIn('"$matches" -gt 64',command)
        self.assertNotIn('printf \'%s\\n\' "$args"\n',command)
        with self.assertRaises(ValueError):
            process_probe(("bad;command",))
