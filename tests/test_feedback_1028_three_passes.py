"""Three independent passes: interaction, task lifecycles and configuration transactions."""
import copy
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest,QSignalSpy
from PySide6.QtWidgets import QWidget,QLineEdit
from tests.test_standalone import APP
from tests.test_lan_client_recovery import adapter
from modules.capture_services.lan_client_recovery import factory_reset_plan
from tests.test_factory_reset_recovery import RecoveryOwner
from modules.capture_services.lan_recovery_service import LanRecoveryService
from modules.main_pages.packet_capture.openwrt.router_password_dialog import RouterPasswordDialog
from modules.main_pages.packet_capture.openwrt.router_homepage_dialog import RouterHomepageDialog
from modules.main_pages.packet_capture.openwrt.factory_reset_result_dialog import FactoryResetResultDialog


class PassOneInteractionTests(unittest.TestCase):
    def password(self):
        dialog=RouterPasswordDialog('root');self.addCleanup(dialog.deleteLater)
        dialog.show();APP.processEvents();return dialog

    def test_mismatch_never_opens_confirmation_and_editing_either_field_clears_feedback(self):
        for field in ('new_password','confirm_password'):
            dialog=self.password()
            dialog.new_password.edit.setText('long-password')
            dialog.confirm_password.edit.setText('different-password')
            with patch('modules.main_pages.packet_capture.openwrt.router_password_dialog.ConfirmDialog') as confirm:
                dialog.save_button.click()
            confirm.assert_not_called()
            self.assertTrue(dialog.confirm_password.edit.property('inputError'))
            QTest.keyClicks(getattr(dialog,field).edit,'x')
            self.assertFalse(dialog.confirm_password.edit.property('inputError'))
            self.assertFalse(dialog.error_label.text());dialog.reject()

    def test_matching_but_invalid_password_cannot_reach_remote_confirmation(self):
        dialog=self.password();dialog.new_password.edit.setText('short');dialog.confirm_password.edit.setText('short')
        with patch('modules.main_pages.packet_capture.openwrt.router_password_dialog.ConfirmDialog') as confirm:
            dialog.save_button.click()
        confirm.assert_not_called();self.assertTrue(dialog.error_label.text())
        self.assertFalse(dialog.confirm_password.edit.property('inputError'));dialog.reject()

    def test_escape_clears_both_secrets_and_restores_hidden_echo(self):
        dialog=self.password();dialog.new_password.edit.setText('long-password')
        dialog.confirm_password.edit.setText('long-password');dialog.new_password.toggle.setChecked(True)
        QTest.keyClick(dialog,Qt.Key_Escape);APP.processEvents()
        self.assertFalse(dialog.isVisible());self.assertFalse(dialog.new_password.edit.text())
        self.assertFalse(dialog.confirm_password.edit.text())
        self.assertEqual(dialog.new_password.edit.echoMode(),QLineEdit.Password)

    def test_cancelled_password_confirmation_keeps_entry_for_correction(self):
        dialog=self.password();dialog.new_password.edit.setText('long-password');dialog.confirm_password.edit.setText('long-password')
        with patch('modules.main_pages.packet_capture.openwrt.router_password_dialog.ConfirmDialog') as confirm:
            confirm.return_value.Accepted=1;confirm.return_value.exec.return_value=0
            dialog.save_button.click()
        self.assertTrue(dialog.isVisible());self.assertEqual(dialog.new_password.edit.text(),'long-password')
        dialog.reject()

    def test_no_available_homepage_cannot_accept_by_click(self):
        dialog=RouterHomepageDialog(dict(pages=['missing-page']));self.addCleanup(dialog.deleteLater)
        accepted=QSignalSpy(dialog.accepted);dialog.continue_button.click()
        self.assertEqual(accepted.count(),0);self.assertFalse(dialog.continue_button.isEnabled())

    def test_reset_readiness_can_be_revoked_without_starting_adapter_work(self):
        owner=RecoveryOwner(NS(),NS());owner.lan_recovery=LanRecoveryService(owner);self.addCleanup(owner.deleteLater)
        dialog=FactoryResetResultDialog(dict(note='Reset submitted'),dict(adapters=[adapter()]),owner)
        self.addCleanup(dialog.deleteLater);dialog.show();APP.processEvents()
        with patch('modules.capture_services.task_completion.start_capture_task') as start:
            dialog.panel.button.click();start.assert_not_called()
            dialog.ready.setChecked(True);self.assertTrue(dialog.panel.button.isEnabled())
            dialog.ready.setChecked(False);self.assertFalse(dialog.panel.button.isEnabled())
            dialog.panel.button.click();start.assert_not_called()
        QTest.keyClick(dialog,Qt.Key_Escape);APP.processEvents()
        self.assertFalse(owner.lan_recovery.offers);self.assertFalse(dialog.isVisible())


class PassTwoLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.owner=RecoveryOwner(NS(),NS());self.addCleanup(self.owner.deleteLater)
        self.broker=LanRecoveryService(self.owner);self.owner.lan_recovery=self.broker

    def offer(self):return self.broker.create_offer(factory_reset_plan(dict(adapters=[adapter()])))

    def drain(self):
        deadline=time.monotonic()+4
        while self.owner._openwrt_workers and time.monotonic()<deadline:
            APP.processEvents();time.sleep(.005)
        self.assertFalse(self.owner._openwrt_workers)
        self.assertFalse(self.broker.adapter_jobs)

    def test_real_worker_exception_releases_all_locks_and_allows_retry(self):
        key=self.offer()
        with patch('modules.platform_services.lan_network.renew',side_effect=TimeoutError('lease timeout')):
            self.assertTrue(self.broker.request(key,adapter()['id']));self.drain()
        self.assertFalse(self.broker.payload(key)['busy']);self.assertTrue(self.broker.payload(key)['message'])
        with patch('modules.platform_services.lan_network.renew',return_value=adapter()) as renew:
            self.assertTrue(self.broker.request(key,adapter()['id']));self.drain()
        self.assertEqual(renew.call_count,1);self.broker.release(key)

    def test_real_worker_outlives_closed_offer_without_allowing_a_second_mutation(self):
        first=self.offer();started=threading.Event();release=threading.Event();permits=[]
        def renewal(row,timeout,permit):
            started.set()
            if not release.wait(2):raise TimeoutError('test gate')
            permits.append(permit())
            if not permit():raise InterruptedError('closed offer')
            return row
        with patch('modules.platform_services.lan_network.renew',side_effect=renewal) as renew:
            try:
                self.assertTrue(self.broker.request(first,adapter()['id']));self.assertTrue(started.wait(1))
                self.broker.release(first);second=self.offer()
                self.assertFalse(self.broker.request(second,adapter()['id']))
                release.set();self.drain()
                self.assertEqual(renew.call_count,1);self.assertEqual(permits,[False])
            finally:release.set();self.drain()
        self.assertNotIn(adapter()['id'],self.broker.payload(second)['busy_adapters'])
        self.broker.release(second);self.assertFalse(self.broker.offers)

    def test_shutdown_revokes_inflight_permission_and_rejects_new_work(self):
        key=self.offer();started=threading.Event();release=threading.Event()
        def renewal(row,timeout,permit):
            started.set()
            if not release.wait(2):raise TimeoutError('test gate')
            if not permit():raise InterruptedError('shutdown')
            return row
        with patch('modules.platform_services.lan_network.renew',side_effect=renewal):
            try:
                self.broker.request(key,adapter()['id']);self.assertTrue(started.wait(1))
                self.owner._shutting_down=True
                self.assertFalse(self.broker.request(key,adapter()['id']))
                release.set();self.drain()
            finally:release.set();self.drain()
        self.assertFalse(self.broker.payload(key)['busy']);self.broker.release(key)

    def test_synchronous_start_exception_releases_gate_without_platform_call(self):
        key=self.offer()
        with patch('modules.capture_services.task_completion.start_capture_task',side_effect=RuntimeError('no worker')),patch('modules.platform_services.lan_network.renew') as renew:
            self.assertFalse(self.broker.request(key,adapter()['id']))
        renew.assert_not_called();self.assertFalse(self.broker.adapter_jobs)
        self.assertFalse(self.broker.payload(key)['busy']);self.broker.release(key)

    def test_many_open_close_cycles_leave_no_retained_offers(self):
        for _ in range(128):
            key=self.offer();self.broker.release(key)
        self.assertFalse(self.broker.offers);self.assertFalse(self.broker.standalone)
        self.assertFalse(self.broker.adapter_jobs)
        keys=[self.offer() for _ in range(32)]
        with self.assertRaises(RuntimeError):self.offer()
        for key in keys:self.broker.release(key)
        self.assertFalse(self.broker.offers)


