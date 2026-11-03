"""Source-backed review contracts for redirects and native repository syntax."""
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from urllib.request import Request
from modules.capture_services.openwrt.package_mirror_policy import DOMESTIC, SourceRedirectHandler
from modules.capture_services.openwrt.temporary_package_sources import prepare_script


class RedirectBodyTests(unittest.TestCase):
    def test_stdlib_redirect_does_not_consume_unbounded_page_body(self):
        class Response:
            closed = False
            def close(self):
                self.closed = True
            def read(self):
                if not self.closed:
                    raise AssertionError('Redirect page body was drained without a size limit')
                return b''
        url = DOMESTIC[0] + '/releases/24.10.0/index'
        response = Response()
        headers = Message()
        headers['Location'] = 'new-index'
        request = Request(url)
        request.timeout = 1
        handler = SourceRedirectHandler('\u76f4\u8fde', url)
        handler.parent = Mock()
        handler.http_error_302(request, response, 302, 'Found', headers)
        handler.parent.open.assert_called_once()
        self.assertTrue(response.closed)


class NativeQuotedSourceTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        bash = shutil.which('bash')
        git_bash = Path('C:/Program Files/Git/bin/bash.exe')
        if git_bash.is_file():
            bash = str(git_bash)
        if bash is None:
            self.skipTest('POSIX shell unavailable')
        base = Path('build').resolve()
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            for name in ('etc/opkg','etc/apk/repositories.d','lib/apk/repositories.d','tmp'):
                (root/name).mkdir(parents=True, exist_ok=True)
            yield root, bash

    def execute(self, root, bash, name):
        script = prepare_script(name, 'b'*32)
        for prefix in ('/etc/opkg','/etc/apk','/lib/apk','/tmp/lxg-feeds'):
            script = script.replace(prefix, root.as_posix()+prefix)
        return subprocess.run([bash,'--noprofile','--norc','-s'], input=script,
                              text=True,encoding='utf-8',capture_output=True,timeout=10)

    def test_quoted_proxy_removed_and_signature_setting_preserved(self):
        with self.fixture() as (root,bash):
            main=root/'etc/opkg.conf'
            original='option check_signature 1\n"option" "http_proxy" "http://old-proxy:8080"\n'
            main.write_text(original,encoding='utf-8')
            (root/'etc/opkg/distfeeds.conf').write_text('src/gz base "https://downloads.openwrt.org/releases/24.10.0/base"\n',encoding='utf-8')
            result=self.execute(root,bash,'opkg')
            self.assertEqual(result.returncode,0,result.stderr)
            text=(Path(result.stdout.strip())/'main.conf').read_text(encoding='utf-8')
            self.assertNotIn('old-proxy',text)
            self.assertIn('check_signature 1',text)
            self.assertEqual(main.read_text(encoding='utf-8'),original)

    def test_quoted_unknown_repository_cannot_escape_mirror_policy(self):
        with self.fixture() as (root,bash):
            (root/'etc/opkg.conf').write_text('option check_signature 1\n',encoding='utf-8')
            (root/'etc/opkg/distfeeds.conf').write_text('"src/gz" "base" "https://unknown.invalid/feed"\n',encoding='utf-8')
            result=self.execute(root,bash,'opkg')
            self.assertEqual(result.returncode,72,result.stderr)
            self.assertEqual(list((root/'tmp').iterdir()),[])

    def test_cleanup_is_idempotent_and_never_removes_another_owner(self):
        from modules.capture_services.openwrt.temporary_package_sources import cleanup_script
        with self.fixture() as (root,bash):
            command=cleanup_script('b'*32).replace('/tmp/lxg-feeds',root.as_posix()+'/tmp/lxg-feeds')
            def cleanup():
                return subprocess.run([bash,'--noprofile','--norc','-s'],input=command,
                                      text=True,encoding='utf-8',capture_output=True,timeout=10)
            self.assertEqual(cleanup().returncode,0)
            folder=root/'tmp'/('lxg-feeds.'+'b'*32)
            folder.mkdir()
            (folder/'.owner').write_text('someone-else',encoding='ascii')
            (folder/'keep').write_text('untouched',encoding='ascii')
            self.assertNotEqual(cleanup().returncode,0)
            self.assertEqual((folder/'keep').read_text(encoding='ascii'),'untouched')
            (folder/'.owner').write_text('b'*32,encoding='ascii')
            self.assertEqual(cleanup().returncode,0)
            self.assertFalse(folder.exists())
            self.assertEqual(cleanup().returncode,0)

    def test_existing_directory_is_not_overwritten_by_preparation(self):
        with self.fixture() as (root,bash):
            folder=root/'tmp'/('lxg-feeds.'+'b'*32)
            folder.mkdir()
            (folder/'.owner').write_text('someone-else',encoding='ascii')
            result=self.execute(root,bash,'opkg')
            self.assertNotEqual(result.returncode,0)
            self.assertEqual((folder/'.owner').read_text(encoding='ascii'),'someone-else')

    def test_apk_variable_remote_url_is_checked_before_native_install(self):
        with self.fixture() as (root,bash):
            (root/'etc/apk/repositories.d/distfeeds.list').write_text(
                'set mirror=https://unknown.invalid\nv3 ${mirror}/releases/25.12.0/base\n',encoding='utf-8')
            result=self.execute(root,bash,'apk')
            self.assertEqual(result.returncode,72,result.stderr)
            self.assertEqual(list((root/'tmp').iterdir()),[])


