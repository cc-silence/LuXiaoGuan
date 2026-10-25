"""Download evidence separates route setup from body reads without leaking secrets."""
import io
import os
import unittest
from contextlib import ExitStack
from unittest.mock import Mock,patch
from urllib.error import URLError,HTTPError
from modules.capture_services.openwrt import signal_package_feed as feed
from modules.capture_services.openwrt.package_download_diagnostics import endpoint,configuration_lines


class DownloadDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.messages=[]
        self.stack.enter_context(feed.download_session(lambda text,*_:self.messages.append(text)))
        self.stack.enter_context(patch.object(feed,'_attempt_plans',return_value=[('http://alice:password123@localhost:7890',object())]))
        self.stack.enter_context(patch.object(feed.urllib.request,'proxy_bypass',return_value=False))
        self.opener=self.stack.enter_context(patch.object(feed.urllib.request,'build_opener')).return_value

    def fetch(self):
        return feed.fetch_bytes('https://downloads.openwrt.org/secret/path?token=hidden',timeout=1)

    def test_connect_timeout_has_phase_target_exception_and_no_secrets(self):
        self.opener.open.side_effect=URLError(TimeoutError('password123 token=hidden'))
        with self.assertRaises(RuntimeError):self.fetch()
        text='\n'.join(self.messages)
        for value in ('downloads.openwrt.org','localhost:7890','URLError/TimeoutError','\u7b49\u5f85\u54cd\u5e94\u5934','\u5df2\u6536 0'):
            self.assertIn(value,text)
        for value in ('alice','password123','token=hidden','secret/path'):self.assertNotIn(value,text)

    def test_body_timeout_records_partial_bytes_and_expected_length(self):
        response=Mock(status=200,headers={'Content-Length':'10'})
        response.read1.side_effect=[b'abcd',TimeoutError()]*2
        self.opener.open.return_value.__enter__=Mock(return_value=response)
        self.opener.open.return_value.__exit__=Mock(return_value=False)
        with self.assertRaises(RuntimeError):self.fetch()
        text='\n'.join(self.messages)
        self.assertIn('\u9636\u6bb5 \u8bfb\u53d6\u54cd\u5e94\u5185\u5bb9',text)
        self.assertIn('\u5df2\u6536 4 \u5b57\u8282',text)
        self.assertIn('\u54cd\u5e94\u957f\u5ea6 10',text)

    def test_success_and_proxy_bypass_are_explicit(self):
        response=Mock(status=200,headers={'Content-Length':'2'})
        response.read1.side_effect=[b'ok',b'']
        self.opener.open.return_value.__enter__=Mock(return_value=response)
        self.opener.open.return_value.__exit__=Mock(return_value=False)
        with patch.object(feed.urllib.request,'proxy_bypass',return_value=True):self.assertEqual(self.fetch(),b'ok')
        text='\n'.join(self.messages)
        self.assertIn('\u547d\u4e2d\u7ed5\u8fc7\u4ee3\u7406\u89c4\u5219',text)
        self.assertIn('\u4e0b\u8f7d\u6210\u529f',text)
        self.assertIn('\u5df2\u6536 2 \u5b57\u8282',text)

    def test_http_auth_failure_records_status_without_response_body(self):
        self.opener.open.side_effect=HTTPError('https://alice:password123@fixture',407,'hidden',{},io.BytesIO(b'private'))
        with self.assertRaises(RuntimeError):self.fetch()
        text='\n'.join(self.messages)
        self.assertIn('HTTP 407',text)
        for value in ('password123','private','hidden'):self.assertNotIn(value,text)

    def test_configuration_detects_socks_and_reports_errors_without_values(self):
        with patch('modules.network_policy.winbox_http_url',side_effect=ValueError('password123')), \
             patch('modules.network_policy.winbox_socks_url',return_value='socks5://alice:password123@localhost:1080'), \
             patch('modules.network_policy.system_proxy_url',return_value=''), \
             patch.dict(os.environ,{'NO_PROXY':'private-host'},clear=True):
            text='\n'.join(configuration_lines([('\u76f4\u8fde',object())]))
        self.assertIn('ValueError',text)
        self.assertIn('socks5://localhost:1080',text)
        self.assertNotIn('\u672a\u8bfb\u53d6\u5355\u72ec\u914d\u7f6e',text)
        for value in ('alice','password123','private-host'):self.assertNotIn(value,text)

    def test_exported_zip_keeps_diagnostic_stages_without_secrets(self):
        import tempfile,zipfile
        from modules.log_services import export_logs
        self.opener.open.side_effect=URLError(TimeoutError('password123'))
        with self.assertRaises(RuntimeError):self.fetch()
        with tempfile.TemporaryDirectory() as directory:
            output=os.path.join(directory,'evidence.zip')
            export_logs(output,'dependency',[(line,'info') for line in self.messages])
            with zipfile.ZipFile(output) as archive:text=archive.read('information.txt').decode('utf-8')
        self.assertIn('URLError/TimeoutError',text)
        self.assertIn('\u7b49\u5f85\u54cd\u5e94\u5934',text)
        for secret in ('alice','password123','token=hidden'):self.assertNotIn(secret,text)

    def test_configuration_is_logged_once_for_nested_session(self):
        response=Mock(status=200,headers={});response.read1.side_effect=[b'ok',b'',b'ok',b'']
        self.opener.open.return_value.__enter__=Mock(return_value=response)
        self.opener.open.return_value.__exit__=Mock(return_value=False)
        with patch.object(feed,'configuration_lines',return_value=['configuration']) as lines:
            self.fetch();self.fetch()
        lines.assert_called_once()
        self.assertEqual(self.messages.count('configuration'),1)

    def test_no_observer_does_not_reread_configuration_for_diagnostics(self):
        self.stack.close()
        response=Mock(status=200,headers={});response.read1.side_effect=[b'ok',b'']
        opener=Mock();opener.open.return_value.__enter__=Mock(return_value=response);opener.open.return_value.__exit__=Mock(return_value=False)
        with patch.object(feed,'configuration_lines') as lines, \
             patch.object(feed,'_attempt_plans',return_value=[('\u76f4\u8fde',object())]), \
             patch.object(feed.urllib.request,'build_opener',return_value=opener):
            self.assertEqual(self.fetch(),b'ok')
        lines.assert_not_called()

    def test_endpoint_rejects_malformed_or_control_characters(self):
        self.assertEqual(endpoint('https://alice:password123@host:443/private?token=hidden'),'https://host:443')
        self.assertEqual(endpoint('https://host:bad'),'invalid-endpoint')
        self.assertEqual(endpoint('http://[::1]:8080'),'http://[::1]:8080')
        self.assertNotIn('\\',endpoint('http://bad\\host/'))


