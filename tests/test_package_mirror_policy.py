"""Route-dependent package sources and native source isolation."""
import io
import unittest
from threading import Event
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request
from modules.capture_services.openwrt import package_mirror_policy as policy
from modules.capture_services.openwrt import signal_package_feed as feed
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.temporary_package_sources import domestic_sources, prepare_script
DIRECT='\u76f4\u8fde'
URL=policy.OFFICIAL[0]+'/releases/24.10.0/packages/aarch64_cortex-a53/base/Packages.gz'

class MirrorPolicyTests(unittest.TestCase):
    def test_exact_paths_direct_and_proxy(self):
        for root in policy.OFFICIAL:
            url=URL.replace(policy.OFFICIAL[0],root)+'?test=1'
            self.assertEqual(policy.request_urls(url,False),tuple(m+url[len(root):] for m in policy.DOMESTIC))
            self.assertEqual(policy.request_urls(url,True),(url,))
    def test_unrelated_sources_unchanged(self):
        for url in ('https://routermanager-updates.routermanager-update-gateway.workers.dev/bundle.zip','https://downloads.openwrt.org.evil/file'):
            self.assertEqual(policy.request_urls(url,False),(url,))
        self.assertIsNone(policy.source_path('https://downloads.openwrt.org:bad/file'))
    def test_proxy_bypass_uses_real_direct_and_domestic(self):
        with patch.object(policy,'proxy_bypass',return_value=True):
            plans=policy.route_requests(URL,[('proxy',Mock()),(DIRECT,ProxyHandler({}))])
        self.assertEqual(len(plans),len(policy.DOMESTIC))
        self.assertTrue(all(label==DIRECT and handler.proxies=={} for label,handler,_ in plans))
    def test_rejected_redirect_closes_response(self):
        response=Mock()
        domestic=policy.request_urls(URL,False)[0]
        with self.assertRaises(OSError):
            policy.SourceRedirectHandler(DIRECT,domestic).redirect_request(Request(domestic),response,302,'',{},URL)
        response.close.assert_called_once()

    def test_redirect_cannot_silently_bypass_proxy(self):
        with patch.object(policy,'proxy_bypass',return_value=True),self.assertRaises(OSError):
            policy.SourceRedirectHandler('proxy',URL).redirect_request(Request(URL),None,302,'',{},URL.replace('downloads.','archive.'))

    def test_redirect_family_and_https_enforced(self):
        domestic=policy.request_urls(URL,False)[0]
        for label,start,target in ((DIRECT,domestic,URL),('proxy',URL,domestic),(DIRECT,domestic,domestic.replace('https:','http:'))):
            with self.subTest(target=target),self.assertRaises(OSError):
                policy.SourceRedirectHandler(label,start).redirect_request(Request(start),None,302,'',{},target)

