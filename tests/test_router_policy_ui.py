"""Rendered three-column settings, policy confirmation, and real progress contracts."""
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QAbstractButton,QLabel
from tests.test_standalone import APP
from tests.common_settings_fixture import create_page
from tests.test_router_policy import PolicyDevice
from modules.main_pages.packet_capture.openwrt.router_policy_dialogs import InitialPolicyDialog,PolicyReviewDialog,review_text
from modules.main_pages.packet_capture.openwrt.router_task_progress import RouterTaskProgressDialog
from modules.capture_services.openwrt.router_policy_plan import build_plan


class PolicyUiTests(unittest.TestCase):
    create_page=create_page

    def test_no_extra_account_or_boot_status_rows_and_state_lives_on_button(self):
        page,_=self.create_page()
        self.assertFalse(hasattr(page,"account_label"))
        self.assertFalse(hasattr(page.policy_actions,"boot_label"))
        self.assertNotIn("read",page.policy_actions.buttons)
        for state,text in ((True,"\u5df2\u5f00\u542f"),(False,"\u5df2\u5173\u95ed"),(None,"\u72b6\u6001\u672a\u77e5")):
            page.manager.policy_status=dict(boot_state=state);page._sync()
            self.assertEqual(page.policy_actions.buttons["boot"].text(),text)
        self.assertTrue(page.orphan_actions.button.isVisible())

    def test_card_actions_share_a_row_and_align_at_bottom_across_each_grid_row(self):
        page,_=self.create_page()
        buttons=[page.password_button,page.lan_button,page.system_button,page.relocation_actions.button,
                 page.backup_button,page.restore_button,page.reset_button,page.reboot_button,
                 *[page.policy_actions.buttons[k] for k in ("initial","boot","station","ap","homepage")],page.orphan_actions.button]
        rows = {}
        for button in buttons:
            card = next(card for card in page.cards if card.isAncestorOf(button))
            row = page.cards_layout.getItemPosition(page.cards_layout.indexOf(card))[0]
            rows.setdefault(row, []).append(button.mapTo(page,QPoint(0,button.height())).y())
        for edges in rows.values():
            self.assertLessEqual(max(edges)-min(edges),2,edges)
        for card in (page.relocation_card,page.cards[8],page.cards[10],page.cards[11]):
            detail=next(b for b in card.findChildren(QAbstractButton) if b.text()=="\u8be6\u60c5")
            siblings=[b for b in card.findChildren(QAbstractButton) if b is not detail]
            self.assertTrue(any(abs(b.mapTo(card,QPoint()).y()-detail.mapTo(card,QPoint()).y())<=1 for b in siblings))

    def test_backup_opens_nonblocking_progress_and_failure_closes_it(self):
        page,_=self.create_page()
        with patch("modules.main_pages.packet_capture.openwrt.maintenance_actions.QFileDialog.getSaveFileName",return_value=("C:/fixture.tar.gz","")):
            page.backup_button.click()
        self.assertIn("request",page.maintenance_actions.progress_dialogs)
        dialog=page.maintenance_actions.progress_dialogs["request"]
        page.maintenance_actions._progress("request",dict(stage="\u6b63\u5728\u4e0b\u8f7d\u5230\u7535\u8111",completed=25,total=100))
        self.assertEqual(dialog.bar.value(),250);self.assertIn("\u6b63\u5728\u4e0b\u8f7d\u5230\u7535\u8111",dialog.label.text())
        page._failed("request","Fixture failure")
        self.assertFalse(page.maintenance_actions.progress_dialogs)
        self.assertTrue(page.backup_button.isEnabled())

    def test_unknown_or_missing_cleanup_capability_keeps_visible_disabled_entry(self):
        page,_=self.create_page()
        self.assertTrue(page.orphan_actions.button.isVisible())
        self.assertFalse(page.orphan_actions.button.isEnabled())
        self.assertTrue(page.orphan_actions.button.toolTip())

    def test_initial_options_cancel_does_not_submit_preview_or_write(self):
        page,_=self.create_page()
        page.policy_actions.buttons["initial"].click()
        d=PolicyDevice();snapshot=d.policy.read(d.cancel)
        with patch("modules.main_pages.packet_capture.openwrt.router_policy_actions.InitialPolicyDialog") as editor:
            editor.return_value.exec.return_value=0
            page._completed("request",snapshot)
        self.assertEqual(page.manager.submit.call_count,1)
        self.assertTrue(page.policy_actions.buttons["initial"].isEnabled())

    def test_device_switch_during_final_review_never_applies(self):
        page,_=self.create_page()
        identity=page.policy_actions._identity()
        page.policy_actions.pending["review"]=("preview","ap",identity)
        with patch("modules.main_pages.packet_capture.openwrt.router_policy_actions.PolicyReviewDialog") as dialog:
            dialog.return_value.Accepted=1
            def changed():
                page.manager.manager.ssh._client=object();return 1
            dialog.return_value.exec.side_effect=changed
            page._completed("review",dict(kind="ap"))
        page.manager.submit.assert_not_called()

    def test_progress_only_uses_actual_totals_and_cancel_runs_once(self):
        page,_=self.create_page();cancel=Mock()
        dialog=RouterTaskProgressDialog("\u6d4b\u8bd5",cancel,page)
        self.addCleanup(dialog.finish)
        dialog.show();APP.processEvents()
        self.assertEqual((dialog.bar.minimum(),dialog.bar.maximum()),(0,0))
        dialog.update_progress(dict(stage="\u751f\u6210\u4e2d",completed=0,total=0))
        self.assertEqual(dialog.bar.maximum(),0)
        dialog.reject();dialog.reject();cancel.assert_called_once()
        dialog.update_progress(dict(stage="\u63d0\u4ea4\u4e2d",uncancellable=True))
        self.assertFalse(dialog.cancel_button.isEnabled())

    def test_dialogs_render_in_both_themes_and_scales_with_safe_options(self):
        from modules.ui_scale_manager import UiScaleManager
        from modules.dialog_theme import build_base_stylesheet
        from modules.config_manager import ConfigManager
        out=Path("build/common-settings-extension-review/ui");out.mkdir(parents=True,exist_ok=True)
        d=PolicyDevice();snapshot=d.policy.read(d.cancel)
        for light in (False,True):
            page,_=self.create_page(light)
            for percent in (100,150):
                page.parentWidget().scale_manager=UiScaleManager(percent)
                with patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet",
                           side_effect=lambda cm,scale_manager:build_base_stylesheet(cm,light_theme=light,theme_name="light.qss" if light else "dark.qss",scale_manager=scale_manager)):
                    initial=InitialPolicyDialog(snapshot,page)
                    self.assertFalse(initial.checks["rescue"].isEnabled())
                    self.assertNotIn("ipv6",initial.checks)
                    self.assertEqual(initial.password.text(),"luxiaoguan123")
                    initial.show();APP.processEvents()
                    initial.grab().save(str(out/f"initial-{light}-{percent}.png"));initial.reject();initial.deleteLater()
                    plan=build_plan(snapshot,"ap")
                    review=PolicyReviewDialog(plan,page)
                    review.show();APP.processEvents()
                    review.grab().save(str(out/f"review-{light}-{percent}.png"));review.reject();review.deleteLater()
                    progress=RouterTaskProgressDialog("\u5907\u4efd\u8fdb\u5ea6",Mock(),page)
                    progress.update_progress(dict(stage="\u6b63\u5728\u4e0b\u8f7d\u5907\u4efd\u5230\u7535\u8111\u2026\u2026",completed=42,total=100))
                    progress.show();APP.processEvents()
                    progress.grab().save(str(out/f"progress-{light}-{percent}.png"));progress.finish()
            page.grab().save(str(out/f"cards-{light}.png"))

    def test_group_headings_keep_every_existing_card_and_adapt_without_recreating_widgets(self):
        from modules.ui_scale_manager import UiScaleManager
        from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
        from modules.config_manager import ConfigManager
        from router_app.appearance import build_main_stylesheet
        from PySide6.QtWidgets import QFrame
        for light in (False, True):
            page,_=self.create_page(light)
            groups=page.card_groups.groups
            self.assertEqual([len(cards) for title,cards in groups],[4,6,4])
            self.assertEqual({id(c) for title,cards in groups for c in cards},{id(c) for c in page.cards})
            identity=tuple(id(c) for c in page.cards)
            for scale in (100,150):
                owner=page.parentWidget();owner.scale_manager=UiScaleManager(scale)
                owner.scale_manager.publish(owner)
                owner.setStyleSheet(build_main_stylesheet(ConfigManager(),'light.qss' if light else 'dark.qss',owner.scale_manager))
                page.setStyleSheet(CapturePageStyleManager(owner)._build_stylesheet())
                owner.scale_manager.apply_bound_metrics(owner)
                for width in (650,1150):
                    page.resize(width,900)
                    for _ in range(4):APP.processEvents()
                    previous=-1
                    for header,(title,cards) in zip(page.card_groups.headers,groups):
                        row,col,rs,cs=page.cards_layout.getItemPosition(page.cards_layout.indexOf(header))
                        self.assertGreater(row,previous)
                        self.assertEqual(cs,page._card_columns)
                        positions=[page.cards_layout.getItemPosition(page.cards_layout.indexOf(c))[0] for c in cards]
                        self.assertTrue(all(pos>row for pos in positions))
                        previous=max(positions)
                    self.assertEqual(tuple(id(c) for c in page.cards),identity)
                path=Path('build/review/common-settings-groups');path.mkdir(parents=True,exist_ok=True)
                self.assertTrue(page.grab().save(str(path/(('light' if light else 'dark')+'-'+str(scale)+'.png'))))
            page.hide()
