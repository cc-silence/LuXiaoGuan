"""Login cancellation uses the current task event and the shared danger style."""
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from PySide6.QtWidgets import QWidget, QVBoxLayout
from tests.test_standalone import APP
from modules.main_pages.packet_capture.openwrt.connection_section import ConnectionSection
from modules.main_pages.packet_capture_page import PacketCapturePage
from modules.capture_services.task_controller import CaptureTaskController
from modules.capture_services.openwrt.models import CaptureTaskType
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.ui_scale_manager import UiScaleManager


class LoginCancelButtonTests(unittest.TestCase):
    def section(self, action):
        parent = QWidget()
        parent.resize(640, 780)
        layout = QVBoxLayout(parent)
        section = ConnectionSection(parent, {}, action)
        layout.addWidget(section.card)
        self.addCleanup(parent.deleteLater)
        return parent, section

    def test_only_connecting_displays_cancel_and_stopping_prevents_repeat(self):
        calls = []
        parent, section = self.section(calls.append)
        for state in ('disconnected', 'connected', 'disconnecting', 'failed'):
            section.set_connection_state(state)
            self.assertTrue(section.cancel_login_button.isHidden(), state)
        section.set_connection_state('connecting')
        self.assertFalse(section.cancel_login_button.isHidden())
        self.assertTrue(section.cancel_login_button.isEnabled())
        self.assertFalse(section.login_button.isEnabled())
        section.cancel_login_button.click()
        self.assertEqual(calls, ['cancel_login'])
        section.set_connection_state('connecting', stopping=True)
        self.assertFalse(section.cancel_login_button.isEnabled())
        section.cancel_login_button.click()
        self.assertEqual(len(calls), 1)
        section.set_connection_state('failed')
        self.assertTrue(section.login_button.isEnabled())

    def test_click_sets_the_real_login_cancel_event_and_keeps_task_owned(self):
        controller = CaptureTaskController()
        event = controller.begin(CaptureTaskType.CONNECT, 'test-login')
        service = NS(openwrt_snapshot=NS(connection_state=NS(value='connecting'),
                                       task_type=NS(value='connect')),
                     cancel_openwrt_task=controller.cancel)
        owner = NS(capture_service=service)
        _, section = self.section(lambda action: PacketCapturePage._handle_openwrt_action(owner, action))
        section.set_connection_state('connecting')
        section.cancel_login_button.click()
        self.assertTrue(event.is_set())
        self.assertTrue(controller.busy)
        controller.finish()
        self.assertFalse(controller.busy)

    def test_stale_login_cancel_does_not_cancel_connected_or_other_tasks(self):
        for state, task in (('connected', 'connect'), ('connecting', 'wps'), ('failed', 'connect')):
            controller = CaptureTaskController()
            event = controller.begin(CaptureTaskType.CONNECT, 'other')
            owner = NS(capture_service=NS(openwrt_snapshot=NS(connection_state=NS(value=state),
                       task_type=NS(value=task)), cancel_openwrt_task=controller.cancel))
            PacketCapturePage._handle_openwrt_action(owner, 'cancel_login')
            self.assertFalse(event.is_set())
            controller.finish()

    def test_real_login_row_renders_red_cancel_in_both_themes(self):
        output = Path('build/previews/login-cancel')
        output.mkdir(parents=True, exist_ok=True)
        for light in (False, True):
            parent, section = self.section(lambda _:None)
            page = NS(scale_manager=UiScaleManager(100), light_theme=light)
            parent.setStyleSheet(CapturePageStyleManager(page)._build_stylesheet())
            section.set_connection_state('connecting')
            parent.show(); APP.processEvents()
            button = section.cancel_login_button
            self.assertTrue(button.property('dangerAction'))
            self.assertGreaterEqual(button.width(), button.sizeHint().width())
            self.assertTrue(parent.grab().save(str(output / ('light.png' if light else 'dark.png'))))
            parent.close()