class RealTransportDiagnosticTests(unittest.TestCase):
    def setUp(self):
        import threading
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        self.finished=threading.Event()
        stopped=self.finished
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Length','10' if self.path=='/slow' else '2');self.end_headers()
                try:
                    self.wfile.write(b'abcd' if self.path=='/slow' else b'ok');self.wfile.flush()
                    if self.path=='/slow':stopped.wait(.2)
                except (BrokenPipeError,ConnectionResetError):pass
            def do_CONNECT(self):
                self.send_response(407);self.end_headers()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);self.thread.start()
        self.addCleanup(self.close)
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.messages=[]

    def close(self):
        self.finished.set();self.server.shutdown();self.server.server_close();self.thread.join(1)

    def test_real_host_port_bypass_matches_urllib(self):
        import urllib.request
        with feed.download_session(lambda text,*_:self.messages.append(text)), \
             patch.object(feed,'_attempt_plans',return_value=[('http://127.0.0.1:1',urllib.request.ProxyHandler({'http':'http://127.0.0.1:1'}))]), \
             patch.dict(os.environ,{'NO_PROXY':'127.0.0.1:'+str(self.server.server_port),'no_proxy':'127.0.0.1:'+str(self.server.server_port)}):
            self.assertEqual(feed.fetch_bytes(self.url+'/ok',timeout=1),b'ok')
        self.assertIn('\u547d\u4e2d\u7ed5\u8fc7\u4ee3\u7406\u89c4\u5219','\n'.join(self.messages))

    def test_real_body_timeout_preserves_partial_count(self):
        import urllib.request
        with feed.download_session(lambda text,*_:self.messages.append(text)), \
             patch.object(feed,'_attempt_plans',return_value=[('\u76f4\u8fde',urllib.request.ProxyHandler({}))]), \
             patch.object(feed,'_ROUTE_TIMEOUT',.04):
            with self.assertRaises(RuntimeError):feed.fetch_bytes(self.url+'/slow',timeout=1)
        text='\n'.join(self.messages)
        self.assertIn('\u9636\u6bb5 \u8bfb\u53d6\u54cd\u5e94\u5185\u5bb9',text)
        self.assertIn('\u5df2\u6536 4 \u5b57\u8282',text)

    def test_real_https_proxy_connect_407_does_not_leak_credentials(self):
        import urllib.request
        proxy=self.url.replace('http://','http://alice:password123@')
        with feed.download_session(lambda text,*_:self.messages.append(text)), \
             patch.object(feed,'_attempt_plans',return_value=[(proxy,urllib.request.ProxyHandler({'https':proxy}))]), \
             patch.object(urllib.request,'proxy_bypass',return_value=False):
            with self.assertRaises(RuntimeError):feed.fetch_bytes('https://fixture.invalid/path?token=hidden',timeout=1)
        text='\n'.join(self.messages)
        self.assertIn('\u4ee3\u7406\u8ba4\u8bc1\u5931\u8d25',text)
        for secret in ('alice','password123','token=hidden'):self.assertNotIn(secret,text)


if __name__=='__main__':unittest.main()
