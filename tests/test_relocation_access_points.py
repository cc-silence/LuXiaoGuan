"""Relocation retains downstream access and binds edited names to real Lua apply."""
import copy
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QLabel, QFrame
from tests.test_standalone import APP
from tests.test_relocation import MovingDevice
from modules.capture_services.openwrt.relocation_manager import RelocationManager
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.main_pages.packet_capture.openwrt.signal_relocation_actions import relocation_identity_editor
from modules.main_pages.packet_capture.openwrt.ap_batch_dialog import AccessPointBatchDialog
from modules.ui_scale_manager import UiScaleManager


class PreservedHotspotTests(unittest.TestCase):
    def setUp(self):
        self.device = MovingDevice()
        self.manager = RelocationManager(WirelessSetupManager(self.device))
        self.cancel = threading.Event()
        # Preserve native Lua error tables when Python supplies fixture rows.
        self.device.lua.execute("""
local cursor=require("uci").cursor()
local foreach=cursor.foreach
cursor.foreach=function(self,config,kind,callback)
 local problem
 foreach(self,config,kind,function(row)
  if problem then return end
  local ok,err=pcall(callback,row)
  if not ok then problem=err end
 end)
 if problem then error(problem,0) end
end
""")

    def add_guest(self):
        self.device.config['wireless']['guest'] = dict(self.device.config['wireless']['home'],
            ssid='Guest', key='guest-password', hidden='1', disabled='1')

    def test_preview_separates_upstream_from_retained_hotspots_without_passwords(self):
        self.add_guest()
        self.device.config['wireless']['uplink'] = dict(self.device.config['wireless']['home'],
            mode='sta', ssid='Old upstream', network='wwan', key='upstream-secret')
        self.device.config['network']['wwan'] = {'.type':'interface','proto':'dhcp'}
        before = copy.deepcopy(self.device.config)
        review = self.manager.preview(self.cancel)
        self.assertEqual([row['section'] for row in review['rows']], ['uplink'])
        self.assertEqual([row['section'] for row in review['access_points']], ['guest','home'])
        self.assertEqual(len(review['plan']['access_points']), 2)
        self.assertNotIn('guest-password', str(review))
        self.assertNotIn('upstream-secret', str(review))
        macs = review['plan']['macs'] + [r['mac'].lower() for r in review['plan']['access_points']]
        self.assertEqual(len(set(macs)), len(macs))
        self.assertTrue(all(int(mac[:2],16) & 3 == 2 for mac in macs))
        self.assertEqual(self.device.config, before)

    def test_unicode_name_and_disabled_hidden_hotspot_preserve_every_other_option(self):
        self.add_guest()
        before = copy.deepcopy(self.device.config['wireless'])
        review = self.manager.preview(self.cancel)
        for i,item in enumerate(review['plan']['access_points']):
            item['name'] = 'New '+str(i)+' \u70ed\u70b9'
        result = self.manager.apply(review, self.cancel, Mock())
        self.assertEqual(len(result['access_points']),2)
        for item in review['plan']['access_points']:
            expected = dict(before[item['section']], ssid=item['name'], macaddr=item['mac'])
            self.assertEqual(self.device.config['wireless'][item['section']], expected)
        self.assertEqual(self.device.config['wireless']['radio7'], before['radio7'])

    def test_shared_downstream_network_and_firewall_membership_are_retained(self):
        self.device.config['wireless']['home']['network'] = 'wwan9'
        self.device.config['network']['wwan9'] = {'.type':'interface','proto':'static',
            'ipaddr':'192.168.9.1','netmask':'255.255.255.0'}
        self.device.config['wireless']['up'] = {'.type':'wifi-iface','mode':'sta','ssid':'Old',
            'network':'wwan9','device':'radio7'}
        self.device.config['firewall']['zone_lan']['network'].append('wwan9')
        before = copy.deepcopy(self.device.config['network']['wwan9'])
        review = self.manager.preview(self.cancel)
        self.assertNotIn('wwan9',review['removed'])
        self.manager.apply(review,self.cancel,Mock())
        self.assertEqual(self.device.config['network']['wwan9'],before)
        self.assertIn('wwan9',self.device.config['firewall']['zone_lan']['network'])
        self.assertNotIn('up',self.device.config['wireless'])

    def test_input_validation_rejects_missing_hotspot_duplicate_mac_and_invalid_names(self):
        self.add_guest()
        good = self.manager.preview(self.cancel)
        before = copy.deepcopy(self.device.config)
        for case in ('missing','duplicate','wired_mac','long','control'):
            with self.subTest(case=case):
                review=copy.deepcopy(good)
                items=review['plan']['access_points']
                if case=='missing':items.pop()
                elif case=='duplicate':items[1]['mac']=items[0]['mac']
                elif case=='wired_mac':items[0]['mac']=review['addresses'][0]
                elif case=='long':items[0]['name']='\u70ed'*11
                else:items[0]['name']='Name'+chr(127)
                with self.assertRaises(SignalError):self.manager.apply(review,self.cancel,Mock())
                self.assertEqual(self.device.config,before)
                self.assertFalse(self.device.sessions)

    def test_unsupported_mac_capability_refuses_before_any_write(self):
        original=self.device.call
        def call(obj,method,args):
            value,error=original(obj,method,args)
            if obj=='network.wireless' and method=='get_validate':
                schema = self.device.unwrap(value)
                schema['radio7']['interface'].pop('macaddr', None)
                value = self.device.wrap(schema)
            return value,error
        self.device.lua.globals().bridge_call=call
        with self.assertRaises(SignalError) as caught:self.manager.preview(self.cancel)
        self.assertEqual(caught.exception.code,'move_ap_support')
        self.assertNotIn(('uci','set'),self.device.calls)
        self.assertEqual(self.device.config,self.device.original)

    def test_runtime_wrong_name_address_or_missing_hotspot_rolls_back(self):
        for fault in ('name','mac','missing'):
            with self.subTest(fault=fault):
                self.setUp()
                original_call=self.device.call
                staged=[False]
                def call(obj,method,args):
                    value,error=original_call(obj,method,args)
                    if staged[0]:
                        if fault=='name' and obj=='hostapd.iface-home':
                            value['ssid']='Unchanged old name'
                        if fault=='mac' and obj=='network.device' and self.device.unwrap(args).get('name')=='iface-home':
                            value['macaddr']='02:ff:ff:ff:ff:01'
                        if fault=='missing' and obj=='network.wireless' and method=='status':
                            value['radio7']['interfaces']=self.device.wrap([])
                    return value,error
                self.device.call=call
                self.device.lua.globals().bridge_call=call
                review=self.manager.preview(self.cancel)
                original=self.manager._call
                def checked(action,*args,**kwargs):
                    result=original(action,*args,**kwargs)
                    if action=='move_stage':staged[0]=True
                    if action=='move_status':
                        self.assertFalse(result['ready'])
                        staged[0]=False
                        raise SignalError('unconfirmed','Runtime AP identity differs')
                    return result
                self.manager._call=checked
                with self.assertRaises(SignalError):self.manager.apply(review,self.cancel,Mock())
                self.assertEqual(self.device.config,self.device.original)
                self.assertFalse(self.device.sessions)

    def test_real_dialog_has_old_names_editable_random_names_and_hidden_generated_macs(self):
        self.add_guest()
        review=self.manager.preview(self.cancel)
        for light in (False,True):
            for scale in (100,150):
                parent=QFrame();parent.light_theme=light;parent.scale_manager=UiScaleManager(scale)
                self.addCleanup(parent.deleteLater)
                # Use the same complete theme factory as the application's dialogs.
                from modules.config_manager import ConfigManager
                from modules.dialog_theme import build_base_stylesheet
                style=build_base_stylesheet(ConfigManager(),light_theme=light,scale_manager=parent.scale_manager)
                with patch('modules.common_widgets.common_dialogs.build_current_base_stylesheet',return_value=style):
                    dialog=relocation_identity_editor(review,parent)
                self.addCleanup(dialog.deleteLater)
                dialog.show();APP.processEvents()
                self.assertTrue(dialog.discard_button.isVisible())
                self.assertGreaterEqual(dialog.discard_button.width(),
                    dialog.discard_button.fontMetrics().horizontalAdvance(dialog.discard_button.text()))
                self.assertLess(dialog.discard_button.geometry().right(),
                    dialog.continue_button.geometry().left())
                self.assertTrue(any('Home' in w.text() for w in dialog.findChildren(QLabel)))
                before=dialog.values()
                name,mac,random=dialog.rows['home']
                self.assertFalse(name.isReadOnly())
                self.assertTrue(mac.isReadOnly());self.assertFalse(mac.isVisible())
                random.click();APP.processEvents()
                self.assertNotEqual(name.text(),next(r['name'] for r in before['identities'] if r['section']=='home'))
                name.setText('Manual \u70ed\u70b9')
                dialog.rows['guest'][2].click()
                self.assertEqual(name.text(),'Manual \u70ed\u70b9')
                self.assertEqual(len(dialog.values()['identities']),2)
                self.assertGreaterEqual(random.width(),random.fontMetrics().horizontalAdvance(random.text()))
                output=Path('build/review/relocation-preserve-hotspots');output.mkdir(parents=True,exist_ok=True)
                self.assertTrue(dialog.grab().save(str(output/(('light' if light else 'dark')+'-'+str(scale)+'.png'))))
                dialog.reject();APP.processEvents()