class MirrorDownloadTests(unittest.TestCase):
    def fetch(self,outcomes,plans=None,url=URL,cancel=None):
        self.calls=[];outcomes=iter(outcomes)
        def open_request(request,**kwargs):
            self.calls.append(request.full_url);result=next(outcomes)
            if callable(result):result=result()
            if isinstance(result,Exception):raise result
            response=Mock(status=200,headers={});response.read1.side_effect=[result,b'']
            context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
            return context
        with patch.object(feed,'_attempt_plans',return_value=plans or [(DIRECT,ProxyHandler({}))]),patch.object(policy,'proxy_bypass',return_value=False),patch.object(feed.urllib.request,'build_opener',return_value=Mock(open=open_request)):
            return feed.fetch_bytes(url,cancel_event=cancel)
    def missing(self):return HTTPError(URL,404,'missing',{},io.BytesIO())
    def test_missing_first_mirror_tries_next(self):
        self.assertEqual(self.fetch([self.missing(),b'ok']),b'ok')
        self.assertEqual(self.calls,list(policy.request_urls(URL,False)[:2]))
    def test_all_missing_retains_index_format_fallback(self):
        with self.assertRaises(feed.DownloadHTTPError):self.fetch([self.missing() for _ in policy.DOMESTIC])
        self.assertEqual(self.calls,list(policy.request_urls(URL,False)))
    def test_denied_domestic_mirror_does_not_block_healthy_next_mirror(self):
        denied=HTTPError(URL,403,'denied',{},io.BytesIO())
        self.assertEqual(self.fetch([denied,b'ok']),b'ok')
        self.assertEqual(self.calls,list(policy.request_urls(URL,False)[:2]))

    def test_mixed_timeout_and_missing_retains_format_fallback(self):
        with self.assertRaises(feed.DownloadHTTPError) as caught:
            self.fetch([self.missing(),TimeoutError(),self.missing(),TimeoutError()])
        self.assertEqual(caught.exception.code,404)

    def test_mixed_failures_actually_fall_back_to_plain_index(self):
        from modules.capture_services.openwrt.package_index_transport import fetch_package_index
        urls=[]
        def download(url,**kwargs):
            urls.append(url)
            outcomes=([self.missing(),TimeoutError(),self.missing(),TimeoutError()]
                      if url.endswith('.gz') else [b'Package: demo\n'])
            return self.fetch(outcomes,url=url)
        self.assertEqual(fetch_package_index(URL[:-3],download),b'Package: demo\n')
        self.assertEqual(urls,[URL,URL[:-3]])

    def test_cancel_at_last_missing_response_wins_over_missing_error(self):
        cancel=Event()
        def last_missing():
            cancel.set()
            return self.missing()
        with self.assertRaisesRegex(RuntimeError,'\u53d6\u6d88'):
            self.fetch([self.missing(),self.missing(),last_missing],cancel=cancel)

    def test_all_denied_stop_after_each_mirror_once(self):
        denied=[HTTPError(URL,403,'denied',{},io.BytesIO()) for _ in policy.DOMESTIC]
        with self.assertRaises(feed.DownloadHTTPError) as caught:self.fetch(denied)
        self.assertEqual(caught.exception.code,403)
        self.assertEqual(self.calls,list(policy.request_urls(URL,False)))

    def test_proxy_failure_then_direct_changes_source(self):
        self.assertEqual(self.fetch([TimeoutError(),b'ok'],[('proxy',Mock()),(DIRECT,ProxyHandler({}))]),b'ok')
        self.assertEqual(self.calls,[URL,policy.request_urls(URL,False)[0]])

class NativeSourceTests(unittest.TestCase):
    def test_proxy_uses_pc_before_touching_router_sources(self):
        manager=Mock()
        with patch('modules.network_policy.configured_proxy_channels',return_value=[object()]):
            with self.assertRaises(SignalError) as caught:
                with domestic_sources(manager,PackageManager('opkg'),Event(),Mock()):self.fail('proxy must use PC')
        self.assertEqual(caught.exception.code,'offline_retry');manager._execute.assert_not_called()
    def test_scoped_managers_cleanup_on_error(self):
        for name in ('opkg','apk'):
            manager=Mock();manager._execute.return_value='/tmp/lxg-feeds.'+'a'*32
            with patch('modules.network_policy.configured_proxy_channels',return_value=[]),patch('modules.capture_services.openwrt.temporary_package_sources.secrets.token_hex',return_value='a'*32):
                with self.assertRaisesRegex(RuntimeError,'install failed'):
                    with domestic_sources(manager,PackageManager(name),Event(),Mock()) as scoped:
                        for command in (scoped.update(),scoped.change(('lua',),simulate=True),scoped.change(('lua',))):
                            self.assertIn('/tmp/lxg-feeds.'+'a'*32+'/',command)
                            self.assertIn('OPKG_CONF_DIR=' if name=='opkg' else '--repositories-file',command)
                        raise RuntimeError('install failed')
            self.assertEqual(manager._execute.call_args_list[-1].args[0],'dependency_sources_cleanup')
            self.assertIn('/.owner',manager._execute.call_args_list[-1].args[1])
    def test_cancellation_not_converted(self):
        manager=Mock();manager._execute.side_effect=SignalError('cancelled','cancel')
        with patch('modules.network_policy.configured_proxy_channels',return_value=[]),patch('modules.capture_services.openwrt.temporary_package_sources.secrets.token_hex',return_value='a'*32):
            with self.assertRaises(SignalError) as caught:
                with domestic_sources(manager,PackageManager('opkg'),None,Mock()):pass
        self.assertEqual(caught.exception.code,'cancelled')
    def test_scripts_never_write_original_sources(self):
        for name in ('opkg','apk'):
            script=prepare_script(name,'a'*32)
            self.assertNotIn('> /etc/',script);self.assertIn('trap ',script);self.assertIn('exit 72',script)


