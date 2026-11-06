"""Restore proceeds directly after final consent, without a backup picker."""
import tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from tests import test_router_backup as fixture
from tests.common_settings_fixture import create_page
from modules.capture_services.openwrt.router_backup import RouterBackupManager
from modules.main_pages.packet_capture.openwrt.maintenance_review_dialog import MaintenanceReviewDialog


class OptionalRestoreTests(unittest.TestCase):
    create_page=create_page
    def test_restore_without_current_backup_still_checks_and_restores_selected_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.tar.gz';source.write_bytes(fixture.archive_bytes())
            device=fixture.Device(folder);manager=RouterBackupManager(device.manager);event=threading.Event()
            review=manager.preview('restore',source,event)
            result=manager.execute(review,None,None,event,Mock())
            names=[name for name,_ in device.commands]
            self.assertNotIn('maintenance_backup',names)
            self.assertIn('maintenance_apply',names);self.assertIn('maintenance_reboot',names)
            self.assertIn('\u672a\u751f\u6210',result['note'])
            self.assertTrue(source.exists())
            result = manager.execute(manager.preview('reset','',event),None,None,event,Mock())
            self.assertEqual(result['operation'], 'reset')

    def test_restore_skips_backup_picker_and_proceeds_to_final_confirmation(self):
        page,_=self.create_page();actions=page.maintenance_actions
        review=dict(operation='restore',model='Router',version='23.05',source='source.tar.gz',files=['etc/config/network'],entries=1,size=100)
        actions.pending['preview']=('preview',actions._identity())
        with patch.object(actions,'_destination',side_effect=AssertionError('Restore must not request a backup')),patch(
                'modules.main_pages.packet_capture.openwrt.maintenance_actions.MaintenanceReviewDialog') as dialog:
            dialog.return_value.Accepted=1;dialog.return_value.exec.return_value=1;dialog.return_value.backup_requested=False
            self.assertTrue(actions.completed('preview',review))
        dialog.assert_called_once_with(review,None,page)
        page.manager.submit.assert_called_once_with('maintenance_apply',review=review,backup_path=None,backup_expected=None)

    def test_final_cancel_still_stops_and_ui_explains_no_extra_backup(self):
        page,_=self.create_page();review=dict(operation='restore',model='Router',version='23.05',files=[],entries=1,size=100)
        dialog=MaintenanceReviewDialog(review,None,page);self.addCleanup(dialog.deleteLater)
        self.assertIn('\u4e0d\u751f\u6210',dialog.warning_text.toPlainText())
        self.assertNotIn('None',dialog.warning_text.toPlainText())
        self.assertIn('\u5907\u4efd', dialog.go.text())
        self.assertIn('\u76f4\u63a5', dialog.direct_button.text())
        self.assertTrue(dialog.go.isEnabled())
        actions=page.maintenance_actions;actions.pending['preview']=('preview',actions._identity())
        with patch.object(actions,'_destination',side_effect=AssertionError('Restore must not request a backup')),patch(
                'modules.main_pages.packet_capture.openwrt.maintenance_actions.MaintenanceReviewDialog') as final:
            final.return_value.Accepted=1;final.return_value.exec.return_value=0
            actions.completed('preview',review)
        page.manager.submit.assert_not_called()

    def test_boot_toggle_has_only_direct_apply_and_cancel(self):
        from modules.main_pages.packet_capture.openwrt.router_policy_dialogs import PolicyReviewDialog
        for light in (False, True):
            page,_=self.create_page(light)
            for enabled in (False, True):
                dialog=PolicyReviewDialog(dict(kind="boot",boot_enabled=enabled),page)
                self.addCleanup(dialog.deleteLater)
                dialog.show()
                from tests.common_settings_fixture import APP
                APP.processEvents()
                self.assertFalse(hasattr(dialog,"backup_button"))
                self.assertTrue(dialog.apply_button.isEnabled())
                self.assertFalse(dialog.backup_requested)
                output=Path("build/maintenance-review")
                output.mkdir(parents=True,exist_ok=True)
                dialog.grab().save(str(output/("boot-"+str(light)+"-"+str(enabled)+".png")))
                dialog.apply_button.click()
                self.assertEqual(dialog.result(),dialog.Accepted)
                self.assertFalse(dialog.backup_requested)