class PassThreeTransactionTests(unittest.TestCase):
    def test_backup_failure_stops_before_identity_or_homepage_writes(self):
        from tests.test_router_policy import PolicyDevice
        for kind in ('station','homepage'):
            d=PolicyDevice();review=d.policy.preview(kind,{},d.cancel)
            before=copy.deepcopy(d.config);files=copy.deepcopy(d.files)
            with patch('modules.capture_services.openwrt.router_policy.RouterBackupManager') as backup:
                backup.return_value.export.side_effect=OSError('backup disk full')
                with self.assertRaises(OSError):d.policy.apply(review,Path('before.tar.gz'),None,d.cancel,Mock())
            self.assertEqual(d.config,before);self.assertEqual(d.files,files)
            self.assertFalse(d.sessions);self.assertNotIn(('uci','apply'),d.calls)

    def test_configuration_changed_during_backup_is_not_overwritten(self):
        from tests.test_router_policy import PolicyDevice
        d=PolicyDevice();review=d.policy.preview('station',{},d.cancel)
        def exported(*args):
            d.config['network']['wwan']['hostname']='ExternalChange'
            d.files['/etc/config/network']+='\n'
            return dict(path='before.tar.gz')
        with patch('modules.capture_services.openwrt.router_policy.RouterBackupManager') as backup:
            backup.return_value.export.side_effect=exported
            with self.assertRaises(RuntimeError):d.policy.apply(review,Path('before.tar.gz'),None,d.cancel,Mock())
        self.assertEqual(d.config['network']['wwan']['hostname'],'ExternalChange')
        self.assertNotIn('macaddr',d.config['wireless']['station'])
        self.assertNotIn(('uci','apply'),d.calls)

    def test_cancel_after_backup_never_begins_identity_transaction(self):
        from tests.test_router_policy import PolicyDevice
        d=PolicyDevice();review=d.policy.preview('station',{},d.cancel);before=copy.deepcopy(d.config)
        def exported(*args):d.cancel.set();return dict(path='before.tar.gz')
        with patch('modules.capture_services.openwrt.router_policy.RouterBackupManager') as backup:
            backup.return_value.export.side_effect=exported
            with self.assertRaises(InterruptedError):d.policy.apply(review,Path('before.tar.gz'),None,d.cancel,Mock())
        self.assertEqual(d.config,before);self.assertFalse(d.sessions)
        self.assertNotIn(('uci','apply'),d.calls)

    def test_driver_ignoring_station_mac_never_confirms_and_restores_configuration(self):
        from tests.test_router_policy import PolicyDevice
        from tests.test_checked_apply import fast_deadlines
        for legacy in (True,False):
            d=PolicyDevice(legacy);native=d.call
            def call(obj,method,arguments):
                if obj=='network.device':return d.wrap(dict(up=True,macaddr='02:00:00:00:00:01')),None
                return native(obj,method,arguments)
            d.lua.globals().bridge_call=call
            review=d.policy.preview('station',{},d.cancel);before=copy.deepcopy(d.config)
            with fast_deadlines(),self.assertRaises(RuntimeError):d.policy.apply(review,None,None,d.cancel,Mock())
            self.assertNotIn(('uci','confirm'),d.calls)
            self.assertIn(('uci','rollback'),d.calls)
            self.assertEqual(d.config,before)

    def test_changing_other_adapter_or_getting_router_address_is_not_valid_renewal(self):
        from modules.platform_services import lan_network as facade
        requested=adapter(expected_addresses=['192.168.22.1/24'])
        wrong_id=adapter(id='different',addresses=['192.168.22.100/24'])
        for current in (wrong_id,adapter(addresses=['192.168.22.1/24']),adapter(addresses=['169.254.8.9/16'])):
            with patch.object(facade,'current_os',return_value=facade.WINDOWS),patch('modules.platform_services.lan_network_windows.renew'),patch.object(facade,'inspect',return_value=dict(adapters=[current])):
                with self.assertRaises(RuntimeError):facade.renew(requested,30)

    def test_expired_linux_permission_never_activates_a_connection(self):
        from modules.platform_services import lan_network_linux as linux
        with patch.object(linux,'inspect',return_value=[adapter()]),patch.object(linux,'command') as command:
            with self.assertRaises(RuntimeError):linux.renew(adapter(),30,lambda:False)
        command.assert_not_called()
