"""Foreground actions yield runtime reads without overlapping remote workers."""
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame
from tests.test_feedback_regressions import APP, Owner
from tests.test_wireless_runtime_status import sample, row
from tests.test_wireless_ui import environment
from modules.capture_services.router_signal_service import RouterSignalService
from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory, SignalSnapshot
from modules.capture_services.openwrt.wireless_setup_models import WirelessState


class SignalForegroundTests(unittest.TestCase):
    def create(self, owner=None):
        owner = owner or Owner()
        self.addCleanup(owner.deleteLater)
        service = RouterSignalService(owner)
        signal = RouterSignal('home', 'Home', 'ap', 'radio7', '5g', False, False, 'active')
        service.snapshot = SignalSnapshot(SignalInventory(rows=(signal,), revision='a'*64),
                                          connected=True, stale=False)
        service._verified = True
        service.setup.snapshot = WirelessState(environment=environment())
        service.setup._inspection_revision = 'a'*64
        return owner, service

    def test_read_keeps_actions_available_but_retains_transport_mutex(self):
        owner, service = self.create()
        self.assertTrue(service.refresh_status(False))
        self.assertTrue(owner.task_controller.busy)
        self.assertTrue(service.active)
        self.assertFalse(service.snapshot.busy)
        self.assertFalse(service.snapshot.stale)
        self.assertIsNone(owner.task_controller.begin('other', 'other'))
        owner.workers.pop()[2](sample(row()))
        self.assertFalse(owner.task_controller.busy)

    def test_signal_change_waits_for_terminal_read_result_then_starts_once(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                owner, service = self.create()
                service.refresh_status(False)
                _, event, success, failure = owner.workers.pop()
                service.manager.change = Mock(return_value=service.snapshot.inventory)
                self.assertTrue(service.change('home', False, confirmed=True))
                self.assertTrue(event.is_set())
                self.assertTrue(service.snapshot.busy)
                self.assertFalse(service.change('home', False, confirmed=True))
                service.manager.change.assert_not_called()
                self.assertEqual(owner.workers, [])
                if failed:
                    failure('Cancelled read', 'cancelled')
                else:
                    success(sample(row()))
                self.assertEqual(len(owner.workers), 1)
                self.assertIsNone(service._pending_foreground)
                operation, event, success, _ = owner.workers.pop()
                success(operation(event))
                service.manager.change.assert_called_once()
                self.assertFalse(service.active)
                self.assertFalse(service.snapshot.stale)
                self.assertFalse(any('Cancelled read' in text for text, *_ in service.messages))

    def test_setup_delete_uses_same_handoff_and_original_arguments(self):
        owner, service = self.create()
        service.refresh_status(False)
        _, event, _, failure = owner.workers.pop()
        revision = environment().data['revision']
        service.setup.manager.delete = Mock(return_value={})
        self.assertTrue(service.setup.delete('home', revision))
        self.assertTrue(event.is_set())
        self.assertEqual(owner.workers, [])
        failure('Cancelled', 'cancelled')
        operation, event, _, _ = owner.workers.pop()
        result = operation(event)
        self.assertFalse(result.error)
        service.setup.manager.delete.assert_called_once_with('home', revision, event, unittest.mock.ANY)
        owner.task_controller.finish()
        service.setup.active = False

    def test_handoff_rejects_changed_context_and_releases_callback(self):
        for changed in ('connection', 'inventory', 'disconnect', 'shutdown'):
            with self.subTest(changed=changed):
                owner, service = self.create()
                service.refresh_status(False)
                _, _, _, failure = owner.workers.pop()
                start, rejected = Mock(return_value=True), Mock()
                self.assertTrue(service.defer_foreground(start, rejected))
                if changed == 'connection':
                    owner.ssh._client = object()
                elif changed == 'inventory':
                    service.snapshot = replace(service.snapshot, inventory=replace(service.snapshot.inventory, revision='b'*64))
                elif changed == 'disconnect':
                    service._connection_changed(NS(connection_state=NS(value='disconnected')))
                else:
                    owner._shutting_down = True
                failure('Cancelled', 'cancelled')
                start.assert_not_called()
                rejected.assert_called_once()
                self.assertIsNone(service._pending_foreground)
                self.assertIsNone(service._status_cancel_event)
                self.assertFalse(owner.task_controller.busy)

    def test_explicit_cancel_drops_accepted_action_even_if_read_already_cancelled(self):
        owner, service = self.create()
        service.refresh_status(False)
        _, _, _, failure = owner.workers.pop()
        start, rejected = Mock(return_value=True), Mock()
        self.assertTrue(service.defer_foreground(start, rejected))
        self.assertTrue(service.cancel())
        failure('Cancelled', 'cancelled')
        start.assert_not_called()
        rejected.assert_called_once()
        self.assertIsNone(service._pending_foreground)
        self.assertTrue(service._auto_refresh_blocked)

    def test_editor_cancel_releases_its_queued_request(self):
        owner, service = self.create()
        service.refresh_status(False)
        _, _, _, failure = owner.workers.pop()
        completed = Mock()
        service.setup.completed.connect(completed)
        service.setup.manager.delete = Mock()
        self.assertTrue(service.setup.delete('home', 'a'*192))
        self.assertFalse(service.cancel_deferred(object()))
        self.assertTrue(service.setup.cancel())
        failure('Cancelled', 'cancelled')
        self.assertFalse(owner.workers)
        service.setup.manager.delete.assert_not_called()
        completed.assert_called_once()
        self.assertTrue(completed.call_args.args[0].error)

    def test_real_threads_exit_before_foreground_operation_runs(self):
        from modules.capture_services.task_completion import start_capture_task
        class ThreadOwner(Owner):
            def __init__(self):
                super().__init__()
                self._openwrt_workers = {}
                self.read_thread = None
            def _start_openwrt_worker(self, session, operation, event, success, failure, *args):
                start_capture_task(self, session, operation, event, success, failure, *args)
                if self.read_thread is None:
                    self.read_thread = self._openwrt_workers[session][0]
            def _cleanup_openwrt_worker(self, session):
                self._openwrt_workers.pop(session, None)
        owner, service = self.create(ThreadOwner())
        entered, release, foreground, read_finished = Event(), Event(), Event(), Event()
        def read(cancel, *args, **kwargs):
            entered.set()
            release.wait(3)
            return sample(row())
        def change(*args):
            self.assertTrue(read_finished.is_set())
            foreground.set()
            return service.snapshot.inventory
        def wait_for(condition):
            deadline = time.monotonic() + 3
            while not condition() and time.monotonic() < deadline:
                APP.processEvents()
                time.sleep(.005)
            self.assertTrue(condition())
        service.manager.change = change
        with patch('modules.capture_services.openwrt.wireless_status_manager.WirelessStatusManager.read', side_effect=read):
            try:
                service.refresh_status(False)
                wait_for(entered.is_set)
                owner.read_thread.finished.connect(read_finished.set, Qt.DirectConnection)
                self.assertTrue(service.change('home', False, confirmed=True))
                APP.processEvents()
                time.sleep(.02)
                APP.processEvents()
                self.assertFalse(foreground.is_set())
            finally:
                release.set()
                wait_for(lambda: not owner._openwrt_workers)
        self.assertTrue(foreground.is_set())
        self.assertFalse(owner.task_controller.busy)

    def test_real_page_row_buttons_remain_enabled_during_read_in_both_themes(self):
        from modules.main_pages.packet_capture.openwrt.router_signal_page import RouterSignalPage
        from modules.ui_scale_manager import UiScaleManager
        from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
        for light in (False, True):
            owner, service = self.create()
            parent = QFrame()
            parent.scale_manager = UiScaleManager(100)
            parent.light_theme = light
            from modules.config_manager import ConfigManager
            from router_app.appearance import build_main_stylesheet
            parent.setObjectName('packetCapturePage')
            parent.setAttribute(Qt.WA_StyledBackground, True)
            parent.setStyleSheet(build_main_stylesheet(ConfigManager(),
                'light.qss' if light else 'dark.qss', parent.scale_manager)
                + CapturePageStyleManager(parent)._capture_stylesheet(light))
            page = RouterSignalPage(service, None, parent)
            page.setStyleSheet(CapturePageStyleManager(parent)._capture_stylesheet(light))
            self.addCleanup(parent.deleteLater)
            self.addCleanup(page.dispose)
            parent.resize(1140, 760)
            page.resize(1100, 680)
            parent.show()
            page.show()
            APP.processEvents()
            if not service._status_running:
                service.refresh_status(False)
            card = page._row_widgets['home']
            self.assertTrue(card.edit_button.isEnabled())
            self.assertTrue(card.delete_button.isEnabled())
            self.assertTrue(card.button.isEnabled())
            with patch.object(page.setup_panels, 'edit') as edit:
                card.edit_button.click()
                edit.assert_called_once_with(card.row)
            page.resize(1100, 680)
            folder = Path('build/review/signal-foreground-handoff')
            folder.mkdir(parents=True, exist_ok=True)
            self.assertTrue(parent.grab().save(str(folder / ('light.png' if light else 'dark.png'))))
            service.change('home', False, confirmed=True)
            self.assertFalse(card.button.isEnabled())
            service.cancel()
            owner.workers.pop()[3]('Cancelled', 'cancelled')


if __name__ == '__main__':
    unittest.main()
