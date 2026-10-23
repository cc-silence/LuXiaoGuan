"""The main window remains usable while wireless apply waits or loses SSH."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from PySide6.QtWidgets import QWidget, QPushButton
from PySide6.QtCore import QCoreApplication, QEvent
from tests.test_standalone import APP
from tests.test_feedback_regressions import Owner
from modules.capture_services.router_signal_service import RouterSignalService
from modules.capture_services.openwrt.signal_models import SignalSnapshot
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.task_completion import start_capture_task
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog, RelaySetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_panels import SignalSetupPanels
from modules.main_pages.packet_capture.openwrt.signal_setup_actions import SignalSetupActions


class WirelessResponsivenessTests(unittest.TestCase):
    def pump_until(self, condition, timeout=4):
        end = time.monotonic() + timeout
        while not condition() and time.monotonic() < end:
            APP.processEvents()
            time.sleep(0.002)
        self.assertTrue(condition(), "Bounded background operation did not settle")

    def exercise(self, dialog_type, disconnect):
        owner = Owner()
        owner._openwrt_workers = {}
        owner._cleanup_openwrt_worker = lambda session: owner._openwrt_workers.pop(session, None)
        owner._start_openwrt_worker = lambda *args: start_capture_task(owner, *args)
        signals = RouterSignalService(owner)
        signals.snapshot = SignalSnapshot(connected=True)
        setup = signals.setup
        parent = QWidget()
        parent.show()
        button = QPushButton("Local action", parent)
        button.show()
        clicked = Mock()
        button.clicked.connect(clicked)
        panels = SimpleNamespace(page=parent, empty_hint=Mock(), _dialogs=set())
        def release_dialog(dialog):
            panels._dialogs.discard(dialog)
            dialog.deleteLater()
        panels._release = release_dialog
        actions = SignalSetupActions(panels, setup)
        dialog = (AccessPointSetupDialog("radio7", "5G", parent) if dialog_type == "ap"
                  else RelaySetupDialog("Upstream", parent, radio_name="radio7"))
        dialog._router_connection = owner.ssh._client
        started, release = threading.Event(), threading.Event()
        threads = []
        def operation(cancel, message):
            threads.append(threading.get_ident())
            started.set()
            if not release.wait(4):
                raise AssertionError("Test did not release background operation")
            raise SignalError("recovery" if disconnect else "authentication", "Offline simulated failure")
        try:
            SignalSetupPanels._show(panels, dialog)
            actions._start(dialog, "save", lambda: setup._start("save", operation), Mock())
            self.pump_until(started.is_set)
            self.assertTrue(owner.task_controller.busy)
            self.assertTrue(dialog._busy)
            self.assertNotEqual(threads[0], threading.get_ident())
            self.assertIsNone(APP.activeModalWidget(), "Wireless editor blocks the main window during SSH wait")
            button.click()
            clicked.assert_called_once()
            if disconnect:
                owner.ssh._client = None
                owner.ssh.connected = False
                owner.openwrtSnapshotChanged.emit(SimpleNamespace(
                    connection_state=SimpleNamespace(value="disconnected"), dependencies=()))
                APP.processEvents()
                self.assertTrue(owner.task_controller.busy)
                self.assertFalse(owner._shutting_down)
                self.assertIsNone(APP.activeModalWidget())
            release.set()
            self.pump_until(lambda: not owner._openwrt_workers)
            self.assertFalse(owner.task_controller.busy)
            self.assertFalse(setup.active)
            self.assertIsNone(actions.pending)
            self.assertFalse(dialog._busy)
            self.assertTrue(dialog.status_label.text())
            self.assertTrue(parent.isVisible())
        finally:
            release.set()
            self.pump_until(lambda: not owner._openwrt_workers)
            dialog._busy = False
            dialog.reject()
            actions.dispose()
            owner.deleteLater()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()

    def test_ap_main_window_remains_usable_during_apply_and_disconnect(self):
        self.exercise("ap", True)

    def test_relay_main_window_remains_usable_during_failed_apply(self):
        self.exercise("sta", False)