class RelocationEditorFlowTests(unittest.TestCase):
    from tests.common_settings_fixture import create_page

    def create(self):
        from tests.test_signal_actions_ui import PendingSetup
        setup=PendingSetup()
        setup.relocation_preview=Mock(return_value=True)
        setup.relocate=Mock(return_value=True)
        page,service=self.create_page(False,setup)
        review=RelocationManager(WirelessSetupManager(MovingDevice())).preview(threading.Event())
        return page,service,setup,review

    def test_first_editor_cancel_never_opens_confirmation_or_changes_router(self):
        from modules.capture_services.openwrt.wireless_setup_models import WirelessTaskResult
        page,service,setup,review=self.create()
        page.relocation_actions.button.click()
        module='modules.main_pages.packet_capture.openwrt.signal_relocation_actions.'
        with patch(module+'AccessPointBatchDialog') as editor, patch(module+'ConfirmDialog') as confirm:
            editor.return_value.Accepted=1
            editor.return_value.exec.return_value=0
            setup.completed.emit(WirelessTaskResult('move_preview',review))
            confirm.assert_not_called()
            editor.return_value.deleteLater.assert_called_once()
        setup.relocate.assert_not_called()
        self.assertTrue(page.relocation_actions.button.isEnabled())

    def test_edited_name_is_shown_in_confirmation_and_sent_without_mutating_preview(self):
        from modules.capture_services.openwrt.wireless_setup_models import WirelessTaskResult
        from modules.common_widgets.common_dialogs import ConfirmDialog
        page,service,setup,review=self.create()
        before=copy.deepcopy(review)
        edits=copy.deepcopy(review['plan']['access_points'])
        edits[0]['name']='Manual \u70ed\u70b9'
        page.relocation_actions.button.click()
        module='modules.main_pages.packet_capture.openwrt.signal_relocation_actions.'
        with patch(module+'AccessPointBatchDialog') as editor, patch(module+'ConfirmDialog') as confirm:
            editor.return_value.Accepted=1
            editor.return_value.exec.return_value=1
            editor.return_value.values.return_value={'identities':edits}
            confirm.Accepted=ConfirmDialog.Accepted
            confirm.return_value.exec.return_value=ConfirmDialog.Accepted
            setup.completed.emit(WirelessTaskResult('move_preview',review))
            self.assertFalse(editor.call_args.kwargs['mac_editable'])
            self.assertIn('Manual \u70ed\u70b9',confirm.call_args.args[1])
        approved=setup.relocate.call_args.args[0]
        self.assertEqual(approved['plan']['access_points'][0]['name'],'Manual \u70ed\u70b9')
        self.assertEqual(review,before)

    def test_connection_change_in_editor_prevents_final_confirmation_and_apply(self):
        from modules.capture_services.openwrt.wireless_setup_models import WirelessTaskResult
        page,service,setup,review=self.create()
        page.relocation_actions.button.click()
        module='modules.main_pages.packet_capture.openwrt.signal_relocation_actions.'
        with patch(module+'AccessPointBatchDialog') as editor, patch(module+'ConfirmDialog') as confirm:
            editor.return_value.Accepted=1
            def changed():
                setup.identity=object()
                return 1
            editor.return_value.exec.side_effect=changed
            editor.return_value.values.return_value={'identities':copy.deepcopy(review['plan']['access_points'])}
            setup.completed.emit(WirelessTaskResult('move_preview',review))
            confirm.assert_not_called()
        setup.relocate.assert_not_called()


if __name__=='__main__':unittest.main()