class NativeShellTests(unittest.TestCase):
    def test_actual_scripts_copy_feeds_preserve_checks_and_remove_old_proxy(self):
        import subprocess
        import tempfile
        from pathlib import Path
        bash=Path('C:/Program Files/Git/bin/bash.exe')
        if not bash.is_file():self.skipTest('POSIX shell unavailable')
        for name in ('opkg','apk'):
            with self.subTest(manager=name),tempfile.TemporaryDirectory(dir=Path('build').resolve()) as directory:
                root=Path(directory)
                for sub in ('etc/opkg','etc/apk/repositories.d','lib/apk/repositories.d','tmp'):
                    (root/sub).mkdir(parents=True,exist_ok=True)
                if name=='opkg':
                    main=root/'etc/opkg.conf'
                    main.write_text('option check_signature 1\noption http_proxy http://old-proxy:8080\n',encoding='utf-8')
                    source=root/'etc/opkg/distfeeds.conf'
                    source.write_text('src/gz base '+URL.rsplit('/',1)[0]+'\n',encoding='utf-8')
                else:
                    source=root/'etc/apk/repositories.d/distfeeds.list'
                    source.write_text(URL.rsplit('/',1)[0]+'\n',encoding='utf-8')
                    (root/'lib/apk/repositories.d/distfeeds.list').write_text('https://shadowed.invalid/feed\n',encoding='utf-8')
                original=source.read_bytes()
                script=prepare_script(name,'a'*32)
                for prefix in ('/etc/opkg','/etc/apk','/lib/apk','/tmp/lxg-feeds'):
                    script=script.replace(prefix,root.as_posix()+prefix)
                result=subprocess.run([str(bash),'--noprofile','--norc','-s'],input=script,text=True,encoding='utf-8',capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                folder=Path(result.stdout.strip())
                self.assertTrue(folder.is_relative_to(root))
                text='\n'.join(f.read_text(encoding='utf-8') for f in folder.rglob('*') if f.is_file())
                self.assertIn(policy.DOMESTIC[0],text)
                self.assertNotIn('downloads.openwrt.org',text)
                self.assertNotIn('shadowed.invalid',text)
                self.assertNotIn('old-proxy',text)
                if name=='opkg':self.assertIn('option check_signature 1',text)
                self.assertEqual(source.read_bytes(),original)
                source.write_text('https://unrecognized.invalid/feed\n',encoding='utf-8')
                if name=='opkg':source.write_text('src/gz base https://unrecognized.invalid/feed\n',encoding='utf-8')
                failed=subprocess.run([str(bash),'--noprofile','--norc','-s'],input=script.replace('a'*32,'c'*32),text=True,encoding='utf-8',capture_output=True,timeout=10)
                self.assertEqual(failed.returncode,72,failed.stderr)
                self.assertEqual(len(list((root/'tmp').iterdir())),1)

class NativeEntryTests(unittest.TestCase):
    def test_signal_native_install_uses_shared_source_context(self):
        from modules.capture_services.openwrt.signal_manager import RouterSignalManager
        before=dict(lua=False,uci=True,ubus=True,jsonc=True,root=True,opkg=True,apk=False,overlay_kb=512)
        manager=RouterSignalManager(Mock())
        manager.probe_dependencies=Mock(side_effect=[before,dict(before,lua=True)])
        manager._execute=Mock(return_value='/tmp/lxg-feeds.'+'a'*32)
        manager._execute_stage=Mock(return_value='Installing lua (1) to root...')
        with patch('modules.network_policy.configured_proxy_channels',return_value=[]),patch('modules.capture_services.openwrt.temporary_package_sources.secrets.token_hex',return_value='a'*32),patch('modules.capture_services.openwrt.package_space.check_plan_space'):
            self.assertTrue(manager.install_dependencies(Event())['lua'])
        self.assertTrue(all('OPKG_CONF_DIR=' in call.args[1] for call in manager._execute_stage.call_args_list))
        self.assertEqual(manager._execute.call_args.args[0],'dependency_sources_cleanup')
