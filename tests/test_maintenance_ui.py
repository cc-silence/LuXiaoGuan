"""Maintenance confirmations, two-column illustrations and real mouse focus regressions."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtTest import QTest
from tests.common_settings_fixture import create_page
from tests.test_signal_actions_ui import PendingSetup
from tests.test_standalone import APP
from modules.common_widgets.card_icon import CardIcon
from modules.main_pages.packet_capture.openwrt.maintenance_review_dialog import MaintenanceReviewDialog

REVIEW = dict(operation="reset", model="Router A", version="25.12", reset_command="/sbin/factoryreset", token="a"*64)


class MaintenanceUiTests(unittest.TestCase):
    create_page = create_page

    def test_all_fourteen_cards_have_reference_icons_in_responsive_groups(self):
        page, _ = self.create_page()
        self.assertEqual(len(page.cards), 14)
        grouped = [card for _title, cards in page.card_groups.groups for card in cards]
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual(set(grouped), set(page.cards))
        rectangles = []
        for card in grouped:
            illustration = card.findChild(CardIcon)
            self.assertIsNotNone(illustration)
            self.assertFalse(illustration.icon.isNull())
            rectangles.append(QRect(card.mapTo(page, QPoint()), card.size()))
        for index, rectangle in enumerate(rectangles):
            for other in rectangles[index + 1:]:
                self.assertFalse(rectangle.intersects(other))

    def test_disabling_clicked_actions_does_not_walk_focus_and_scroll_to_bottom(self):
        for name in ("lan_button","system_button","relocation","orphan"):
            setup=PendingSetup()
            setup.relocation_preview=Mock(return_value=True)
            setup.orphan_preview=Mock(return_value=True)
            page,_=self.create_page(setup=setup)
            page.resize(960,420);APP.processEvents()
            button = page.relocation_actions.button if name=="relocation" else page.orphan_actions.button if name=="orphan" else getattr(page,name)
            page.scroll.ensureWidgetVisible(button)
            APP.processEvents()
            bar=page.scroll.verticalScrollBar()
            before=bar.value()
            QTest.mouseClick(button,Qt.LeftButton)
            APP.processEvents()
            self.assertEqual(bar.value(),before,name)
            bar.setValue(bar.maximum())
            APP.processEvents()
            self.assertEqual(bar.value(),bar.maximum())

    def test_maintenance_buttons_keep_scroll_position_and_recover_after_failure(self):
        prefix="modules.main_pages.packet_capture.openwrt.maintenance_actions.QFileDialog."
        for name in ("backup_button","restore_button","reset_button"):
            page,_=self.create_page()
            page.resize(960,420);APP.processEvents()
            button=getattr(page,name)
            page.scroll.ensureWidgetVisible(button);APP.processEvents()
            bar=page.scroll.verticalScrollBar();before=bar.value()
            with patch(prefix+"getSaveFileName",return_value=("C:/before-test.tar.gz","")),patch(
                    prefix+"getOpenFileName",return_value=("C:/source-test.tar.gz","")):
                QTest.mouseClick(button,Qt.LeftButton)
            APP.processEvents()
            self.assertEqual(bar.value(),before,name)
            self.assertFalse(button.isEnabled())
            page._failed("request","Simulated failure");APP.processEvents()
            self.assertTrue(button.isEnabled())
            self.assertFalse(page.maintenance_actions.active)
            self.assertEqual(bar.value(),before,name)

    def test_cancel_file_picker_never_submits_maintenance(self):
        page,_=self.create_page()
        with patch("modules.main_pages.packet_capture.openwrt.maintenance_actions.QFileDialog.getSaveFileName",return_value=("","")):
            page.backup_button.click()
        with patch("modules.main_pages.packet_capture.openwrt.maintenance_actions.QFileDialog.getOpenFileName",return_value=("","")):
            page.restore_button.click()
        page.manager.submit.assert_not_called()

    def test_reset_preview_requires_final_typed_confirmation_and_connection(self):
        page,_=self.create_page()
        page.reset_button.click()
        page.manager.submit.assert_called_once_with("maintenance_preview",kind="reset",source="")
        with patch.object(page.maintenance_actions,"_destination",return_value=("C:/before.tar.gz",None)), patch(
            "modules.main_pages.packet_capture.openwrt.maintenance_actions.MaintenanceReviewDialog") as dialog:
            dialog.return_value.exec.return_value=0
            dialog.return_value.Accepted=1
            page._completed("request",REVIEW)
        self.assertEqual(page.manager.submit.call_count,1)
        page.reset_button.click()
        with patch.object(page.maintenance_actions,"_destination",return_value=("C:/before.tar.gz",None)), patch(
            "modules.main_pages.packet_capture.openwrt.maintenance_actions.MaintenanceReviewDialog") as dialog:
            dialog.return_value.Accepted=1
            def switched():
                page.manager.manager.ssh._client=object()
                return 1
            dialog.return_value.exec.side_effect=switched
            page._completed("request",REVIEW)
        self.assertEqual(page.manager.submit.call_count,2)

    def test_confirm_dialogs_render_and_default_to_no_execution(self):
        output=Path("build/maintenance-review")
        output.mkdir(parents=True,exist_ok=True)
        from modules.dialog_theme import build_base_stylesheet
        for light in (False,True):
            page,_=self.create_page(light)
            with patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet",
                       side_effect=lambda config_manager,scale_manager:build_base_stylesheet(config_manager,
                           theme_name="light.qss" if light else "dark.qss",light_theme=light,scale_manager=scale_manager)):
                for percent in (100,150):
                    from modules.ui_scale_manager import UiScaleManager
                    page.parentWidget().scale_manager=UiScaleManager(percent)
                    for kind in ("reset","restore"):
                        review=dict(REVIEW,operation=kind,source="source.tar.gz",sha256="a"*64,size=1024,entries=2,files=["etc/config/network","etc/shadow"])
                        dialog=MaintenanceReviewDialog(review,"C:/backup/before.tar.gz",page)
                        self.addCleanup(dialog.deleteLater)
                        dialog.show();APP.processEvents()
                        self.assertFalse(dialog.go.isEnabled())
                        self.assertGreater(dialog.warning_text.height(),80)
                        self.assertGreaterEqual(dialog.warning_text.font().pointSize(),round(10*percent/100))
                        dialog.grab().save(str(output/(kind+"-"+str(light)+"-"+str(percent)+".png")))
                        if kind=="reset":
                            dialog.input.setText("wrong")
                            self.assertFalse(dialog.go.isEnabled())
                            dialog.input.setText("\u6062\u590d\u51fa\u5382")
                        else:dialog.trusted.setChecked(True)
                        self.assertTrue(dialog.go.isEnabled())
                        dialog.reject()
