"""Cached badges never authorize or block fresh foreground capture admission."""
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch
from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QApplication
from modules.capture_services.openwrt.capture_start_preflight import CaptureStartPreflight
from modules.capture_services.openwrt.models import OpenWrtRadioInfo
from modules.capture_services.openwrt.radio_usage import RadioUsage


class StartPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def fixture(self, free=False):
        service = NS(runner=object(), ssh=NS(connected=True, _client=object(), host_fingerprint="same"))
        controller = CaptureStartPreflight(service)
        configs = [dict(radio_info=OpenWrtRadioInfo("radio7", interface="phy2", free=free),
                        channels="", allow_takeover=False)]
        self.addCleanup(controller.cancel)
        return controller, configs

    def finish(self, controller, configs, usages=None, error=None):
        with patch("modules.capture_services.openwrt.capture_start_preflight.read_radio_usage",
                   return_value=usages, side_effect=error) as read:
            self.assertTrue(controller.request(configs))
            controller._thread.join(2)
            self.assertFalse(controller._thread.is_alive())
        read.assert_called_once()
        return controller._request

    def test_cached_business_is_replaced_by_fresh_idle(self):
        controller, configs = self.fixture(free=False)
        result = self.finish(controller, configs, {"radio7": RadioUsage("radio7", "phy2", "idle", "disabled")})
        self.assertTrue(result.configs[0]["radio_info"].free)
        self.assertFalse(result.configs[0]["allow_takeover"])
        self.assertTrue(controller.is_current(result))
        self.assertFalse(configs[0]["radio_info"].free)

    def test_cached_idle_cannot_hide_current_business(self):
        controller, configs = self.fixture(free=True)
        result = self.finish(controller, configs, {"radio7": RadioUsage("radio7", "phy2", "business", "enabled")})
        self.assertFalse(result.configs[0]["radio_info"].free)
        self.assertFalse(result.configs[0]["allow_takeover"])

    def test_unknown_or_blocked_never_becomes_business_confirmation(self):
        for state in ("unknown", "blocked"):
            controller, configs = self.fixture()
            result = self.finish(controller, configs, {"radio7": RadioUsage("radio7", "phy2", state, "unverified")})
            self.assertEqual(result.error, "unverified")

    def test_preflight_conflict_keeps_existing_diagnostic_collection(self):
        controller, configs = self.fixture()
        usage = RadioUsage("radio7", "phy2", "blocked", "unverified")
        with patch("modules.capture_services.openwrt.capture_start_preflight.record_usage_conflicts") as collect:
            result = self.finish(controller, configs, {"radio7": usage})
        collect.assert_called_once_with(controller.service.runner, {"radio7": usage}, result.cancel)
        self.assertEqual(result.error, "unverified")

    def test_missing_radio_and_read_failure_do_not_fall_back_to_cache(self):
        controller, configs = self.fixture(free=True)
        self.assertTrue(self.finish(controller, configs, {}).error)
        self.assertIn("probe failed", self.finish(controller, configs, error=RuntimeError("probe failed")).error)

    def test_result_is_rejected_after_connection_change_or_cancel(self):
        controller, configs = self.fixture()
        result = self.finish(controller, configs, {"radio7": RadioUsage("radio7", "phy2", "idle", "disabled")})
        controller.service.ssh._client = object()
        self.assertFalse(controller.is_current(result))
        controller.service.ssh._client = result.connection
        controller.service.ssh.host_fingerprint = "different"
        self.assertFalse(controller.is_current(result))
        controller.service.ssh.host_fingerprint = result.fingerprint
        controller.cancel()
        self.assertFalse(controller.is_current(result))

    def test_repeated_click_does_not_spawn_parallel_queries(self):
        controller, configs = self.fixture()
        entered, release = threading.Event(), threading.Event()
        def query(*args):
            entered.set()
            release.wait(2)
            return {"radio7": RadioUsage("radio7", "phy2", "idle", "disabled")}
        with patch("modules.capture_services.openwrt.capture_start_preflight.read_radio_usage", side_effect=query) as read:
            try:
                self.assertTrue(controller.request(configs))
                self.assertTrue(entered.wait(1))
                self.assertFalse(controller.request(configs))
                controller.cancel()
            finally:
                release.set()
                controller._thread.join(2)
        read.assert_called_once()
        self.assertFalse(controller.is_current(controller._request))

    def test_completion_slot_runs_in_main_thread(self):
        controller, configs = self.fixture()
        class Receiver(QObject):
            @Slot(object)
            def receive(self, result):
                self.thread_id = threading.get_ident()
        receiver = Receiver()
        receiver.thread_id = None
        controller.finished.connect(receiver.receive)
        self.finish(controller, configs, {"radio7": RadioUsage("radio7", "phy2", "idle", "disabled")})
        self.app.processEvents()
        self.assertEqual(receiver.thread_id, threading.get_ident())


class StartPreflightPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def fixture(self, free):
        from test_auto_capture_feature import AutoCaptureFeatureTests
        from modules.capture_services.openwrt.models import OpenWrtConnectionState, OpenWrtDeviceInfo, OpenWrtSessionSnapshot
        from modules.main_pages.packet_capture.openwrt.auto_capture_page import AutoCapturePage
        owner = MagicMock()
        owner.settings = {}
        owner.openwrt_snapshot = OpenWrtSessionSnapshot(connection_state=OpenWrtConnectionState.CONNECTED,
            device=OpenWrtDeviceInfo(radios=(OpenWrtRadioInfo("radio7", interface="phy2", free=free),)))
        service = AutoCaptureFeatureTests.ui_service(self, owner)
        owner.auto_capture = service
        page = AutoCapturePage(owner, {})
        self.addCleanup(page.dispose)
        return page, service

    def test_cached_business_fresh_idle_starts_without_any_takeover_prompt(self):
        page, service = self.fixture(False)
        with patch("modules.capture_services.openwrt.capture_start_preflight.read_radio_usage",
                   return_value={"radio7": RadioUsage("radio7", "phy2", "idle", "disabled")}), \
             patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as prompt, \
             patch.object(service, "start_radio_capture") as start:
            page._on_start_all_clicked()
            page._start_preflight._thread.join(2)
            self.app.processEvents()
        prompt.assert_not_called()
        start.assert_called_once()
        self.assertTrue(start.call_args.args[0].free)
        self.assertFalse(start.call_args.args[2])
        self.assertTrue(page.radio_cards["radio7"].radio_info.free)

    def test_cached_idle_fresh_business_cancel_starts_nothing(self):
        page, service = self.fixture(True)
        with patch("modules.capture_services.openwrt.capture_start_preflight.read_radio_usage",
                   return_value={"radio7": RadioUsage("radio7", "phy2", "business", "enabled")}), \
             patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as prompt, \
             patch.object(service, "start_radio_capture") as start:
            prompt.Accepted = 1
            prompt.return_value.exec.return_value = 0
            page.radio_cards["radio7"]._on_start_clicked()
            page._start_preflight._thread.join(2)
            self.app.processEvents()
        prompt.assert_called_once()
        start.assert_not_called()

    def test_stop_or_page_dispose_rejects_queued_start_result(self):
        for action in ("_on_stop_all_clicked", "dispose"):
            page, service = self.fixture(True)
            with patch("modules.capture_services.openwrt.capture_start_preflight.read_radio_usage",
                       return_value={"radio7": RadioUsage("radio7", "phy2", "idle", "disabled")}), \
                 patch.object(service, "start_radio_capture") as start:
                page._on_start_all_clicked()
                page._start_preflight._thread.join(2)
                getattr(page, action)()
                self.app.processEvents()
            start.assert_not_called()
