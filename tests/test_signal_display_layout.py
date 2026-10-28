"""Compact signal rows retain identity and layout after removal of optional details."""
from dataclasses import replace
from pathlib import Path
import unittest
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

    def test_compact_rows_ignore_historical_details_and_survive_scale_changes(self):
        from PySide6.QtWidgets import QCheckBox
        for light in (False, True):
            page, service = self.create_page(light)
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
                for width, height in ((850, 460), (1200, 580)):
                    page.resize(width, height)
                    self.settle()
                    self.assertFalse(hasattr(page, 'details_check'))
                    self.assertFalse(any(c.text() == '\u663e\u793a\u4fe1\u53f7\u8be6\u60c5' for c in page.findChildren(QCheckBox)))
                    cards = sorted(page._row_widgets.values(), key=lambda widget: widget.y())
                    self.assertTrue(all(card.detail_label.text() != '' for card in cards))
                    for first, second in zip(cards, cards[1:]):
                        self.assertLess(first.geometry().bottom(), second.y())
                    self.assertEqual(tuple(id(w) for w in page._row_widgets.values()), identity)
                output = Path('build/review/signal-no-details')
                output.mkdir(parents=True, exist_ok=True)
                self.assertTrue(page.grab().save(str(output / (('light' if light else 'dark')+str(scale)+'.png'))))
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
