"""Qt action-state contracts, adaptive geometry and retry after UCI conflicts."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from tests.test_standalone import APP
from tests.test_wireless_ui import environment
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget
from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory, SignalSnapshot
from modules.capture_services.openwrt.wireless_setup_models import WirelessState, WirelessTaskResult
from modules.capture_services.wireless_setup_service import WirelessSetupService
from modules.main_pages.packet_capture.openwrt.router_signal_page import RouterSignalPage
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import RelaySetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_relay, relay_request
from tests.test_wireless_setup import TARGET
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.ui_scale_manager import UiScaleManager


class Service(QObject):
    snapshotChanged = Signal(object)
    reconnectRequired = Signal(str)
    messageGenerated = Signal(str, str)
    messages = ()
    active = False
    details_enabled = False
    dependencies_missing = False

    def __init__(self):
        super().__init__()
        rows = tuple(RouterSignal("signal"+str(i), "Home", "ap", "radio"+str(i), band,
                                 False, False, "active") for i, band in enumerate(("2g", "5g", "5g")))
        self.snapshot = SignalSnapshot(SignalInventory(rows, "a"*64), connected=True, stale=False)
        self.refresh = Mock(return_value=True)
        self.cancel = Mock(return_value=True)


class PendingSetup(QObject):
    snapshotChanged = Signal(object)
    completed = Signal(object)

    def __init__(self):
        super().__init__()
        self.snapshot = WirelessState(pending_changes=True)
        self.active = False
        self.identity = object()
        self.enable = Mock()
        self.review_pending = Mock(return_value=True)
        self.commit_pending = Mock(return_value=True)

    def _connection_identity(self):
        return self.identity


class SignalActionTests(unittest.TestCase):
    def create_page(self, light=False, setup=None):
        parent = QWidget()
        parent.scale_manager = UiScaleManager(100)
        parent.light_theme = light
        from modules.config_manager import ConfigManager
        from modules.dialog_theme import build_base_stylesheet
        parent.setStyleSheet(build_base_stylesheet(ConfigManager(), light_theme=light, scale_manager=parent.scale_manager))
        service = Service()
        if setup is not None:
            service.setup = setup
        page = RouterSignalPage(service, None, parent)
        page.setStyleSheet(CapturePageStyleManager(parent)._capture_stylesheet(light))
        page.resize(1100, 680)
        parent.resize(1200, 1000)
        parent.show()
        page.show()
        APP.processEvents()
        self.addCleanup(parent.deleteLater)
        self.addCleanup(page.dispose)
        return page, service

    def test_refresh_turns_into_cancel_only_for_own_task(self):
        page, service = self.create_page()
        self.assertTrue(page.inventory_toolbar.isVisible())
        page.stack.setCurrentIndex(2)
        APP.processEvents()
        self.assertFalse(page.inventory_toolbar.isVisible())
        page.stack.setCurrentIndex(0)
        APP.processEvents()
        self.assertTrue(page.inventory_toolbar.isVisible())
        page.refresh_button.click()
        service.refresh.assert_called_once()
        service.active = True
        service.snapshot = replace(service.snapshot, busy=True)
        page.apply_snapshot(service.snapshot)
        self.assertTrue(page.refresh_button.isEnabled())
        page.refresh_button.click()
        service.cancel.assert_called_once()
        self.assertEqual(service.refresh.call_count, 1)
        service.active = False
        page.apply_snapshot(service.snapshot)
        self.assertFalse(page.refresh_button.isEnabled())
        page.refresh_button.click()
        self.assertEqual(service.cancel.call_count, 1)

    def test_connection_notices_follow_login_and_disconnect_on_both_setup_pages(self):
        for light in (False, True):
            page, service = self.create_page(light)
            panels = page.setup_panels
            original = tuple(id(label) for label in panels.connection_hints)
            for connected in (False, True, False, True):
                service.snapshot = replace(service.snapshot, connected=connected, stale=True)
                service.snapshotChanged.emit(service.snapshot)
                for index, notice in zip((1, 2), panels.connection_hints):
                    page.stack.setCurrentIndex(index)
                    APP.processEvents()
                    self.assertEqual(notice.isVisible(), not connected)
                    self.assertIn("\u91cd\u65b0\u767b\u5f55", notice.text())
                self.assertEqual(tuple(id(label) for label in panels.connection_hints), original)
                if not connected:
                    self.assertFalse(panels.scan_buttons)
                    self.assertFalse(panels.radio_buttons)
                    self.assertFalse(panels.scan_seconds.isEnabled())
                    self.assertFalse(panels.radio_hint.isVisible())
                    self.assertFalse(panels.environment_hint.isVisible())
                    self.assertFalse(panels.empty_hint.isVisible())
                    output = Path("build/signal-disconnected-preview")
                    output.mkdir(exist_ok=True)
                    for index, name in ((1, "transmit"), (2, "relay")):
                        page.stack.setCurrentIndex(index)
                        APP.processEvents()
                        page.grab().save(str(output / (name + ("-light.png" if light else "-dark.png"))))
            self.assertFalse(panels.setup and panels.setup.active)

    def test_scan_is_available_with_stale_inventory_and_shows_cancel(self):
        page, service = self.create_page()
        panels = page.setup_panels
        setup = SimpleNamespace(active=False, snapshot=WirelessState(), scan=Mock(return_value=True), cancel=Mock())
        panels.setup = setup
        self.addCleanup(lambda: setattr(panels, "setup", None))
        service.snapshot = replace(service.snapshot, stale=True)
        panels.apply_snapshot(service.snapshot)
        self.assertTrue(panels.scan_buttons["radio0"].isEnabled())
        panels.scan_buttons["radio0"].click()
        setup.scan.assert_called_once_with(10, "radio0")
        setup.active = True
        setup.snapshot = WirelessState(busy=True, action="scan", detail="Scanning", scan_radio="radio0")
        service.snapshot = replace(service.snapshot, busy=True)
        panels._setup_changed(setup.snapshot)
        self.assertTrue(panels.scan_buttons["radio0"].isEnabled())
        self.assertTrue(all(not button.isEnabled() for name, button in panels.scan_buttons.items() if name != "radio0"))
        self.assertTrue(panels.empty_hint.isVisibleTo(panels.relay_page))
        panels.scan_buttons["radio0"].click()
        setup.cancel.assert_called_once()
        self.assertEqual(setup.scan.call_count, 1)

    def test_scan_buttons_follow_detected_radios_and_keep_identity(self):
        page, service = self.create_page()
        panels = page.setup_panels
        env = replace(environment(), data={"radios": [
            {"name": "radio0", "band": "2g", "interfaces": ["phy0-ap0"], "runtime_up": True},
            {"name": "radio1", "band": "5g", "interfaces": [], "runtime_up": False},
            {"name": "radio2", "band": "5g", "interfaces": ["phy2-ap0"], "runtime_up": True}]})
        setup = SimpleNamespace(active=False, snapshot=WirelessState(environment=env), scan=Mock(return_value=True), cancel=Mock())
        panels.setup = setup
        self.addCleanup(lambda: setattr(panels, "setup", None))
        panels._setup_changed(setup.snapshot)
        original = dict(panels.scan_buttons)
        self.assertEqual(set(original), {"radio0", "radio1", "radio2"})
        self.assertTrue(original["radio0"].isEnabled())
        self.assertTrue(original["radio1"].isEnabled())
        self.assertTrue(original["radio2"].isEnabled())
        original["radio2"].click()
        setup.scan.assert_called_once_with(10, "radio2")
        setup.active = True
        setup.snapshot = replace(setup.snapshot, action="scan", busy=True, scan_radio="radio2")
        service.snapshot = replace(service.snapshot, busy=True)
        panels._setup_changed(setup.snapshot)
        self.assertEqual(panels.scan_buttons, original)
        self.assertTrue(original["radio2"].isEnabled())
        self.assertFalse(original["radio0"].isEnabled())
        self.assertIn("radio2", original["radio2"].text())
        original["radio2"].click()
        setup.cancel.assert_called_once()
        panels.apply_snapshot(replace(service.snapshot, connected=False))
        self.assertFalse(panels.scan_buttons)

    def test_table_and_three_radio_buttons_follow_available_space(self):
        for light in (False, True):
            page, service = self.create_page(light)
            panels = page.setup_panels
            page.stack.setCurrentIndex(1)
            APP.processEvents()
            self.assertGreaterEqual(page.navigation_buttons[0].x(), UiScaleManager.for_widget(page).px(12))
            self.assertGreaterEqual(page.navigation_buttons[0].y(), UiScaleManager.for_widget(page).px(8))
            buttons = list(panels.radio_buttons.values())
            self.assertEqual(len(buttons), 3)
            self.assertFalse(page.inventory_toolbar.isVisible())
            for button in buttons:
                self.assertTrue(button.property("signalPrimaryAction"))
                self.assertGreaterEqual(button.height(), UiScaleManager.for_widget(page).px(48))
            self.assertLessEqual(max(b.width() for b in buttons)-min(b.width() for b in buttons), 1)
            self.assertGreater(sum(b.width() for b in buttons), panels.radios_area.width()-25)
            page.stack.setCurrentIndex(2)
            APP.processEvents()
            before = panels.table.height()
            page.resize(1100, 900)
            APP.processEvents()
            self.assertGreater(panels.table.height(), before+150)
            scan_buttons = list(panels.scan_buttons.values())
            self.assertEqual(len(scan_buttons), 3)
            self.assertLessEqual(max(b.width() for b in scan_buttons)-min(b.width() for b in scan_buttons), 1)
            for button in scan_buttons:
                self.assertGreater(button.x(), panels.scan_seconds.x())
                self.assertGreaterEqual(button.height(), UiScaleManager.for_widget(page).px(48))
            self.assertFalse(page.inventory_toolbar.isVisible())
            self.assertTrue(all(b.property("signalPrimaryAction") for b in scan_buttons))
            output = Path(__file__).parent / "signal-actions-preview"
            output.mkdir(exist_ok=True)
            for button in scan_buttons:
                button.setEnabled(True)
            page.grab().save(str(output / ("relay-light.png" if light else "relay-dark.png")))
            page.stack.setCurrentIndex(1)
            APP.processEvents()
            page.grab().save(str(output / ("radios-light.png" if light else "radios-dark.png")))

    def test_default_identity_is_stable_and_wds_omits_hostname(self):
        for light in (False, True):
            parent = QWidget()
            parent.setProperty("settingsLightTheme", light)
            parent.setStyleSheet("QWidget { background: #f5f6fa; }" if light else "QWidget { background: #181b1f; }")
            dialog = RelaySetupDialog("Upstream", parent, radio_name="radio7", password="test-password")
            configure_relay(dialog, environment())
            fields = dialog.identity_fields
            first = relay_request(dialog, TARGET)
            self.assertEqual(first.options["identity_action"], "set")
            self.assertEqual(fields.mode.currentData(), "profile")
            self.assertEqual(int(first.options["macaddr"][:2], 16) & 3, 2)
            self.assertEqual(first.options["hostname"], fields.profile.currentData())
            self.assertIn("hostname", first.options)
            self.assertEqual(relay_request(dialog, TARGET).options["macaddr"], first.options["macaddr"])
            fields.mode.setCurrentIndex(fields.mode.findData("profile"))
            self.assertIn("hostname", relay_request(dialog, TARGET).options)
            dialog.bridge_mode.setCurrentIndex(dialog.bridge_mode.findData("wds"))
            self.assertNotIn("hostname", relay_request(dialog, TARGET).options)
            dialog.other_section.set_expanded(True, animate=False)
            dialog.other_tabs.buttons[3].click()
            dialog.show()
            dialog.resize(780, 820)
            APP.processEvents()
            output = Path(__file__).parent / "signal-actions-preview"
            output.mkdir(exist_ok=True)
            dialog.grab().save(str(output / ("identity-light.png" if light else "identity-dark.png")))
            dialog.reject()
            parent.deleteLater()

    def test_pending_review_confirmation_cancellation_and_connection_binding(self):
        from modules.common_widgets.common_dialogs import ConfirmDialog
        setup = PendingSetup()
        page, _service = self.create_page(setup=setup)
        controller = page.pending_actions
        review = dict(token="review", rows=[dict(config="wireless", section="home", option="ssid", value="New AP")])
        self.assertTrue(controller.button.isVisible())
        self.assertTrue(controller.button.property("signalPrimaryAction"))
        self.assertTrue(controller.button._breathing)
        controller.button.click()
        self.assertFalse(controller.button._breathing)
        setup.review_pending.assert_called_once()
        with patch("modules.main_pages.packet_capture.openwrt.signal_pending_actions.ConfirmDialog") as dialog:
            dialog.Accepted = ConfirmDialog.Accepted
            dialog.return_value.exec.return_value = ConfirmDialog.Rejected
            setup.completed.emit(WirelessTaskResult("pending_review", review))
        setup.commit_pending.assert_not_called()
        controller.button.click()
        with patch("modules.main_pages.packet_capture.openwrt.signal_pending_actions.ConfirmDialog") as dialog:
            dialog.Accepted = ConfirmDialog.Accepted
            dialog.return_value.exec.return_value = ConfirmDialog.Accepted
            setup.completed.emit(WirelessTaskResult("pending_review", review))
        setup.commit_pending.assert_called_once_with("review")
        setup.commit_pending.reset_mock()
        controller.button.click()
        with patch("modules.main_pages.packet_capture.openwrt.signal_pending_actions.ConfirmDialog") as dialog:
            dialog.Accepted = ConfirmDialog.Accepted
            def changed():
                setup.identity = object()
                return ConfirmDialog.Accepted
            dialog.return_value.exec.side_effect = changed
            setup.completed.emit(WirelessTaskResult("pending_review", review))
        setup.commit_pending.assert_not_called()

    def test_successful_inventory_refresh_retries_failed_environment_without_relogin(self):
        context = SimpleNamespace(active=False, _enabled=True, _pending=False, signals=SimpleNamespace(_auto_refresh_blocked=False), snapshot=WirelessState(environment=None),
                                  snapshotChanged=Mock(), _checkRequested=Mock())
        snapshot = SignalSnapshot(SignalInventory(revision="a"*64), connected=True, stale=False)
        WirelessSetupService._inventory_changed(context, snapshot)
        self.assertTrue(context._pending)
        context._checkRequested.emit.assert_called_once()
        context.snapshot = WirelessState(environment=replace(environment(), data=dict(environment().data, revision="a"*192)))
        WirelessSetupService._inventory_changed(context, snapshot)
        context._checkRequested.emit.assert_called_once()

    def test_pending_review_disconnect_releases_waiting_state(self):
        setup = PendingSetup()
        page, service = self.create_page(setup=setup)
        page.pending_actions.button.click()
        self.assertTrue(page.pending_actions._waiting)
        service.snapshot = replace(service.snapshot, connected=False)
        service.snapshotChanged.emit(service.snapshot)
        self.assertFalse(page.pending_actions._waiting)
        service.snapshot = replace(service.snapshot, connected=True)
        service.snapshotChanged.emit(service.snapshot)
        self.assertTrue(page.pending_actions.button.isEnabled())

    def test_toggle_and_repair_refuse_inventory_replaced_during_confirmation(self):
        from modules.common_widgets.common_dialogs import ConfirmDialog
        for repair in (False, True):
            page, service = self.create_page()
            service.change = Mock(return_value=True)
            service.repair = Mock(return_value=True)
            row = service.snapshot.inventory.rows[0]
            if repair:
                row = replace(row, running="inactive")
                service.snapshot = replace(service.snapshot, inventory=replace(service.snapshot.inventory, rows=(row,)))
            with patch("modules.main_pages.packet_capture.openwrt.router_signal_page.ConfirmDialog") as dialog:
                dialog.Accepted = ConfirmDialog.Accepted
                def switched():
                    service.snapshot = replace(service.snapshot, inventory=replace(service.snapshot.inventory))
                    return ConfirmDialog.Accepted
                dialog.return_value.exec.side_effect = switched
                if repair:
                    page._request_repair(row)
                else:
                    page._request_change(row)
            service.change.assert_not_called()
            service.repair.assert_not_called()

    def test_unchanged_inventory_still_allows_toggle_and_repair(self):
        from modules.common_widgets.common_dialogs import ConfirmDialog
        for repair in (False, True):
            page, service = self.create_page()
            service.change = Mock(return_value=True)
            service.repair = Mock(return_value=True)
            row = service.snapshot.inventory.rows[0]
            if repair:
                row = replace(row, running="inactive")
                service.snapshot = replace(service.snapshot, inventory=replace(service.snapshot.inventory, rows=(row,)))
            with patch("modules.main_pages.packet_capture.openwrt.router_signal_page.ConfirmDialog") as dialog:
                dialog.Accepted = ConfirmDialog.Accepted
                dialog.return_value.exec.return_value = ConfirmDialog.Accepted
                if repair:
                    page._request_repair(row)
                else:
                    page._request_change(row)
            if repair:
                service.repair.assert_called_once_with(row.section, confirmed=True)
            else:
                service.change.assert_called_once_with(row.section, False, confirmed=True)
