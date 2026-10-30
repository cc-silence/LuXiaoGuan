"""Integration contracts across real maintenance results and shared feedback routes."""
import tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from tests import test_router_backup as fixtures
from tests.common_settings_fixture import create_page
from tests.test_signal_actions_ui import PendingSetup
from modules.capture_services.openwrt.router_backup import RouterBackupManager


class CommonSettingsSecondAuditTests(unittest.TestCase):
    create_page=create_page

    def test_actual_restore_results_reach_info_panel_without_modal_for_all_outcomes(self):
        for uncertain in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                source=Path(folder)/'source.tar.gz';source.write_bytes(fixtures.archive_bytes())
                device=fixtures.Device(folder);device.apply_error=uncertain
                manager=RouterBackupManager(device.manager);event=threading.Event()
                review=manager.preview('restore',source,event)
                result=manager.execute(review,None,None,event,Mock())
                self.assertEqual(result.get('operation'),'restore')
                page,_=self.create_page(with_info=True);actions=page.maintenance_actions
                actions.pending['done']=('done',None)
                with patch.object(page.info_panel,'append_info') as append,patch(
                    'modules.main_pages.packet_capture.openwrt.maintenance_actions.MessageDialog') as modal:
                    actions.completed('done',result)
                modal.assert_not_called()
                self.assertEqual(append.call_args.args[0],result['note'])
                self.assertEqual(append.call_args.args[1],'warning' if uncertain else 'info')

    def test_rejected_preview_start_is_visible_in_info_panel_for_both_shared_actions(self):
        for kind in ('relocation','orphan'):
            setup=PendingSetup();setup.relocation_preview=Mock(return_value=False);setup.orphan_preview=Mock(return_value=False)
            page,_=self.create_page(setup=setup,with_info=True)
            page._message('previous message')
            with patch.object(page.info_panel,'append_info') as append:
                getattr(page,kind+'_actions').review()
            append.assert_called_once()
            self.assertFalse(page.status_label.text())

    def test_shared_feedback_retains_legacy_signal_page_status_label(self):
        from types import SimpleNamespace as NS
        from modules.main_pages.packet_capture.openwrt.page_feedback import publish_page_message
        page=NS(status_label=Mock())
        publish_page_message(page,'unchanged signal-page feedback')
        page.status_label.setText.assert_called_once_with('unchanged signal-page feedback')

    def test_shared_preview_result_dialogs_release_resources_after_close(self):
        from types import SimpleNamespace as NS
        from modules.main_pages.packet_capture.openwrt.signal_relocation_actions import RelocationActions
        from modules.main_pages.packet_capture.openwrt.signal_orphan_actions import OrphanInterfaceActions
        from modules.capture_services.openwrt.wireless_setup_models import WirelessTaskResult
        for targets in ([],[{}]):
            identity=object()
            context=NS(_waiting=True,_preview_preserve=True,_identity=identity,
                       service=NS(_connection_identity=lambda:identity),
                       page=NS(service=NS(snapshot=NS(connected=True))),
                       _sync=Mock(),summary=Mock(return_value='review'))
            name='ConfirmDialog' if targets else 'MessageDialog'
            with patch('modules.main_pages.packet_capture.openwrt.signal_relocation_actions.'+name) as factory:
                factory.return_value.exec.return_value=0;factory.Accepted=1
                RelocationActions._completed(context,WirelessTaskResult('move_preview',dict(targets=targets,preserve_access_points=True,access_points=[])))
            factory.return_value.deleteLater.assert_called_once_with()
        context=NS(_waiting=True,page=NS(),_sync=Mock(),_current=Mock(return_value=True))
        with patch('modules.main_pages.packet_capture.openwrt.signal_orphan_actions.MessageDialog') as factory:
            OrphanInterfaceActions._completed(context,WirelessTaskResult('orphan_preview',dict(rows=[])))
        factory.return_value.deleteLater.assert_called_once_with()
