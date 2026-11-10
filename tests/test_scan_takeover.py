"""Single-channel scan takeover requires fresh evidence, consent and restoration."""
import unittest
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from tests.test_management_link import report, runner
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.management_link import ManagementLink
from modules.capture_services.openwrt.scan_channel_policy import single_channel_only, scan_takeover_needed
from modules.capture_services.scan_management_service import ScanManagementPlan
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
from modules.capture_services.openwrt.wifi_scanner import WifiScanResult
from modules.main_pages.packet_capture.scan_start_controller import ScanStartController
from modules.common_widgets.common_dialogs import ConfirmDialog
from tests.test_scan_link_ui import Probe

SINGLE = "Wiphy phy1\n\tvalid interface combinations:\n\t\t * #{ managed, AP } <= 4,\n\t\t   total <= 4, #channels <= 1\n\tOther capability:\n"
MULTI = SINGLE.replace("#channels <= 1", "#channels <= 2")


class ChannelPolicyTests(unittest.TestCase):
    def test_only_unambiguous_complete_single_channel_capability(self):
        mixed = SINGLE.replace("\tOther capability:", "\t\t * #{ managed } <= 1, total <= 1, #channels <= 2\n\tOther capability:")
        incomplete = SINGLE.replace("#channels <= 1", "unknown")
        missing_total = SINGLE.replace("total <= 4, ", "")
        missing_group = SINGLE.replace("} <= 4", "} broken")
        unexpected = SINGLE.replace("\tOther capability:", "\t\t * unsupported format\n\tOther capability:")
        for text, expected in [(SINGLE, True), (MULTI, False), (mixed, False),
                               (incomplete, None), (missing_total, None), (missing_group, None),
                               (unexpected, None), ("", None),
                               (SINGLE + SINGLE, None)]:
            with self.subTest(text=text):
                self.assertIs(single_channel_only(text), expected)

    def test_iw_617_global_and_radio_specific_output(self):
        global_one = SINGLE.replace("valid interface combinations:", "Globally valid interface combinations:")
        global_multi = global_one.replace("#channels <= 1", "#channels <= 2")
        radio_level = SINGLE.replace("\tvalid interface combinations:", "\t\t\tRadio's valid interface combinations:")
        for text, expected in [(global_one, True), (global_multi, False),
                               (radio_level, None), (SINGLE + radio_level, None),
                               (global_one + radio_level, None), (SINGLE + global_one, None),
                               (SINGLE + "\tSupported wiphy radios:\n\t\t* Idx 0:\n", None)]:
            with self.subTest(text=text):
                self.assertIs(single_channel_only(text), expected)

    def test_only_business_single_channel_is_eligible(self):
        for cap, state, expected in [(SINGLE, "business", True), (SINGLE, "idle", False),
                                     (MULTI, "business", False), ("", "business", False)]:
            with self.subTest(cap=cap, state=state):
                remote = NS(run=Mock(return_value=RemoteCommandResult("cap", 0, cap + "\n__RM_SCAN_CAP_END__\n")))
                with patch("modules.capture_services.openwrt.scan_channel_policy.read_radio_usage", return_value={}), \
                     patch("modules.capture_services.openwrt.scan_channel_policy.usage_for_phy", return_value=NS(state=state)):
                    self.assertEqual(scan_takeover_needed(remote, "phy1", Event()), expected)

    def test_ambiguous_multi_radio_never_authorizes_even_when_in_use(self):
        cap = SINGLE + "\tSupported wiphy radios:\n\t\t* Idx 0:\n"
        remote = NS(run=Mock(return_value=RemoteCommandResult("cap", 0, cap + "\n__RM_SCAN_CAP_END__\n")))
        with patch("modules.capture_services.openwrt.scan_channel_policy.read_radio_usage") as usage:
            self.assertFalse(scan_takeover_needed(remote, "phy1", Event()))
            usage.assert_not_called()

    def test_missing_completion_marker_never_authorizes(self):
        remote = NS(run=Mock(return_value=RemoteCommandResult("cap", 0, SINGLE)))
        with patch("modules.capture_services.openwrt.scan_channel_policy.read_radio_usage") as usage:
            self.assertFalse(scan_takeover_needed(remote, "phy1", Event()))
            usage.assert_not_called()

    def test_transport_failure_keeps_existing_scan_path(self):
        from paramiko.ssh_exception import SSHException
        remote = NS(run=Mock(side_effect=SSHException("transport")))
        self.assertFalse(scan_takeover_needed(remote, "phy1", Event()))

    def test_failed_or_cancelled_capability_never_authorizes_takeover(self):
        for result in [RemoteCommandResult("cap", 1), RemoteCommandResult("cap", -1, timed_out=True)]:
            self.assertFalse(scan_takeover_needed(NS(run=Mock(return_value=result)), "phy1", Event()))
        event = Event(); event.set()
        with self.assertRaises(InterruptedError):
            scan_takeover_needed(NS(run=Mock()), "phy1", event)


