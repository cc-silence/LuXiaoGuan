"""Save-only action dispatch and orphan review selection/connection contracts."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PySide6.QtCore import Qt
from tests.test_standalone import APP
from tests import test_signal_actions_ui as signal_ui
from tests import test_direct_wireless_save as direct_ui
from tests.test_orphan_interfaces import OrphanDevice,ORPHAN
from tests.test_wireless_setup import TARGET
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.orphan_interface_manager import OrphanInterfaceManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessTaskResult
from modules.main_pages.packet_capture.openwrt.orphan_review_dialog import OrphanReviewDialog
from modules.common_widgets.common_dialogs import ConfirmDialog

class SaveButtonTests(unittest.TestCase):
    actions=direct_ui.DirectUiTests.actions
    def test_save_only_dispatches_disabled_save_without_apply_confirmation(self):
        actions,service,dialogs=self.actions()
        actions.join(TARGET)
        dialog=dialogs[0];self.addCleanup(dialog.deleteLater)
        dialog.password.edit.setText("offline-password")
        with patch.object(actions,"_confirm",side_effect=AssertionError("Only apply needs confirmation")):
            dialog.save_only_button.click()
        self.assertEqual(service.save.call_args.kwargs,{"apply":False})
        self.assertFalse(dialog.save_only_button.isEnabled())
        self.assertFalse(dialog.save_button.isEnabled())
        service.completed.emit(WirelessTaskResult("save_only",{}))
        self.assertEqual(dialog.password.edit.text(),"")

    def test_ap_and_relay_buttons_render_at_both_scales(self):
        from PySide6.QtWidgets import QWidget
        from modules.ui_scale_manager import UiScaleManager
        from tests.test_wireless_ui import environment
        from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog,RelaySetupDialog
        from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_ap,configure_relay
        output=Path("tests/signal-actions-preview");output.mkdir(exist_ok=True)
        for light in (False,True):
            for percent in (100,150):
                parent=QWidget();self.addCleanup(parent.deleteLater)
                parent.scale_manager=UiScaleManager(percent);parent.setProperty("settingsLightTheme",light)
                from modules.config_manager import ConfigManager
                from modules.dialog_theme import build_base_stylesheet
                parent.setStyleSheet(build_base_stylesheet(ConfigManager(),light_theme=light,scale_manager=parent.scale_manager))
                for cls,configure,name in ((AccessPointSetupDialog,configure_ap,"ap"),(RelaySetupDialog,configure_relay,"relay")):
                    dialog=cls("radio7","5G",parent) if name=="ap" else cls("Upper",parent,radio_name="radio7")
                    self.addCleanup(dialog.deleteLater)
                    configure(dialog,environment())
                    dialog.show();APP.processEvents()
                    for button in (dialog.save_only_button,dialog.save_button,dialog.cancel_button):
                        self.assertTrue(button.isVisible())
                        self.assertGreaterEqual(button.width(),button.fontMetrics().horizontalAdvance(button.text())+8)
                    dialog.grab().save(str(output/(name+"-save-"+str(light)+"-"+str(percent)+".png")))
                    dialog.close()

class OrphanUiTests(unittest.TestCase):
    create_page=signal_ui.SignalActionTests.create_page
    def setup_page(self,light=False):
        setup=signal_ui.PendingSetup()
        setup.orphan_preview=Mock(return_value=True)
        setup.cleanup_orphans=Mock(return_value=True)
        page,service=self.create_page(light,setup)
        review=OrphanInterfaceManager(WirelessSetupManager(OrphanDevice())).preview(threading.Event())
        return page,service,setup,review

    def test_review_default_no_selection_and_unknown_owner_cannot_be_checked(self):
        page,_,_,review=self.setup_page()
        dialog=OrphanReviewDialog(review,page);self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.selected(),[])
        self.assertFalse(dialog.clean_button.isEnabled())
        for i in range(dialog.tree.topLevelItemCount()):
            item=dialog.tree.topLevelItem(i)
            if item.text(0)==ORPHAN:
                item.setCheckState(0,Qt.Checked)
            else:self.assertFalse(item.flags()&Qt.ItemIsUserCheckable)
        self.assertEqual(dialog.selected(),[ORPHAN])
        self.assertTrue(dialog.clean_button.isEnabled())

    def test_cancel_and_explicit_selection_confirmation(self):
        page,_,setup,review=self.setup_page()
        for accepted in (False,True):
            page.orphan_actions.button.click()
            with patch("modules.main_pages.packet_capture.openwrt.signal_orphan_actions.OrphanReviewDialog") as dialog, patch(
                    "modules.main_pages.packet_capture.openwrt.signal_orphan_actions.ConfirmDialog") as confirm:
                dialog.return_value.Accepted=ConfirmDialog.Accepted
                dialog.return_value.exec.return_value=ConfirmDialog.Accepted if accepted else ConfirmDialog.Rejected
                dialog.return_value.selected.return_value=[ORPHAN]
                confirm.return_value.Accepted=ConfirmDialog.Accepted
                confirm.return_value.exec.return_value=ConfirmDialog.Accepted
                setup.completed.emit(WirelessTaskResult("orphan_preview",review))
                if not accepted:setup.cleanup_orphans.assert_not_called()
        setup.cleanup_orphans.assert_called_once_with(review,[ORPHAN])

    def test_router_change_during_confirmation_refuses_cleanup(self):
        page,_,setup,review=self.setup_page()
        page.orphan_actions.button.click()
        with patch("modules.main_pages.packet_capture.openwrt.signal_orphan_actions.OrphanReviewDialog") as dialog, patch(
                "modules.main_pages.packet_capture.openwrt.signal_orphan_actions.ConfirmDialog") as confirm:
            dialog.return_value.Accepted=ConfirmDialog.Accepted
            dialog.return_value.exec.return_value=ConfirmDialog.Accepted
            dialog.return_value.selected.return_value=[ORPHAN]
            confirm.return_value.Accepted=ConfirmDialog.Accepted
            def changed():
                setup.identity=object()
                return ConfirmDialog.Accepted
            confirm.return_value.exec.side_effect=changed
            setup.completed.emit(WirelessTaskResult("orphan_preview",review))
        setup.cleanup_orphans.assert_not_called()

    def test_actual_dialog_render_in_both_themes_and_scales(self):
        output=Path("tests/signal-actions-preview");output.mkdir(exist_ok=True)
        for light in (False,True):
            page,_,_,review=self.setup_page(light)
            for percent in (100,150):
                from modules.ui_scale_manager import UiScaleManager
                page.parentWidget().scale_manager=UiScaleManager(percent)
                dialog=OrphanReviewDialog(review,page);self.addCleanup(dialog.deleteLater)
                dialog.show();APP.processEvents()
                self.assertGreater(dialog.tree.viewport().height(),100)
                self.assertGreaterEqual(dialog.clean_button.width(),dialog.clean_button.fontMetrics().horizontalAdvance(dialog.clean_button.text()))
                dialog.grab().save(str(output/("orphans-"+str(light)+"-"+str(percent)+".png")))
                dialog.close()
