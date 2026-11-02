"""Action-state changes must not move focus down a configured-signal list."""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt
from tests.test_standalone import APP
from tests import test_signal_actions_ui as signal_fixtures
from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory


class SignalRowScrollTests(unittest.TestCase):
    create_page = signal_fixtures.SignalActionTests.create_page

    def configured_page(self, light=False):
        page, service = self.create_page(light=light)
        rows = tuple(RouterSignal(f"signal{i:02}", f"Network {i:02}", "ap", "radio0", "2g",
                                  bool(i % 2), False, "inactive" if i % 2 else "active") for i in range(24))
        service.snapshot = replace(service.snapshot, inventory=SignalInventory(rows, "a" * 64))
        service.snapshotChanged.emit(service.snapshot)
        page.window().activateWindow()
        APP.processEvents()
        self.assertGreater(page.scroll.verticalScrollBar().maximum(), 0)
        return page, service

    def test_busy_and_stale_preserve_scroll_for_all_configured_actions(self):
        for light in (False, True):
            page, service = self.configured_page(light)
            for state in ("busy", "stale"):
                for name in ("button", "edit_button", "delete_button"):
                    with self.subTest(light=light, state=state, action=name):
                        idle = replace(service.snapshot, busy=False, stale=False)
                        service.snapshot = idle
                        service.snapshotChanged.emit(idle)
                        APP.processEvents()
                        button = getattr(page._row_widgets["signal00"], name)
                        page.scroll.ensureWidgetVisible(button)
                        button.setFocus(Qt.MouseFocusReason)
                        APP.processEvents()
                        self.assertTrue(button.hasFocus())
                        bar = page.scroll.verticalScrollBar()
                        before = bar.value()
                        service.snapshot = replace(idle, **{state: True})
                        service.snapshotChanged.emit(service.snapshot)
                        APP.processEvents()
                        self.assertEqual(bar.value(), before)
                        self.assertFalse(any(b.hasFocus() for row in page._row_widgets.values()
                                             for b in (row.button, row.edit_button, row.delete_button)))

    def test_toggle_click_start_and_completion_keep_scroll_and_reuse_rows(self):
        page, service = self.configured_page()
        original = dict(page._row_widgets)
        idle = service.snapshot
        target = page._row_widgets["signal00"]
        target.button.setFocus(Qt.MouseFocusReason)
        APP.processEvents()
        before = page.scroll.verticalScrollBar().value()

        def begin(*args, **kwargs):
            service.snapshot = replace(idle, busy=True)
            service.snapshotChanged.emit(service.snapshot)
            return True

        service.change = Mock(side_effect=begin)
        with patch("modules.main_pages.packet_capture.openwrt.router_signal_page.ConfirmDialog"), \
                patch.object(page, "_confirm_inventory_action", return_value=True):
            target.button.click()
        APP.processEvents()
        self.assertEqual(page.scroll.verticalScrollBar().value(), before)
        service.change.assert_called_once_with("signal00", False, confirmed=True)
        changed = tuple(replace(row, disabled=True, running="inactive") if row.section == "signal00" else row
                        for row in idle.inventory.rows)
        service.snapshot = replace(idle, inventory=replace(idle.inventory, rows=changed, revision="b" * 64))
        service.snapshotChanged.emit(service.snapshot)
        APP.processEvents()
        self.assertEqual(page.scroll.verticalScrollBar().value(), before)
        self.assertEqual(page._row_widgets, original)
        self.assertTrue(target.button.isEnabled())
