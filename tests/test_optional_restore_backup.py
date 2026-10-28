"""A cancelled optional backup picker skips backup, not restoration or final consent."""
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
            with self.assertRaises(ValueError):
                manager.execute(manager.preview('reset','',event),None,None,event,Mock())

    def test_cancel_extra_backup_continues_to_final_restore_confirmation(self):
        page,_=self.create_page();actions=page.maintenance_actions
        review=dict(operation='restore',model='Router',version='23.05',source='source.tar.gz',files=['etc/config/network'],entries=1,size=100)
        actions.pending['preview']=('preview',actions._identity())
        with patch.object(actions,'_destination',return_value=None),patch(
                'modules.main_pages.packet_capture.openwrt.maintenance_actions.MaintenanceReviewDialog') as dialog:
            dialog.return_value.Accepted=1;dialog.return_value.exec.return_value=1
            self.assertTrue(actions.completed('preview',review))
        dialog.assert_called_once_with(review,None,page)
        page.manager.submit.assert_called_once_with('maintenance_apply',review=review,backup_path=None,backup_expected=None)

    def test_final_cancel_still_stops_and_ui_explains_no_extra_backup(self):
        page,_=self.create_page();review=dict(operation='restore',model='Router',version='23.05',files=[],entries=1,size=100)
        dialog=MaintenanceReviewDialog(review,None,page);self.addCleanup(dialog.deleteLater)
        self.assertIn('\u4e0d\u751f\u6210',dialog.warning_text.toPlainText())
        self.assertNotIn('None',dialog.warning_text.toPlainText())
        self.assertEqual(dialog.go.text(),'\u6062\u590d\u5e76\u91cd\u542f')
        self.assertFalse(dialog.go.isEnabled())
        actions=page.maintenance_actions;actions.pending['preview']=('preview',actions._identity())
        with patch.object(actions,'_destination',return_value=None),patch(
                'modules.main_pages.packet_capture.openwrt.maintenance_actions.MaintenanceReviewDialog') as final:
            final.return_value.Accepted=1;final.return_value.exec.return_value=0
            actions.completed('preview',review)
        page.manager.submit.assert_not_called()
