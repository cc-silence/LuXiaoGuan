"""Audit regressions: sparse proxy migration, queued router identity and cancelled local work."""
import threading,unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from modules.config_services.proxy_config_manager import normalize_proxy_data
from modules.capture_services.wireless_setup_service import WirelessSetupService
from tests.test_factory_reset_recovery import RecoveryOwner
from tests.test_lan_client_recovery import adapter
from modules.capture_services.lan_recovery_service import LanRecoveryService
from modules.capture_services.lan_client_recovery import factory_reset_plan


class CompatibilityAuditTests(unittest.TestCase):
    def test_sparse_existing_proxy_never_enables_the_missing_proxy_kind(self):
        for raw in (dict(http_host='proxy.example',http_port='1234'),
                    dict(primary=dict(http_host='legacy.proxy',http_port='2345'))):
            result=normalize_proxy_data(raw)
            self.assertEqual(result['socks_host'],'')
        result=normalize_proxy_data(dict(socks_host='socks.example',socks_port='3456'))
        self.assertEqual(result['http_host'],'')
        fresh=normalize_proxy_data(None)
        self.assertEqual(fresh['http_host'],'127.0.0.1');self.assertEqual(fresh['socks_host'],'127.0.0.1')

    def test_relocation_worker_uses_the_connection_generation_captured_at_click(self):
        original=object();ssh=NS(_client=original,connection_generation=1,host_fingerprint='pinned',
            authenticated_settings=dict(host='router',port=22,user='root'),_host='router',_port=22,_user='root')
        context=NS(owner=NS(openwrt_backend=NS(ssh_client=ssh)),manager=Mock())
        jobs=[]
        context._start=lambda action,operation,**kwargs:jobs.append(operation) or True
        with patch('modules.capture_services.openwrt.relocation_manager.RelocationManager') as factory:
            self.assertTrue(WirelessSetupService.relocate(context,{}))
            ssh._client=object();ssh.connection_generation=2
            jobs[0](threading.Event(),Mock())
        options=factory.return_value.apply.call_args.kwargs
        self.assertEqual(options.get('connection_generation'),1)
        self.assertIs(options.get('expected_client'),original)

    def test_cancelled_or_shutdown_renewal_never_calls_platform_network_mutation(self):
        for shutting_down in (False,True):
            owner=RecoveryOwner(NS(),NS());broker=LanRecoveryService(owner)
            self.addCleanup(owner.deleteLater)
            key=broker.create_offer(factory_reset_plan(dict(adapters=[adapter()])))
            jobs=[]
            def start(*args):jobs.append((args[2],args[3]))
            with patch('modules.capture_services.task_completion.start_capture_task',side_effect=start):
                broker.request(key,adapter()['id'])
            operation,event=jobs[0]
            if shutting_down:owner._shutting_down=True
            else:event.set()
            with patch('modules.platform_services.lan_network.renew') as renew:
                with self.assertRaises(InterruptedError):operation(event)
                renew.assert_not_called()
            broker.release(key)

    def test_stale_relocation_generation_stops_before_opening_remote_connection(self):
        from modules.capture_services.openwrt.relocation_manager import RelocationManager
        from modules.capture_services.openwrt.signal_manager import SignalError
        original=object()
        wireless=NS(runner=NS(ssh_client=NS(_client=object(),connection_generation=2)))
        with patch('modules.capture_services.openwrt.relocation_connection.RelocationConnection') as remote:
            with self.assertRaises(SignalError) as caught:
                RelocationManager(wireless).apply({},threading.Event(),Mock(),connection_settings={},
                    expected_client=original,connection_generation=1)
            self.assertEqual(caught.exception.code,'changed')
            remote.assert_not_called()
