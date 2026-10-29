"""October 28 common-settings regressions; no real router or adapter writes."""
import copy
import json
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from tests.common_settings_fixture import create_page
from tests.test_router_policy import PolicyDevice
from tests.test_shared_station_identity import device_with_group
from tests.test_lan_client_recovery import adapter,review,NEW
from modules.capture_services.lan_client_recovery import RenewalGate,client_plan
from modules.capture_services.openwrt.router_policy_plan import build_plan
from modules.main_pages.packet_capture.openwrt.router_password_dialog import RouterPasswordDialog
from modules.main_pages.packet_capture.openwrt.router_homepage_dialog import RouterHomepageDialog
from modules.ui_scale_manager import UiScaleManager
from modules.dialog_theme import build_base_stylesheet
from modules.config_manager import ConfigManager


def apply_batch(device):
    snapshot=device.policy.read(device.cancel)
    plan=build_plan(snapshot,'station')
    device.policy._transaction(snapshot,plan['operations'],device.cancel)
    return plan


class StationReplacementTests(unittest.TestCase):
    def test_repeated_replacement_ignores_stale_or_corrupt_records_and_keeps_unrelated_settings(self):
        for legacy in (True,False):
            for grouped in (True,False):
                with self.subTest(legacy=legacy,grouped=grouped):
                    d=device_with_group(legacy) if grouped else PolicyDevice(legacy)
                    d.config['wireless']['station']['rm_identity_backup']='invalid-json'
                    before=copy.deepcopy(d.config)
                    plan=apply_batch(d)
                    self.assertTrue(all(op['action']=='station_identity' for op in plan['operations']))
                    for entry in plan['identities']:
                        row=d.config['wireless'][entry['section']]
                        self.assertNotIn('rm_identity_backup',row)
                        for field in ('ssid','key','disabled','network','device'):
                            self.assertEqual(row.get(field),before['wireless'][entry['section']].get(field))
                    self.assertEqual(d.config['network']['lan'],before['network']['lan'])
                    self.assertEqual(d.config['firewall'],before['firewall'])
                    self.assertEqual(d.config['wireless']['home'],before['wireless']['home'])
                    macs=[row['mac'] for row in plan['identities']]
                    self.assertEqual(len(set(macs)),len(macs))
                    d.config['network']['wwan']['hostname']='ChangedAfterLastOperation'
                    next_plan=apply_batch(d)
                    self.assertNotEqual(next_plan['identities'][0]['mac'],macs[0])

    def test_missing_member_or_duplicate_addresses_leave_all_configuration_untouched(self):
        for duplicate in (True,False):
            d=device_with_group();snapshot=d.policy.read(d.cancel)
            plan=build_plan(snapshot,'station');members=plan['operations'][0]['members']
            if duplicate:members[1]['macaddr']=members[0]['macaddr']
            else:members.pop()
            before=copy.deepcopy(d.config)
            with self.assertRaises(RuntimeError):d.policy._transaction(snapshot,plan['operations'],d.cancel)
            self.assertEqual(d.config,before)
            self.assertFalse(d.sessions)

    def test_change_after_preview_is_rejected(self):
        d=device_with_group();plan=d.policy.preview('station',{},d.cancel)
        d.files['/etc/config/wireless']+='\n'
        before=copy.deepcopy(d.config)
        with self.assertRaises(RuntimeError):d.policy.apply(plan,None,None,d.cancel,Mock())
        self.assertEqual(d.config,before)

    def test_full_checked_apply_confirms_runtime_without_restore_ledger(self):
        for legacy in (True,False):
            d=PolicyDevice(legacy);plan=d.policy.preview('station',{},d.cancel)
            result=d.policy.apply(plan,None,None,d.cancel,Mock())
            self.assertTrue(result['applied'])
            self.assertNotIn('rm_identity_backup',d.config['wireless']['station'])
            self.assertIn(('uci','confirm'),d.calls)


