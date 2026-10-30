"""Explicit hotspot removal is preview-bound and uses the same rollback path."""
import copy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from tests import test_relocation_access_points as fixtures
from tests.test_standalone import APP
from tests.test_signal_actions_ui import PendingSetup
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_setup_models import WirelessTaskResult
from modules.main_pages.packet_capture.openwrt.signal_relocation_actions import relocation_identity_editor
from modules.main_pages.packet_capture.openwrt.ap_batch_dialog import AccessPointBatchDialog


class DiscardHotspotTests(unittest.TestCase):
    def setUp(self):
        fixtures.PreservedHotspotTests.setUp(self)

    def test_discard_preview_is_read_only_then_removes_hotspots_and_owned_networks(self):
        self.device.config['wireless']['home']['network']='guest'
        self.device.config['network']['guest']={'.type':'interface','proto':'static','ipaddr':'192.168.9.1'}
        before=copy.deepcopy(self.device.config)
        review=self.manager.preview(self.cancel,preserve_access_points=False)
        self.assertFalse(review['preserve_access_points'])
        self.assertEqual([r['section'] for r in review['rows']],['home'])
        self.assertEqual(review['access_points'],[])
        self.assertEqual(review['plan']['access_points'],[])
        self.assertIn('guest',review['removed'])
        self.assertEqual(self.device.config,before)
        result=self.manager.apply(review,self.cancel,Mock())
        self.assertNotIn('home',self.device.config['wireless'])
        self.assertNotIn('guest',self.device.config['network'])
        self.assertEqual(result['removed_access_points'],1)
        self.assertFalse(result['preserve_access_points'])
        self.assertEqual(self.device.config['network']['lan']['ipaddr'],'192.168.8.1')

    def test_stale_preserve_preview_cannot_be_relabelled_as_delete(self):
        review=self.manager.preview(self.cancel)
        review['preserve_access_points']=False
        review['access_points']=[]
        review['plan']['access_points']=[]
        with self.assertRaises(SignalError) as caught:self.manager.apply(review,self.cancel,Mock())
        self.assertEqual(caught.exception.code,'changed')
        self.assertEqual(self.device.config,self.device.original)
        self.assertNotIn(('uci','apply'),self.device.calls)

    def test_discard_status_failure_restores_hotspot_name_and_password_exactly(self):
        review=self.manager.preview(self.cancel,preserve_access_points=False)
        original=self.manager._call
        def call(action,*args,**kwargs):
            if action=='move_status':raise SignalError('transport','Synthetic connection loss')
            return original(action,*args,**kwargs)
        self.manager._call=call
        with self.assertRaises(SignalError):self.manager.apply(review,self.cancel,Mock())
        self.assertEqual(self.device.config,self.device.original)
        self.assertFalse(self.device.sessions)

    def test_discard_does_not_require_ap_identity_option_support(self):
        original=self.device.call
        def call(obj,method,args):
            value,error=original(obj,method,args)
            if obj=='network.wireless' and method=='get_validate':
                schema=self.device.unwrap(value)
                schema['radio7']['interface'].pop('macaddr',None)
                value=self.device.wrap(schema)
            return value,error
        self.device.lua.globals().bridge_call=call
        review=self.manager.preview(self.cancel,preserve_access_points=False)
        self.manager.apply(review,self.cancel,Mock())
        self.assertNotIn('home',self.device.config['wireless'])

    def test_discard_button_ignores_rename_validation_and_existing_editor_has_no_button(self):
        review=self.manager.preview(self.cancel)
        dialog=relocation_identity_editor(review,None);self.addCleanup(dialog.deleteLater)
        dialog.rows['home'][0].setText('')
        dialog.show();APP.processEvents()
        self.assertTrue(dialog.discard_button.isVisible())
        dialog.discard_button.click()
        self.assertEqual(dialog.result(),dialog.Discarded)
        self.assertEqual(self.device.config,self.device.original)
        from modules.capture_services.openwrt.relocation_access_points import access_point_snapshot
        existing=AccessPointBatchDialog(access_point_snapshot(review))
        self.addCleanup(existing.deleteLater)
        self.assertIsNone(existing.discard_button)


class DiscardUiFlowTests(unittest.TestCase):
    from tests.common_settings_fixture import create_page

    def test_first_button_repreviews_then_requires_explicit_final_confirmation(self):
        base=fixtures.PreservedHotspotTests();base.setUp()
        setup=PendingSetup();setup.relocation_preview=Mock(return_value=True);setup.relocate=Mock(return_value=True)
        page,service=self.create_page(False,setup)
        keep=base.manager.preview(base.cancel)
        remove=base.manager.preview(base.cancel,preserve_access_points=False)
        page.relocation_actions.button.click()
        module='modules.main_pages.packet_capture.openwrt.signal_relocation_actions.'
        with patch(module+'AccessPointBatchDialog') as editor, patch(module+'ConfirmDialog') as confirm:
            editor.return_value.Accepted=1;editor.return_value.Discarded=2
            editor.return_value.exec.return_value=2
            setup.completed.emit(WirelessTaskResult('move_preview',keep))
            setup.relocation_preview.assert_called_with(preserve_access_points=False)
            confirm.assert_not_called();setup.relocate.assert_not_called()
            self.assertTrue(page.relocation_actions._waiting)
            confirm.Accepted=1;confirm.return_value.exec.return_value=0
            setup.completed.emit(WirelessTaskResult('move_preview',remove))
            self.assertIn('Home',confirm.call_args.args[1])
            self.assertIn('\u5c06\u88ab\u5220\u9664',confirm.call_args.args[1])
            setup.relocate.assert_not_called()
            editor.return_value.exec.assert_called_once()
        # A fresh request and final confirmation execute precisely the delete preview.
        page.relocation_actions.button.click()
        with patch(module+'AccessPointBatchDialog') as editor, patch(module+'ConfirmDialog') as confirm:
            editor.return_value.Accepted=1;editor.return_value.Discarded=2
            editor.return_value.exec.return_value=2
            setup.completed.emit(WirelessTaskResult('move_preview',keep))
            confirm.Accepted=1;confirm.return_value.exec.return_value=1
            setup.completed.emit(WirelessTaskResult('move_preview',remove))
            setup.relocate.assert_called_once_with(remove)


if __name__=='__main__':unittest.main()
