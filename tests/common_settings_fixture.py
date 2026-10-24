"""Reusable common-settings page fixture with no device operations."""
from types import SimpleNamespace
from unittest.mock import Mock
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from tests.test_signal_actions_ui import Service, PendingSetup
from modules.ui_scale_manager import UiScaleManager
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.main_pages.packet_capture.openwrt.router_common_settings_page import RouterCommonSettingsPage


class Management(QObject):
    completed=Signal(str,object)
    failed=Signal(str,str)
    def __init__(self):
        super().__init__()
        self.manager=SimpleNamespace(ssh=SimpleNamespace(connected=True,_user="root",_host="router",_port=22,_client=object(),host_fingerprint="pinned"))
        self.submit=Mock(return_value="request")
        self.cancel=Mock()


class Owner(QObject):
    activityChanged=Signal()
    openwrtSnapshotChanged=Signal(object)
    def __init__(self,service):
        super().__init__()
        self.router_signals=service
        self.router_management=Management()
        self.is_busy=False


def create_page(self,light=False,setup=None):
    parent=QWidget()
    parent.scale_manager=UiScaleManager(100)
    parent.light_theme=light
    from modules.config_manager import ConfigManager
    from modules.dialog_theme import build_base_stylesheet
    parent.setStyleSheet(build_base_stylesheet(ConfigManager(),light_theme=light,theme_name="light.qss" if light else "dark.qss",scale_manager=parent.scale_manager))
    service=Service()
    service.setup=setup or PendingSetup()
    owner=Owner(service)
    page=RouterCommonSettingsPage(owner,lambda: {},parent)
    page._fixture_owner=owner
    page.setStyleSheet(CapturePageStyleManager(parent)._build_stylesheet())
    page.resize(1100,900)
    parent.resize(1200,1000)
    parent.show();page.show();APP.processEvents()
    self.addCleanup(parent.deleteLater)
    self.addCleanup(page.dispose)
    return page,service
