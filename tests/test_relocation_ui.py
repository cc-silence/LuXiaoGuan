"""Relocation confirmation, cancellation and connection ownership contracts."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from tests.test_signal_actions_ui import PendingSetup
from tests.test_relocation import MovingDevice
from modules.capture_services.openwrt.relocation_manager import RelocationManager
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessTaskResult
from modules.common_widgets.common_dialogs import ConfirmDialog
from tests.test_standalone import APP
import threading
import unittest
from tests.common_settings_fixture import create_page


class RelocationUiTests(unittest.TestCase):
    create_page = create_page
    def setup_page(self, light=False):
        setup = PendingSetup()
        setup.relocation_preview = Mock(return_value=True)
        setup.relocate = Mock(return_value=True)
        page, service = self.create_page(light, setup)
        review = RelocationManager(WirelessSetupManager(MovingDevice())).preview(threading.Event())
        return page, service, setup, review

    def test_relocation_cancel_and_accept(self):
        page, _, setup, review = self.setup_page()
        button = page.relocation_actions.button
        button.click()
        setup.relocation_preview.assert_called_once()
        self.assertFalse(button.isEnabled())
        with patch("modules.main_pages.packet_capture.openwrt.signal_relocation_actions.ConfirmDialog") as dialog:
            dialog.Accepted = ConfirmDialog.Accepted
            dialog.return_value.exec.return_value = ConfirmDialog.Rejected
            setup.completed.emit(WirelessTaskResult("move_preview", review))
        setup.relocate.assert_not_called()
        button.click()
        with patch("modules.main_pages.packet_capture.openwrt.signal_relocation_actions.ConfirmDialog") as dialog:
            dialog.Accepted = ConfirmDialog.Accepted
            dialog.return_value.exec.return_value = ConfirmDialog.Accepted
            setup.completed.emit(WirelessTaskResult("move_preview", review))
            self.assertIn(review["plan"]["hostname"], dialog.call_args.args[1])
        setup.relocate.assert_called_once_with(review)

    def test_relocation_rejects_switched_or_disconnected_router(self):
        for disconnect in (False, True):
            page, service, setup, review = self.setup_page()
            page.relocation_actions.button.click()
            with patch("modules.main_pages.packet_capture.openwrt.signal_relocation_actions.ConfirmDialog") as dialog:
                dialog.Accepted = ConfirmDialog.Accepted
                def changed():
                    if disconnect:
                        service.snapshot = replace(service.snapshot, connected=False)
                    else:
                        setup.identity = object()
                    return ConfirmDialog.Accepted
                dialog.return_value.exec.side_effect = changed
                setup.completed.emit(WirelessTaskResult("move_preview", review))
            setup.relocate.assert_not_called()

    def test_relocation_both_theme_geometry_and_offline_disabled(self):
        output = Path(__file__).parent / "signal-actions-preview"
        output.mkdir(exist_ok=True)
        for light in (False, True):
            page, service, setup, _ = self.setup_page(light)
            button = page.relocation_actions.button
            self.assertTrue(button.isVisible())
            self.assertTrue(button.property("dangerAction"))
            for width in (850, 1250):
                page.resize(width, 900)
                APP.processEvents()
                self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()))
                self.assertIs(button.parentWidget(), page.relocation_container)
            self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()))
            page.grab().save(str(output / ("relocation-light.png" if light else "relocation-dark.png")))
            service.snapshot = replace(service.snapshot, connected=False)
            service.snapshotChanged.emit(service.snapshot)
            self.assertFalse(button.isEnabled())
            button.click()
            setup.relocation_preview.assert_not_called()
            service.snapshot = replace(service.snapshot, connected=True)
            service.snapshotChanged.emit(service.snapshot)
            setup.active = True
            setup.snapshotChanged.emit(setup.snapshot)
            self.assertFalse(button.isEnabled())
            APP.processEvents()

    def test_disconnect_during_preview_does_not_leave_button_stuck(self):
        page, service, setup, review = self.setup_page()
        page.relocation_actions.button.click()
        service.snapshot = replace(service.snapshot, connected=False)
        service.snapshotChanged.emit(service.snapshot)
        self.assertFalse(page.relocation_actions._waiting)
        setup.completed.emit(WirelessTaskResult("move_preview", review))
        setup.relocate.assert_not_called()
        service.snapshot = replace(service.snapshot, connected=True)
        service.snapshotChanged.emit(service.snapshot)
        self.assertTrue(page.relocation_actions.button.isEnabled())
