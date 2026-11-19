import unittest
from dataclasses import replace
from unittest.mock import patch
from PySide6.QtWidgets import QWidget
from tests.test_dependency_group_controls import APP, ViewOwner, info
from modules.capture_services.openwrt.models import OpenWrtConnectionState
from modules.main_pages.packet_capture.openwrt.common_tool_section import CommonToolSection
from modules.common_widgets.common_dialogs import ConfirmDialog


class CurlFailureUninstallUiTests(unittest.TestCase):
    def test_buttons_and_confirmation(self):
        owner=ViewOwner(); parent=QWidget()
        owner.openwrt_snapshot=replace(owner.openwrt_snapshot, connection_state=OpenWrtConnectionState.CONNECTED, dependencies=(info('curl','unknown'),info('wireless_rpc','ready')))
        section=CommonToolSection(parent,owner)
        self.addCleanup(parent.deleteLater); self.addCleanup(section.dispose)
        self.assertFalse(section.curl_uninstall_button.isHidden())
        self.assertFalse(section.rows['curl'][1].isHidden())
        self.assertTrue(section.rows['wireless_rpc'][1].isHidden())
        with patch.object(owner.dependency_service,'ensure_available',return_value=True), patch.object(owner.dependency_service,'uninstall_async',create=True) as uninstall, patch('modules.main_pages.packet_capture.openwrt.common_tool_section.ConfirmDialog') as dialog:
            dialog.Accepted=ConfirmDialog.Accepted
            dialog.return_value.exec.return_value=ConfirmDialog.Accepted
            section._clicked('curl',force_uninstall=True)
            uninstall.assert_called_once_with({'curl':True},['curl'],scope='curl')
        updated=replace(owner.openwrt_snapshot,dependencies=(info('curl','missing'),info('wireless_rpc','missing')))
        section.apply_snapshot(updated)
        self.assertTrue(section.curl_uninstall_button.isHidden())
        self.assertFalse(section.rows['wireless_rpc'][1].isHidden())
