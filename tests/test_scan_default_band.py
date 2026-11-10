"""Evidence and stale-result tests for the scan page default."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from threading import Event
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget, QComboBox
from tests.test_standalone import APP
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.management_link import ManagementLink
from modules.capture_services.openwrt.scan_default_band import management_frequency, choose_scan_band
from modules.main_pages.packet_capture.openwrt.scan_default_band_controller import ScanDefaultBandController


class Actions(QObject):
    actionRequested = Signal(str)


class Page(QWidget):
    settingsChanged = Signal()
    def __init__(self, service):
        super().__init__()
        self.service = service
        self.tools = Actions()
        self.tools.band_combo = QComboBox(self)
        self.tools.band_combo.addItems(["2.4G", "5G"])


class ScanBandTests(unittest.TestCase):
    def info(self, freq=5180, phy=1, name="wlan1"):
        return f"Interface {name}\n\twiphy {phy}\n\tchannel 36 ({freq} MHz), width: 80 MHz\n"

    def test_frequency_is_bound_to_exact_interface_and_phy(self):
        self.assertEqual(management_frequency(self.info(), "wlan1", "phy1"), "5G")
        self.assertEqual(management_frequency(self.info(2412), "wlan1", "phy1"), "2.4G")
        for text, interface, phy in [(self.info(), "wlan0", "phy1"),
                                     (self.info(), "wlan1", "phy0"),
                                     (self.info(5975), "wlan1", "phy1"),
                                     (self.info()+self.info(), "wlan1", "phy1"),
                                     ("", "wlan1", "phy1")]:
            self.assertEqual(management_frequency(text, interface, phy), "")

    def test_selection_uses_shared_live_radio_selector(self):
        runner = NS(run=Mock(return_value=RemoteCommandResult("info", 0, self.info())))
        link = ManagementLink("wireless", "wlan1", "phy1", "ssh_return_path")
        for target, expected in [("phy0", "2.4G"), ("phy1", ""), ("invalid", "")]:
            select = Mock(return_value=target)
            with self.subTest(target=target), patch("modules.capture_services.openwrt.scan_default_band.read_management_link", return_value=link):
                self.assertEqual(choose_scan_band(runner, select, Event()), expected)
                self.assertEqual(select.call_args.args[0], "2.4G")
        select = Mock()
        with patch("modules.capture_services.openwrt.scan_default_band.read_management_link", return_value=ManagementLink("wired", "eth0")):
            self.assertEqual(choose_scan_band(runner, select), "")
            select.assert_not_called()

    def test_link_or_frequency_change_during_selection_is_ignored(self):
        first = ManagementLink("wireless", "wlan1", "phy1", "ssh_return_path")
        moved = ManagementLink("wireless", "wlan0", "phy0", "ssh_return_path")
        runner = NS(run=Mock(return_value=RemoteCommandResult("info", 0, self.info())))
        select = Mock(return_value="phy0")
        with patch("modules.capture_services.openwrt.scan_default_band.read_management_link", side_effect=[first, moved]):
            self.assertEqual(choose_scan_band(runner, select), "")
        runner.run.side_effect = [RemoteCommandResult("info", 0, self.info()),
                                  RemoteCommandResult("info", 0, self.info(2412))]
        with patch("modules.capture_services.openwrt.scan_default_band.read_management_link", return_value=first):
            self.assertEqual(choose_scan_band(runner, select), "")

    def controller(self):
        client = object()
        ssh = NS(host_fingerprint="fp", _client=client, connected=True)
        backend = NS(ssh_client=ssh, command_runner=object())
        snap = NS(connection_state=NS(value="connected"), device=NS(radios=[]))
        service = NS(openwrt_backend=backend, openwrt_snapshot=snap, _shutting_down=False,
                     task_controller=NS(busy=False), _start_openwrt_worker=Mock(),
                     activityChanged=NS(emit=Mock()))
        page = Page(service)
        self.addCleanup(page.deleteLater)
        controller = ScanDefaultBandController(page)
        return controller, page, service

    def test_result_applies_only_to_current_connection_and_unmodified_choice(self):
        for change in ("none", "manual", "action", "busy", "reconnect", "cancel"):
            with self.subTest(change=change):
                controller, page, service = self.controller()
                controller.enter()
                _, _, _, success, _ = service._start_openwrt_worker.call_args.args
                if change == "manual": page.tools.band_combo.activated.emit(0)
                if change == "action": page.tools.actionRequested.emit("scan")
                if change == "busy": service.task_controller.busy = True
                if change == "reconnect": service.openwrt_backend.ssh_client._client = object()
                if change == "cancel": controller.cancel()
                success("5G")
                self.assertEqual(page.tools.band_combo.currentText(), "5G" if change == "none" else "2.4G")

    def test_disconnect_or_busy_never_starts_worker(self):
        controller, page, service = self.controller()
        service.task_controller.busy = True
        controller.enter()
        service._start_openwrt_worker.assert_not_called()
        service.task_controller.busy = False
        service.openwrt_snapshot.connection_state.value = "disconnected"
        controller.enter()
        service._start_openwrt_worker.assert_not_called()

    def test_reenter_cancels_old_probe_before_fresh_result(self):
        controller, page, service = self.controller()
        controller.enter()
        first = service._start_openwrt_worker.call_args.args
        controller.enter()
        self.assertTrue(first[2].is_set())
        first[3]("5G")
        self.assertEqual(page.tools.band_combo.currentText(), "2.4G")
        self.assertEqual(service._start_openwrt_worker.call_count, 2)
        service._start_openwrt_worker.call_args.args[3]("5G")
        self.assertEqual(page.tools.band_combo.currentText(), "5G")


class ScanBandWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_standalone
        test_standalone.StandaloneTests.setUpClass()
        cls.runtime = test_standalone.StandaloneTests.runtime

    @classmethod
    def tearDownClass(cls):
        from tests import test_standalone
        test_standalone.StandaloneTests.tearDownClass()

    def test_real_worker_handoff_and_cleanup(self):
        from dataclasses import replace
        from tests.test_desktop_shortcut import wait_until
        from modules.capture_services.openwrt.models import OpenWrtConnectionState
        owner = self.runtime.capture_service
        backend = owner.openwrt_backend
        old = backend.snapshot
        backend.snapshot = replace(old, connection_state=OpenWrtConnectionState.CONNECTED)
        page = Page(owner)
        controller = ScanDefaultBandController(page)
        ssh = NS(connected=True, host_fingerprint="fp", _client=object())
        try:
            with patch.object(backend, "ssh_client", ssh), patch(
                    "modules.main_pages.packet_capture.openwrt.scan_default_band_controller.choose_scan_band",
                    return_value="5G"):
                controller.enter()
                wait_until(lambda: page.tools.band_combo.currentText() == "5G", 5)
                wait_until(lambda: not owner._openwrt_workers, 5)
                self.assertIsNone(controller._event)
        finally:
            controller.cancel()
            backend.snapshot = old
            page.close()
            page.deleteLater()
            APP.processEvents()


if __name__ == "__main__": unittest.main()