class ScanExecutionTests(unittest.TestCase):
    def make_backend(self):
        backend = OpenWrtCaptureBackend()
        backend.ssh_client = NS(connected=True, host_fingerprint="fingerprint")
        backend.command_runner = runner(report(port="eth0.1"))
        backend._ensure_remote_tmp = Mock()
        backend._find_scan_radio = Mock(return_value="phy0")
        backend.reconnect_cleanup.require_ready = Mock()
        backend.workspace_manager.create = Mock()
        backend.radio_session.create = Mock(return_value=NS(interface="wbmonfixture"))
        backend.wifi_scanner.scan = Mock(return_value=WifiScanResult((), "scanned"))
        backend._finish_capture_task = Mock()
        backend._maybe_takeover = Mock(return_value="recovery-record")
        return backend

    def options(self, band, consent=False):
        return dict(band=band, management_scan_phy="phy0", management_scan_fingerprint="fingerprint",
                    scan_single_channel_confirmed=consent)

    def test_both_bands_recheck_then_take_over_and_restore(self):
        for band in ("2.4G", "5G"):
            with self.subTest(band=band):
                backend = self.make_backend()
                with patch("modules.capture_services.openwrt.scan_channel_policy.scan_takeover_needed", return_value=True):
                    self.assertEqual(backend.scan_wifi("session", self.options(band, True)).csv_path, "scanned")
                backend.reconnect_cleanup.require_ready.assert_any_call()
                self.assertEqual(backend._maybe_takeover.call_args.kwargs["expected_band"], band)
                self.assertEqual(backend._finish_capture_task.call_args.args[2], "recovery-record")

    def test_normal_and_unknown_evidence_never_take_over(self):
        for band in ("2.4G", "5G"):
            backend = self.make_backend()
            with patch("modules.capture_services.openwrt.scan_channel_policy.scan_takeover_needed", return_value=False):
                backend.scan_wifi("session", self.options(band, True))
            backend._maybe_takeover.assert_not_called()
            self.assertIsNone(backend._finish_capture_task.call_args.args[2])

    def test_new_need_without_consent_stops_before_remote_workspace(self):
        backend = self.make_backend()
        with patch("modules.capture_services.openwrt.scan_channel_policy.scan_takeover_needed", return_value=True):
            with self.assertRaises(RuntimeError): backend.scan_wifi("session", self.options("5G"))
        backend.workspace_manager.create.assert_not_called()
        backend._maybe_takeover.assert_not_called()
        backend.radio_session.create.assert_not_called()

    def test_unreadable_recovery_blocks_mutation_not_ordinary_scan(self):
        backend = self.make_backend()
        def ready(*args, **kwargs):
            if not kwargs.get("allow_unavailable_password_record"):
                raise RuntimeError("unavailable")
        backend.reconnect_cleanup.require_ready.side_effect = ready
        with patch("modules.capture_services.openwrt.scan_channel_policy.scan_takeover_needed", return_value=True):
            with self.assertRaises(RuntimeError): backend.scan_wifi("session", self.options("5G", True))
        backend._maybe_takeover.assert_not_called()
        backend.workspace_manager.create.assert_not_called()

    def test_reconnect_recovers_token_backed_record_even_when_no_interfaces_existed(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from modules.capture_services.openwrt.session_journal import SessionJournal
        for token, expected in (("token", True), ("", False)):
            with self.subTest(token=bool(token)), TemporaryDirectory() as folder:
                backend = self.make_backend()
                sid = "a" * 32
                backend._finished_sessions.add(sid)
                folder = Path(folder)
                record = SessionJournal(session_id=sid, task_type="scan", remote_work_dir="/tmp/owned",
                                        radio="radio0", phy="phy0", config_token=token,
                                        device_fingerprint="fingerprint", interfaces=())
                path = folder / "takeover-journal.json"
                path.write_text(json.dumps(record.to_dict()), encoding="utf-8")
                backend._recover_single_takeover = Mock(return_value="not restored")
                with patch("modules.capture_services.backends.openwrt_backend.local_session_root", return_value=folder):
                    backend._recover_crashed_takeovers(Event())
                self.assertEqual(backend._recover_single_takeover.called, expected)
                self.assertTrue(path.exists())

    def test_running_scan_process_defers_wireless_restore(self):
        from pathlib import PurePosixPath
        backend = self.make_backend()
        backend.process_manager.handles = Mock(return_value=[NS(program="airodump-ng", remote_work_dir="/tmp/owned")])
        backend.radio_session.restore = Mock()
        backend.radio_takeover = NS(pending_record=None)
        workspace = NS(remote_dir=PurePosixPath("/tmp/owned"))
        errors = backend._restore_task_wireless(workspace, "recovery-record")
        self.assertTrue(errors)
        backend.radio_session.restore.assert_not_called()

    def test_failure_after_takeover_still_passes_record_to_cleanup(self):
        backend = self.make_backend()
        backend.radio_session.create.side_effect = RuntimeError("failure")
        with patch("modules.capture_services.openwrt.scan_channel_policy.scan_takeover_needed", return_value=True):
            with self.assertRaises(RuntimeError): backend.scan_wifi("session", self.options("5G", True))
        self.assertEqual(backend._finish_capture_task.call_args.args[2], "recovery-record")


class ScanTakeoverConfirmationTests(unittest.TestCase):
    def test_remember_is_scan_specific_and_only_saved_after_accept(self):
        for answer, remember, save_ok in (("yes", True, True), ("yes", False, True),
                                          ("no", True, True), ("yes", True, False)):
            with self.subTest(answer=answer, remember=remember, save_ok=save_ok):
                page = QWidget()
                page.info_panel = NS(append_info=Mock())
                page.settings = {"openwrt": {}}
                page._save_settings_with_feedback = Mock(return_value=save_ok)
                probe = Probe()
                page.capture_service = NS(scan_management=probe,
                    openwrt_backend=NS(ssh_client=NS(host_fingerprint="fingerprint")),
                    scan_openwrt_wifi_async=Mock(return_value=True))
                controller = ScanStartController(page)
                controller.start({"band": "5G"})
                plan = ScanManagementPlan("phy0", ManagementLink("wired", "eth0"), "fingerprint",
                                          scan_takeover_required=True)
                def choose(dialog):
                    self.assertIsNotNone(dialog.remember_option)
                    dialog.remember_option.setChecked(remember)
                    (dialog.yes_button if answer == "yes" else dialog.no_button).click()
                    return dialog.result()
                with patch.object(ConfirmDialog, "exec", choose):
                    probe.finished.emit("probe", plan, "", False)
                should_save = answer == "yes" and remember
                self.assertEqual(page._save_settings_with_feedback.called, should_save)
                self.assertEqual(page.settings["openwrt"].get("skip_scan_takeover_confirmation", False),
                                 should_save and save_ok)
                self.assertNotIn("skip_radio_takeover_confirmation", page.settings["openwrt"])
                self.assertEqual(page.capture_service.scan_openwrt_wifi_async.called,
                                 answer == "yes" and (not remember or save_ok))
                page.close(); page.deleteLater(); APP.processEvents()

    def test_remembered_choice_skips_only_scan_dialog_and_still_binds_plan(self):
        page = QWidget(); page.info_panel = NS(append_info=Mock()); probe = Probe()
        page.capture_service = NS(scan_management=probe,
            openwrt_backend=NS(ssh_client=NS(host_fingerprint="fingerprint")),
            scan_openwrt_wifi_async=Mock(return_value=True))
        controller = ScanStartController(page)
        controller.start({"band":"2.4G", "skip_scan_takeover_confirmation":True})
        plan = ScanManagementPlan("phy0", ManagementLink("wired", "eth0"), "fingerprint",
                                  scan_takeover_required=True)
        with patch.object(ConfirmDialog, "exec", side_effect=AssertionError("Unexpected dialog")):
            probe.finished.emit("probe", plan, "", False)
        options = page.capture_service.scan_openwrt_wifi_async.call_args.args[0]
        self.assertIs(options["scan_single_channel_confirmed"], True)
        self.assertEqual(options["management_scan_phy"], "phy0")
        page.close(); page.deleteLater(); APP.processEvents()

    def test_preference_survives_save_and_defaults_to_false(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from modules.config_services.capture_settings_manager import CaptureSettingsManager
        with TemporaryDirectory() as folder:
            manager = CaptureSettingsManager(Path(folder) / "settings.json")
            data = manager.load()
            self.assertFalse(data["openwrt"]["skip_scan_takeover_confirmation"])
            data["openwrt"]["skip_scan_takeover_confirmation"] = True
            manager.save(data)
            self.assertTrue(CaptureSettingsManager(manager.path).load()["openwrt"]["skip_scan_takeover_confirmation"])

    def test_cancel_and_accept_are_bound_to_current_scan(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                page = QWidget()
                page.info_panel = NS(append_info=Mock())
                probe = Probe()
                page.capture_service = NS(scan_management=probe,
                    openwrt_backend=NS(ssh_client=NS(host_fingerprint="fingerprint")),
                    scan_openwrt_wifi_async=Mock(return_value=True))
                controller = ScanStartController(page)
                controller.start({"band":"5G"})
                plan = ScanManagementPlan("phy0", ManagementLink("wired", "eth0"), "fingerprint",
                                          scan_takeover_required=True)
                with patch.object(ConfirmDialog, "exec", return_value=ConfirmDialog.Accepted if accepted else ConfirmDialog.Rejected):
                    probe.finished.emit("probe", plan, "", False)
                self.assertEqual(page.capture_service.scan_openwrt_wifi_async.called, accepted)
                if accepted:
                    options = page.capture_service.scan_openwrt_wifi_async.call_args.args[0]
                    self.assertIs(options["scan_single_channel_confirmed"], True)
                    self.assertEqual(options["management_scan_phy"], "phy0")
                page.close(); page.deleteLater(); APP.processEvents()


if __name__ == "__main__": unittest.main()