class RealRedirectTransportTests(unittest.TestCase):
    def test_huge_stalled_redirect_body_is_skipped_and_target_downloaded(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Event, Thread
        from unittest.mock import patch
        from urllib.request import ProxyHandler
        from modules.capture_services.openwrt.signal_package_feed import fetch_bytes
        release = Event()
        paths = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                paths.append(self.path)
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location','/file')
                    self.send_header('Content-Length','1000000000')
                    self.end_headers()
                    release.wait(3)
                else:
                    self.send_response(200)
                    self.send_header('Content-Length','2')
                    self.end_headers()
                    self.wfile.write(b'ok')
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch('modules.capture_services.openwrt.signal_package_feed._attempt_plans',
                       return_value=[('\u76f4\u8fde',ProxyHandler({}))]):
                result = fetch_bytes('http://127.0.0.1:'+str(server.server_port)+'/redirect', timeout=1,limit=8)
            self.assertEqual(result,b'ok')
            self.assertEqual(paths,['/redirect','/file'])
            self.assertFalse(release.is_set())
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)
            self.assertFalse(thread.is_alive())


class LostPreparationReplyTests(unittest.TestCase):
    def test_cancelled_or_lost_prepare_reply_still_cleans_owned_directory(self):
        from unittest.mock import patch
        from modules.capture_services.openwrt.package_manager import PackageManager
        from modules.capture_services.openwrt.signal_manager import SignalError
        from modules.capture_services.openwrt.temporary_package_sources import domestic_sources
        for code in ('cancelled','transport'):
            with self.subTest(failure=code):
                manager=Mock()
                manager._execute.side_effect=[SignalError(code,'reply lost'),'']
                with patch('modules.network_policy.configured_proxy_channels',return_value=[]):
                    with self.assertRaises(SignalError) as caught:
                        with domestic_sources(manager,PackageManager('opkg'),None,Mock()):
                            self.fail('preparation did not return')
                self.assertEqual(caught.exception.code,'cancelled' if code=='cancelled' else 'offline_retry')
                self.assertEqual(manager._execute.call_args_list[-1].args[0],'dependency_sources_cleanup')
                self.assertIn('/.owner',manager._execute.call_args_list[-1].args[1])

    def test_unexpected_reply_cannot_redirect_cleanup_to_foreign_directory(self):
        from unittest.mock import patch
        from modules.capture_services.openwrt.package_manager import PackageManager
        from modules.capture_services.openwrt.signal_manager import SignalError
        from modules.capture_services.openwrt.temporary_package_sources import domestic_sources
        manager=Mock()
        manager._execute.side_effect=['/tmp/foreign-directory','']
        with patch('modules.network_policy.configured_proxy_channels',return_value=[]):
            with self.assertRaises(SignalError):
                with domestic_sources(manager,PackageManager('opkg'),None,Mock()):
                    self.fail('unexpected receipt')
        cleanup=manager._execute.call_args_list[-1].args[1]
        self.assertNotIn('/tmp/foreign-directory',cleanup)
        self.assertIn('/.owner',cleanup)
