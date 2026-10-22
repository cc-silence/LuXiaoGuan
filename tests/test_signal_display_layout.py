"""Rendered detail rows grow, wrap and scroll using Qt layout constraints."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock
from tests.test_signal_actions_ui import SignalActionTests as _ActionHelpers, APP, PendingSetup
from modules.ui_scale_manager import UiScaleManager
from modules.config_manager import ConfigManager
from router_app.appearance import build_main_stylesheet
from modules.capture_services.openwrt.signal_details import SignalClient, SignalDetails
from modules.capture_services.openwrt.signal_models import RouterSignal
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager


class SignalLayoutTests(unittest.TestCase):
    create_page = _ActionHelpers.create_page

    def settle(self):
        for _ in range(5):
            APP.processEvents()

    def assert_cards(self, page):
        cards = sorted(page._row_widgets.values(), key=lambda w: w.y())
        for card in cards:
            detail = card.detail_label
            self.assertTrue(detail.isVisible())
            self.assertGreaterEqual(detail.height(), detail.heightForWidth(detail.width()))
            self.assertLess(detail.geometry().bottom(), card.height())
        for first, second in zip(cards, cards[1:]):
            self.assertLess(first.geometry().bottom(), second.y())

    def test_details_expand_collapse_wrap_and_survive_scale_changes(self):
        for light in (False, True):
            page, service = self.create_page(light)
            service.set_details_enabled = Mock()
            clients = tuple(SignalClient("02:11:22:33:44:55", "192.168.1.20", "Long client name "+str(i), -45) for i in range(8))
            rows = tuple(replace(service.snapshot.inventory.rows[0], section="signal"+str(i),
                                 details=SignalDetails(interface="phy0-ap0", channel="6", bandwidth="HE40",
                                                       clients=clients, clients_state="ready")) for i in range(8))
            service.snapshot = replace(service.snapshot, inventory=replace(service.snapshot.inventory, rows=rows))
            page.apply_snapshot(service.snapshot)
            identity = tuple(id(w) for w in page._row_widgets.values())
            owner = page.parentWidget()
            for scale in (100, 175, 100):
                owner.scale_manager = UiScaleManager(scale)
                owner.scale_manager.publish(owner)
                owner.setStyleSheet(build_main_stylesheet(ConfigManager(), "light.qss" if light else "dark.qss", owner.scale_manager))
                owner.scale_manager.apply_bound_metrics(owner)
                page.setStyleSheet(CapturePageStyleManager(owner)._capture_stylesheet(light))
                page.resize(850, 460)
                page.details_check.setChecked(True)
                self.settle()
                self.assert_cards(page)
                expanded = page.scroll.verticalScrollBar().maximum()
                self.assertGreater(expanded, 0)
                page.resize(1200, 580)
                self.settle()
                self.assert_cards(page)
                self.assertEqual(tuple(id(w) for w in page._row_widgets.values()), identity)
                page.details_check.setChecked(False)
                self.settle()
                self.assertLess(page.scroll.verticalScrollBar().maximum(), expanded)
            owner.hide()

    def test_names_use_runtime_ssid_and_never_disguise_label(self):
        page, service = self.create_page()
        row = RouterSignal("home", "old-name", "sta", "radio7", "2g", False, False, "active",
                           label="vivo-Y73s", runtime_ssid="202", runtime_band="5g")
        self.assertEqual(page._display_name(row), "202")
        self.assertEqual(page._band_text(row), "5G \u00b7 radio7")
        self.assertEqual(page._display_name(replace(row, runtime_ssid="")), "old-name")

    def test_pending_button_green_breathing_and_resolved_cleanup(self):
        setup = PendingSetup()
        page, service = self.create_page(setup=setup)
        service.snapshot = replace(service.snapshot, pending_changes=True)
        service.snapshotChanged.emit(service.snapshot)
        button = page.pending_actions.button
        self.settle()
        self.assertTrue(button.property("signalPrimaryAction"))
        self.assertTrue(button._breathing)
        setup.snapshot = replace(setup.snapshot, pending_changes=False)
        service.snapshot = replace(service.snapshot, pending_changes=False)
        service.snapshotChanged.emit(service.snapshot)
        self.assertFalse(button._breathing)
        self.assertFalse(button.isVisible())