class FeedbackUiTests(unittest.TestCase):
    create_page=create_page

    def test_homepage_goes_from_background_check_to_apply_without_review_popup(self):
        page,_=self.create_page();actions=page.policy_actions
        actions.pending['preview']=('preview','homepage',actions._identity())
        result=dict(kind='homepage',files=[dict(path='/www/index.html')])
        with patch('modules.main_pages.packet_capture.openwrt.router_policy_actions.PolicyReviewDialog') as dialog:
            actions.completed('preview',result)
        dialog.assert_not_called()
        self.assertEqual(page.manager.submit.call_args.args,('policy_apply',))
        self.assertIs(page.manager.submit.call_args.kwargs['review'],result)
        self.assertIsNone(page.manager.submit.call_args.kwargs['backup_path'])

    def test_lan_completion_opens_independent_recovery_result(self):
        page,_=self.create_page();actions=page.config_actions
        ssh=page.manager.manager.ssh
        actions.pending['apply']=('network',ssh._client,ssh.host_fingerprint,'save')
        gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90)
        page.capture_service.configurationProgressChanged.emit('apply',dict(configuration_apply=True,network_recovery=gate.payload()))
        gate.close()
        result=dict(note='Applied',applied=True,checked_apply=True)
        path='modules.main_pages.packet_capture.openwrt.network_recovery_result_dialog.NetworkRecoveryResultDialog'
        with patch(path) as dialog:
            actions.completed('apply',result)
        self.assertEqual(dialog.call_count,1)
        self.assertEqual(dialog.call_args.args[1]['new_addresses'],['192.168.22.1/24'])
        self.assertFalse(actions.recovery_plans)

    def test_recovery_result_stays_open_and_reset_requires_readiness_confirmation(self):
        from types import SimpleNamespace as NS
        from tests.test_factory_reset_recovery import RecoveryOwner
        from modules.capture_services.lan_recovery_service import LanRecoveryService
        from modules.main_pages.packet_capture.openwrt.network_recovery_result_dialog import NetworkRecoveryResultDialog
        from modules.main_pages.packet_capture.openwrt.factory_reset_result_dialog import FactoryResetResultDialog
        output=Path('build/feedback-1028-review');output.mkdir(parents=True,exist_ok=True)
        for light in (True,False):
            parent=QWidget();parent.scale_manager=UiScaleManager(100)
            self.addCleanup(parent.deleteLater)
            style=build_base_stylesheet(ConfigManager(),light_theme=light,
                theme_name='light.qss' if light else 'dark.qss',scale_manager=parent.scale_manager)
            owner=RecoveryOwner(NS(),NS());owner.lan_recovery=LanRecoveryService(owner)
            self.addCleanup(owner.deleteLater)
            with patch('modules.common_widgets.common_dialogs.build_current_base_stylesheet',return_value=style):
                dialog=NetworkRecoveryResultDialog(dict(note='LAN applied',recovery_title='LAN'),client_plan(review(adapter()),NEW),owner,parent)
                reset=FactoryResetResultDialog(dict(note='Reset submitted'),dict(adapters=[adapter()]),owner,parent)
            self.addCleanup(dialog.deleteLater);self.addCleanup(reset.deleteLater)
            dialog.show();APP.processEvents()
            self.assertTrue(dialog.panel.button.isEnabled())
            self.assertTrue(dialog.isVisible())
            dialog.grab().save(str(output/('lan-'+str(light)+'.png')))
            dialog.accept();self.assertFalse(owner.lan_recovery.offers.keys()-{reset.offer})
            reset.show();APP.processEvents()
            self.assertFalse(reset.panel.button.isEnabled())
            reset.grab().save(str(output/('reset-'+str(light)+'.png')))
            reset.ready.setChecked(True);self.assertTrue(reset.panel.button.isEnabled())
            reset.reject();self.assertFalse(owner.lan_recovery.offers)

    def test_error_draws_in_both_themes_and_clears_on_user_edit(self):
        output=Path('build/feedback-1028-review');output.mkdir(parents=True,exist_ok=True)
        for light in (True,False):
            for scale in (100,150):
                parent=QWidget();parent.scale_manager=UiScaleManager(scale)
                self.addCleanup(parent.deleteLater)
                style=build_base_stylesheet(ConfigManager(),light_theme=light,
                    theme_name='light.qss' if light else 'dark.qss',scale_manager=parent.scale_manager)
                with patch('modules.common_widgets.common_dialogs.build_current_base_stylesheet',return_value=style):
                    dialog=RouterPasswordDialog('root',parent)
                self.addCleanup(dialog.deleteLater)
                dialog.new_password.edit.setText('valid-password')
                dialog.confirm_password.edit.setText('different-password')
                dialog.save_button.click();dialog.show();APP.processEvents()
                self.assertTrue(dialog.confirm_password.edit.property('inputError'))
                self.assertTrue(dialog.error_label.text())
                self.assertNotEqual(dialog.new_password.edit.palette().base().color(),dialog.confirm_password.edit.palette().base().color())
                self.assertTrue(dialog.grab().save(str(output/('password-'+str(light)+'-'+str(scale)+'.png'))))
                QTest.keyClicks(dialog.confirm_password.edit,'x')
                self.assertFalse(dialog.confirm_password.edit.property('inputError'))
                self.assertFalse(dialog.error_label.text())
                dialog.reject()


class RenewalVerificationTests(unittest.TestCase):
    def test_stale_lease_is_failure_and_new_subnet_is_success(self):
        from modules.platform_services import lan_network as facade
        row=adapter(expected_addresses=['192.168.22.1/24'])
        fresh=adapter(addresses=['192.168.22.100/24'])
        with patch.object(facade,'current_os',return_value=facade.WINDOWS),patch('modules.platform_services.lan_network_windows.renew'),patch.object(facade,'inspect',return_value=dict(adapters=[adapter()])) as inspect:
            with self.assertRaises(RuntimeError):facade.renew(row,30)
            inspect.return_value=dict(adapters=[fresh])
            self.assertEqual(facade.renew(row,30)['addresses'],fresh['addresses'])

    def test_unarmed_or_closed_gate_never_advertises_renewal(self):
        gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90)
        self.assertFalse(gate.payload()['can_renew'])
        gate.arm();self.assertTrue(gate.payload()['can_renew'])
        selected,_=gate.claim(adapter()['id'])
        self.assertEqual(selected['expected_addresses'],['192.168.22.1/24'])
        gate.finish_renewal('done',adapter(addresses=['192.168.22.100/24']))
        self.assertEqual(gate.payload()['adapters'][0]['addresses'],['192.168.22.100/24'])
        gate.close();self.assertFalse(gate.payload()['can_renew'])
