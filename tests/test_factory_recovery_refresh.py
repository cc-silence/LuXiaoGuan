"""Connection changes during a post-reset dialog, without OS/network mutations."""
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest
from tests.test_factory_reset_recovery import RecoveryOwner, APP
from tests.test_lan_client_recovery import adapter
from modules.capture_services.lan_client_recovery import factory_reset_plan
from modules.capture_services.lan_recovery_service import LanRecoveryService
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics

DEFAULTS = dict(address='192.168.1.1', netmask='255.255.255.0', source='rom')


class FactoryRefreshTests(unittest.TestCase):
    def setUp(self):
        self.owner = RecoveryOwner(NS(), NS(operation_diagnostics=OperationDiagnostics()))
        self.broker = self.owner.lan_recovery = LanRecoveryService(self.owner)
        self.addCleanup(self.owner.deleteLater)
        self.wifi = adapter(id='wifi', wireless=True)
        self.wired = adapter(id='wired', wireless=False, addresses=['192.168.1.22/24'])
        plan = factory_reset_plan(dict(adapters=[self.wifi]), DEFAULTS)
        plan['factory_refresh_defaults'] = DEFAULTS
        plan['factory_result_note'] = 'Factory login address and password instructions'
        self.key = self.broker.create_offer(plan)
        self.addCleanup(self.broker.release, self.key)

    def wait(self):
        deadline = time.monotonic() + 4
        while self.owner._openwrt_workers and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(.005)
        self.assertFalse(self.owner._openwrt_workers)

    def test_click_on_stale_wifi_uses_new_unique_wired_route_in_background(self):
        threads = []
        def inspect(*args, **kwargs):
            threads.append(threading.current_thread() is threading.main_thread())
            return dict(adapters=[self.wired], error='')
        with patch('modules.platform_services.lan_network.inspect', side_effect=inspect), patch(
                'modules.platform_services.lan_network.renew', return_value=self.wired) as renew:
            self.assertTrue(self.broker.request(self.key, 'wifi'))
            self.assertFalse(self.broker.request(self.key, 'wifi'))
            self.wait()
        self.assertEqual(threads, [False])
        renew.assert_called_once()
        row = renew.call_args.args[0]
        self.assertEqual(row['id'], 'wired')
        self.assertEqual(row['expected_addresses'], ['192.168.1.1/24'])
        self.assertFalse(self.broker.payload(self.key)['busy'])
        self.assertTrue(self.broker.payload(self.key)['guide'].startswith('Factory login address and password instructions'))
        self.assertIn('192.168.1.22', self.broker.payload(self.key)['message'])

    def test_ready_refresh_is_read_only_and_click_rechecks_another_change(self):
        with patch('modules.platform_services.lan_network.inspect', return_value=dict(adapters=[self.wired], error='')), patch(
                'modules.platform_services.lan_network.renew') as renew:
            self.broker.set_ready(self.key, True)
            self.wait()
            renew.assert_not_called()
        self.assertEqual(self.broker.payload(self.key)['adapters'][0]['id'], 'wired')
        changed = adapter(id='new-wired', addresses=['192.168.1.33/24'])
        with patch('modules.platform_services.lan_network.inspect', return_value=dict(adapters=[changed], error='')), patch(
                'modules.platform_services.lan_network.renew', return_value=changed) as renew:
            self.broker.request(self.key, 'wired')
            self.wait()
        self.assertEqual(renew.call_args.args[0]['id'], 'new-wired')

    def test_ambiguous_routes_require_selection_and_inspection_failure_can_retry(self):
        choices = [adapter(id='one', route=False), adapter(id='two', route=False)]
        with patch('modules.platform_services.lan_network.inspect', return_value=dict(adapters=choices, error='')), patch(
                'modules.platform_services.lan_network.renew') as renew:
            self.broker.request(self.key, 'wifi')
            self.wait()
            renew.assert_not_called()
        self.assertTrue(self.broker.payload(self.key)['selection_required'])
        with patch('modules.platform_services.lan_network.inspect', return_value=dict(adapters=[], error='failure')), patch(
                'modules.platform_services.lan_network.renew') as renew:
            self.broker.request(self.key, 'one')
            self.wait()
            renew.assert_not_called()
        self.assertFalse(self.broker.payload(self.key)['busy'])
        self.assertTrue(self.broker.payload(self.key)['message'])

    def test_unrelated_default_route_is_not_an_automatic_replacement(self):
        unrelated = adapter(id='vpn', addresses=['10.9.0.2/24'], route=True)
        with patch('modules.platform_services.lan_network.inspect', return_value=dict(adapters=[unrelated], error='')), patch(
                'modules.platform_services.lan_network.renew') as renew:
            self.broker.request(self.key, 'wifi')
            self.wait()
            renew.assert_not_called()
        self.assertTrue(self.broker.payload(self.key)['selection_required'])

    def test_actual_dialog_updates_selected_interface_and_reenable_button(self):
        from modules.main_pages.packet_capture.openwrt.factory_reset_result_dialog import FactoryResetResultDialog
        dialog = FactoryResetResultDialog(dict(note='Factory login instructions', factory_login=DEFAULTS),
                                          dict(adapters=[self.wifi]), self.owner)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.reject)
        dialog.show()
        APP.processEvents()
        with patch('modules.platform_services.lan_network.inspect', return_value=dict(adapters=[self.wired], error='')), patch(
                'modules.platform_services.lan_network.renew', return_value=self.wired) as renew:
            dialog.ready.setChecked(True)
            self.wait()
            self.assertEqual(dialog.panel.adapters.currentData(), 'wired')
            self.assertTrue(dialog.panel.button.isEnabled())
            self.assertIn('Factory login instructions', dialog.panel.guide.toPlainText())
            renew.assert_not_called()
            dialog.panel.button.click()
            self.wait()
            renew.assert_called_once()
            self.assertEqual(renew.call_args.args[0]['id'], 'wired')
            self.assertTrue(dialog.panel.button.isEnabled())

    def test_expired_offer_reports_why_request_did_not_start(self):
        gate = self.broker.offers[self.key][0]
        gate.deadline = time.monotonic() + 1
        updates = []
        self.owner.configurationProgressChanged.connect(lambda key, value: updates.append(value))
        self.assertFalse(self.broker.request(self.key, 'wifi'))
        self.assertTrue(self.broker.payload(self.key)['message'])
        self.assertTrue(updates)
        self.assertFalse(self.broker.payload(self.key)['busy'])

    def test_original_static_recovery_survives_an_unrelated_second_adapter(self):
        from tests.test_factory_static_recovery import inventory
        before = inventory()
        original = before['adapters'][0]
        other = adapter(id='unrelated', route=False, addresses=['10.99.0.2/24'])
        before['adapters'].append(other)
        plan = factory_reset_plan(before, DEFAULTS)
        plan['factory_refresh_defaults'] = DEFAULTS
        key = self.broker.create_offer(plan)
        self.addCleanup(self.broker.release, key)
        with patch('modules.platform_services.lan_network.inspect', return_value=dict(adapters=[dict(original, route=False), other], error='')):
            self.broker.set_ready(key, True)
            self.wait()
        payload = self.broker.payload(key)
        row = next(row for row in payload['adapters'] if row['id'] == original['id'])
        self.assertTrue(row.get('static_recovery'))
        self.assertEqual(self.broker.offers[key][0].plan['static_migration']['adapter']['id'], original['id'])
        self.assertTrue(payload['selection_required'])

    def test_inspection_timeout_and_worker_start_failure_release_busy_state(self):
        with patch('modules.platform_services.lan_network.inspect', side_effect=TimeoutError('inspection expired')):
            self.broker.request(self.key, 'wifi')
            self.wait()
        self.assertFalse(self.broker.payload(self.key)['busy'])
        self.assertFalse(self.broker.factory_refresh.jobs)
        self.assertIn('inspection expired', self.broker.payload(self.key)['message'])
        for failure in (False, RuntimeError('cannot start worker')):
            options = dict(return_value=False) if failure is False else dict(side_effect=failure)
            with patch('modules.capture_services.task_completion.start_capture_task', **options):
                self.assertFalse(self.broker.request(self.key, 'wifi'))
            self.assertFalse(self.broker.payload(self.key)['busy'])
            self.assertFalse(self.broker.factory_refresh.jobs)

    def test_offer_expiring_during_inspection_never_starts_renewal(self):
        def inspect(*args, **kwargs):
            self.broker.offers[self.key][0].deadline = time.monotonic() - 1
            return dict(adapters=[self.wired], error='')
        with patch('modules.platform_services.lan_network.inspect', side_effect=inspect), patch(
                'modules.platform_services.lan_network.renew') as renew:
            self.broker.request(self.key, 'wifi')
            self.wait()
            renew.assert_not_called()
        self.assertFalse(self.broker.payload(self.key)['busy'])
        self.assertTrue(self.broker.payload(self.key)['message'])
        self.assertFalse(self.broker.factory_refresh.jobs)

    def test_unaddressed_wired_interface_is_selectable_then_renewed_explicitly(self):
        from modules.main_pages.packet_capture.openwrt.factory_reset_result_dialog import FactoryResetResultDialog
        pending = dict(self.wired, addresses=[], route=False)
        dialog = FactoryResetResultDialog(dict(note='Factory login instructions', factory_login=DEFAULTS),
                                          dict(adapters=[self.wifi]), self.owner)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.reject)
        dialog.show()
        APP.processEvents()
        with patch('modules.platform_services.lan_network.inspect', return_value=dict(adapters=[pending], error='')) as inspect, patch(
                'modules.platform_services.lan_network.renew', return_value=self.wired) as renew:
            dialog.ready.setChecked(True)
            self.wait()
            index = dialog.panel.adapters.findData('wired')
            self.assertGreaterEqual(index, 0)
            renew.assert_not_called()
            self.assertIs(inspect.call_args.kwargs['include_unaddressed'], True)
            dialog.panel.adapters.setCurrentIndex(index)
            self.assertTrue(dialog.panel.button.isEnabled())
            dialog.panel.button.click()
            self.wait()
            renew.assert_called_once()
            self.assertEqual(renew.call_args.args[0]['id'], 'wired')
            self.assertFalse(self.broker.payload(dialog.offer)['busy'])

    def test_close_during_inspection_never_starts_renewal(self):
        entered = threading.Event()
        release = threading.Event()
        def inspect(*args, **kwargs):
            entered.set()
            release.wait(2)
            return dict(adapters=[self.wired], error='')
        with patch('modules.platform_services.lan_network.inspect', side_effect=inspect), patch(
                'modules.platform_services.lan_network.renew') as renew:
            self.broker.request(self.key, 'wifi')
            self.assertTrue(entered.wait(1))
            self.broker.release(self.key)
            release.set()
            self.wait()
            renew.assert_not_called()
        self.assertFalse(self.broker.factory_refresh.jobs)


if __name__ == '__main__':
    unittest.main()
