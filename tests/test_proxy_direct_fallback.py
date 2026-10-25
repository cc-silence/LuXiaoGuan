"""Proxy failures fall through to a genuine direct channel."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from modules.network_policy import allowed_channels, env_for_channel, CHANNEL_DIRECT


class ChannelPolicyTests(unittest.TestCase):
    def routes(self, http='', socks='', system=''):
        with patch('modules.network_policy.winbox_http_url', return_value=http), \
             patch('modules.network_policy.winbox_socks_url', return_value=socks), \
             patch('modules.network_policy.system_proxy_url', return_value=system):
            return allowed_channels(None, 'proxy')

    def test_proxy_priority_deduplication_and_direct_last(self):
        routes = self.routes('http://first:1', 'socks5h://second:2', 'http://third:3')
        self.assertEqual([c.proxy_url for c in routes],
            ['http://first:1', 'socks5h://second:2', 'http://third:3', ''])
        self.assertEqual([c.name for c in self.routes('http://first:1', system='http://first:1')],
                         ['http_proxy', CHANNEL_DIRECT])

    def test_no_proxy_and_explicit_direct_both_work(self):
        self.assertEqual([c.name for c in self.routes()], [CHANNEL_DIRECT])
        self.assertEqual([c.name for c in allowed_channels(None, 'direct')], [CHANNEL_DIRECT])

    def test_direct_environment_clears_all_inherited_proxy_routes(self):
        with patch.dict(os.environ, {'HTTPS_PROXY':'http://broken', 'ALL_PROXY':'socks5h://broken'}):
            env = env_for_channel(self.routes()[-1])
        self.assertNotIn('HTTPS_PROXY', env)
        self.assertNotIn('ALL_PROXY', env)
        self.assertEqual(env['NO_PROXY'], '*')


class ReceiveFallbackTests(unittest.TestCase):
    def receive(self, first, *, cancel=None, bridge_error=False, deadline=float('inf')):
        import requests
        from threading import Event
        from unittest.mock import MagicMock
        from modules.capture_services.openwrt.common_bundle_download import _receive
        session = MagicMock()
        session.__enter__.return_value = session
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.iter_content.return_value = [b'direct data']
        session.get.side_effect = [first, response] if not bridge_error else [response]
        bridge = MagicMock()
        bridge.__enter__.return_value = bridge
        bridge.http_proxy.side_effect = ([ValueError('invalid proxy'), ''] if bridge_error
                                        else lambda url: url)
        with patch('modules.network_policy.winbox_http_url', return_value='http://broken:1'), \
             patch('modules.network_policy.winbox_socks_url', return_value=''), \
             patch('modules.network_policy.system_proxy_url', return_value=''), \
             patch('requests.Session', return_value=session), \
             patch('modules.http_socks_bridge.ProxyBridgePool', return_value=bridge):
            data = _receive('https://fixture/bundle', None, 64, cancel or Event(),
                            lambda *_:None, deadline)
        return data, session

    def test_proxy_timeout_reaches_direct_without_environment_proxy(self):
        import requests
        data, session = self.receive(requests.exceptions.ProxyError('proxy unavailable'))
        self.assertEqual(data, b'direct data')
        self.assertFalse(session.trust_env)
        self.assertEqual(session.get.call_args_list[-1].kwargs['proxies'], {})
        self.assertEqual(session.get.call_count, 2)

    def test_proxy_authentication_and_gateway_failures_reach_direct(self):
        from unittest.mock import MagicMock
        for status in (407, 408, 429, 500, 502, 503, 504):
            first = MagicMock()
            first.__enter__.return_value = first
            first.status_code = status
            data, session = self.receive(first)
            self.assertEqual(data, b'direct data', status)
            self.assertEqual(session.get.call_count, 2)

    def test_invalid_bridge_can_still_reach_direct(self):
        self.assertEqual(self.receive(None, bridge_error=True)[0], b'direct data')

    def test_http_denial_and_cancellation_do_not_retry(self):
        from unittest.mock import MagicMock
        from threading import Event
        first = MagicMock()
        first.__enter__.return_value = first
        first.status_code = 403
        with self.assertRaises(ValueError):
            self.receive(first)
        cancel = Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            self.receive(first, cancel=cancel)


class CheckerRealFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_app_updates as fixture
        cls.fixture = fixture
        fixture.NetworkTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.NetworkTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.fixture.NetworkTests.setUp(self)

    def tearDown(self):
        self.fixture.NetworkTests.tearDown(self)

    def run_check(self, *args, **kwargs):
        return self.fixture.NetworkTests.run_check(self, *args, **kwargs)

    def set_routes(self, routes):
        self.routes.stop()
        self.routes = patch('modules.network_policy.allowed_channels', return_value=routes)
        self.routes.start()

    def test_invalid_proxy_address_reaches_real_signed_direct_response(self):
        from modules.network_policy import NetworkChannel
        self.set_routes([NetworkChannel('http_proxy', 'bad proxy', 'http://bad:port'),
                         NetworkChannel(CHANNEL_DIRECT, 'direct')])
        self.assertEqual(self.run_check()[0][0], 'ok')

    def test_connection_refused_reaches_real_signed_direct_response(self):
        from modules.network_policy import NetworkChannel
        self.set_routes([NetworkChannel('http_proxy', 'bad proxy', 'http://127.0.0.1:1'),
                         NetworkChannel(CHANNEL_DIRECT, 'direct')])
        # Native TCP refusal may exceed the single-route fixture deadline.
        timeout_ms = self.checker.TIMEOUT_MS * 3 + 1000
        self.assertEqual(self.run_check(timeout_ms=timeout_ms)[0][0], 'ok')

    def test_stalled_proxy_timeout_leaves_budget_for_direct(self):
        import threading, time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from modules.network_policy import NetworkChannel
        hits = []
        class StalledProxy(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                hits.append(self.path)
                time.sleep(.45)
                try:
                    self.send_response(503)
                    self.end_headers()
                except OSError: pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), StalledProxy)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.checker.TIMEOUT_MS = 150
            self.set_routes([NetworkChannel('http_proxy', 'slow proxy',
                            'http://127.0.0.1:' + str(server.server_port)),
                            NetworkChannel(CHANNEL_DIRECT, 'direct')])
            self.assertEqual(self.run_check()[0][0], 'ok')
            self.assertTrue(hits)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class UpdateFileFallbackTests(unittest.TestCase):
    def test_real_blob_verification_after_proxy_failure(self):
        import hashlib, json, zlib
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import MagicMock
        import requests
        from tests import test_app_updates as fixture
        from modules.app_update_services.manifest import canonical, verify_manifest
        from modules.app_update_services.locations import CACHE_DIRECTORY
        from modules.app_update_services.transfer import UpdateTransfer
        content = b'new verified application'
        compressed = zlib.compress(content)
        row = dict(path='LuXiaoGuan.exe', size=len(content), sha256=hashlib.sha256(content).hexdigest(),
                   blob_size=len(compressed), blob_sha256=hashlib.sha256(compressed).hexdigest())
        catalog = dict(schema=1, version='2.0.9_alpha', resource_revision=0, files=[row],
                       retired=[], migrations=[], history=[dict(version='2.0.9_alpha', notes='', published_at=fixture.PAYLOAD['published_at'])])
        raw = canonical(catalog)
        identity = hashlib.sha256(raw).hexdigest()
        payload = dict(fixture.PAYLOAD, catalog_url='https://fixture/catalog/' + identity + '.json',
                       catalog_size=len(raw), catalog_sha256=identity)
        manifest = fixture.signed(payload)
        release = verify_manifest(manifest, fixture.PUBLIC)
        session = MagicMock()
        session.__enter__.return_value = session
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.iter_content.return_value = [compressed]
        session.get.side_effect = [requests.exceptions.ProxyError('unavailable'), response]
        with TemporaryDirectory() as folder, \
             patch('modules.network_policy.winbox_http_url', return_value='http://broken:1'), \
             patch('modules.network_policy.winbox_socks_url', return_value=''), \
             patch('modules.network_policy.system_proxy_url', return_value=''), \
             patch('requests.Session', return_value=session):
            root = Path(folder)
            (root/'config').mkdir()
            (root/'config/app_update.json').write_text(json.dumps(dict(network_mode='proxy')), encoding='utf-8')
            transfer = UpdateTransfer(root, catalog, manifest)
            stage = root/CACHE_DIRECTORY/identity
            transfer._download_locked(dict(files=[row], download_bytes=len(compressed)), release, stage)
            self.assertEqual((stage/(row['sha256']+'.file')).read_bytes(), content)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.get.call_args_list[-1].kwargs['proxies'], {})


class RemainingBudgetTests(unittest.TestCase):
    def test_proxy_cannot_use_all_direct_connection_budget(self):
        import requests, time
        data, session = ReceiveFallbackTests.receive(self, requests.exceptions.Timeout('slow proxy'),
                                     deadline=time.monotonic() + 12)
        self.assertEqual(data, b'direct data')
        self.assertLessEqual(max(session.get.call_args_list[0].kwargs['timeout']), 3)
        self.assertEqual(session.get.call_args_list[1].kwargs['proxies'], {})


class SoftwareItemFallbackTests(unittest.TestCase):
    def test_failed_proxy_submits_fresh_direct_task(self):
        from types import SimpleNamespace as NS
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import Mock
        from modules.main_pages.download.download_operations import DownloadOperationsCoordinator
        page = NS(config_manager=None, _check_results={}, _download_tasks={},
                  download_service=NS(download=Mock(return_value=NS(gid='direct-task'))),
                  _append_info=Mock(), _set_status=Mock())
        row = dict(id='software', node=dict(name='Software', download_mode='proxy'),
                   network_channel_index=0)
        task = NS(software_id='software', software_name='Software', url='https://fixture/file.zip',
                  filename='file.zip', options={'http-proxy':'http://broken:1',
                                               'winbox-network-channel':'http_proxy'})
        with TemporaryDirectory() as folder, \
             patch('modules.network_policy.winbox_http_url', return_value='http://broken:1'), \
             patch('modules.network_policy.winbox_socks_url', return_value=''), \
             patch('modules.network_policy.system_proxy_url', return_value=''):
            operations = NS(_page=page, _software_download_dir=Mock(return_value=Path(folder)))
            operations._aria2_proxy_options = lambda r: DownloadOperationsCoordinator._aria2_proxy_options(operations, r)
            self.assertTrue(DownloadOperationsCoordinator._try_next_network_channel(operations, task, row))
        options = page.download_service.download.call_args.kwargs['options']
        self.assertEqual(options['winbox-network-channel'], CHANNEL_DIRECT)
        self.assertNotIn('http-proxy', options)
        self.assertNotIn('https-proxy', options)
        self.assertEqual(row['active_network_channel'], CHANNEL_DIRECT)

    def test_manual_proxy_availability_does_not_count_direct(self):
        from modules.network_policy import configured_proxy_channels
        with patch('modules.network_policy.winbox_http_url', return_value=''), \
             patch('modules.network_policy.winbox_socks_url', return_value=''), \
             patch('modules.network_policy.system_proxy_url', return_value=''):
            self.assertEqual(configured_proxy_channels(), [])
            self.assertEqual([c.name for c in allowed_channels(None, 'proxy')], [CHANNEL_DIRECT])
