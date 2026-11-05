"""Task-local mirror preferences and human-readable source diagnostics."""
import io
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler
from modules.capture_services.openwrt import signal_package_feed as feed
from modules.capture_services.openwrt import package_mirror_policy as policy
from modules.capture_services.openwrt.package_download_diagnostics import AttemptDiagnostics

DIRECT='\u76f4\u8fde'
URL=policy.OFFICIAL[0]+'/releases/23.05.4/packages/aarch64_cortex-a53/base/Packages.gz'

class MirrorPreferenceTests(unittest.TestCase):
    def test_default_order_and_native_source_include_aliyun(self):
        self.assertEqual(policy.DOMESTIC[:2],('https://mirrors.ustc.edu.cn/openwrt','https://mirrors.aliyun.com/openwrt'))
        from modules.capture_services.openwrt.temporary_package_sources import prepare_script
        script=prepare_script('opkg','a'*32)
        self.assertIn('mirrors[.]aliyun[.]com',script)
        self.assertIn(policy.DOMESTIC[0],script)

    def test_success_preferred_and_repeated_denials_demoted(self):
        preference=policy.MirrorPreference()
        for _ in range(10):preference.failed_http(DIRECT,policy.DOMESTIC[0]+'/file',403)
        self.assertEqual(preference.order()[-1],policy.DOMESTIC[0])
        self.assertEqual(preference.denials[policy.DOMESTIC[0]],2)
        preference.succeeded(DIRECT,policy.DOMESTIC[1]+'/file')
        self.assertEqual(preference.order()[0],policy.DOMESTIC[1])
        preference.succeeded(DIRECT,policy.DOMESTIC[0]+'/file')
        self.assertEqual(preference.order()[0],policy.DOMESTIC[0])

    def test_missing_files_do_not_blacklist_host_or_modify_proxy_route(self):
        preference=policy.MirrorPreference()
        for _ in range(10):preference.failed_http(DIRECT,policy.DOMESTIC[0]+'/file',404)
        self.assertEqual(preference.order(),policy.DOMESTIC)
        with patch.object(policy,'proxy_bypass',return_value=False):
            plans=policy.route_requests(URL,[('http://localhost:1081',object())],tuple(reversed(policy.DOMESTIC)))
        self.assertEqual(plans[0][2],URL)
        self.assertEqual(len(plans),1)

    def test_summary_identifies_source_and_http_status_without_credentials(self):
        diagnostic=AttemptDiagnostics(policy.DOMESTIC[1]+'/file','\u76f4\u8fde (host)',5,0)
        diagnostic.status=403
        self.assertIn('\u963f\u91cc\u4e91\u955c\u50cf',diagnostic.short_start())
        self.assertIn('403',diagnostic.short_result(True))
        self.assertIn('\u963f\u91cc\u4e91\u955c\u50cf',diagnostic.short_result(False))

class TaskSessionTests(unittest.TestCase):
    def test_successful_mirror_reused_between_files_then_reset_next_task(self):
        calls=[];messages=[]
        def open_request(request,**kwargs):
            calls.append(request.full_url)
            if request.full_url.startswith(policy.DOMESTIC[0]+'/'):
                raise HTTPError(request.full_url,403,'denied',{},io.BytesIO())
            response=Mock(status=200,headers={});response.read1.side_effect=[b'ok',b'']
            context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
            return context
        with patch.object(feed,'_attempt_plans',return_value=[(DIRECT,ProxyHandler({}))]),patch.object(feed.urllib.request,'build_opener',return_value=Mock(open=open_request)):
            with feed.download_session(lambda text,*_:messages.append(text)):
                self.assertEqual(feed.fetch_bytes(URL),b'ok')
                self.assertEqual(feed.fetch_bytes(URL.replace('Packages.gz','package.ipk')),b'ok')
            self.assertEqual(calls,[policy.DOMESTIC[0]+URL[len(policy.OFFICIAL[0]):],policy.DOMESTIC[1]+URL[len(policy.OFFICIAL[0]):],policy.DOMESTIC[1]+URL.replace('Packages.gz','package.ipk')[len(policy.OFFICIAL[0]):]])
            self.assertIsNone(feed._download_state.get())
            feed.fetch_bytes(URL)
            self.assertTrue(calls[3].startswith(policy.DOMESTIC[0]+'/'))
        self.assertTrue(any('\u963f\u91cc\u4e91\u955c\u50cf' in message for message in messages))

    def test_batch_install_owns_one_session_for_all_packages_and_restores_it(self):
        from modules.capture_services.openwrt.dependency_batch_install import install_packages
        def run(*args):
            state=feed._download_state.get()
            self.assertIsNotNone(state)
            with feed.download_session():self.assertIs(feed._download_state.get(),state)
            return 'done'
        with patch('modules.capture_services.openwrt.dependency_batch_install._install_packages',side_effect=run):
            self.assertEqual(install_packages(None,None,None,None,None,None,Mock()),'done')
        self.assertIsNone(feed._download_state.get())

    def test_native_source_uses_same_task_mirror_preference(self):
        from modules.capture_services.openwrt.package_manager import PackageManager
        from modules.capture_services.openwrt.temporary_package_sources import domestic_sources
        from modules.capture_services.openwrt.command_builder import quote_argument
        manager=Mock();manager._execute.side_effect=['/tmp/lxg-feeds.'+'a'*32,'']
        tell=Mock()
        with feed.download_session(tell):
            feed._download_state.get()['mirror_preference'].succeeded(DIRECT,policy.DOMESTIC[1]+'/file')
            with patch('modules.network_policy.configured_proxy_channels',return_value=[]),patch('modules.capture_services.openwrt.temporary_package_sources.secrets.token_hex',return_value='a'*32):
                with domestic_sources(manager,PackageManager('opkg'),None,tell):pass
        script=manager._execute.call_args_list[0].args[1]
        self.assertIn('allowed='+quote_argument(policy.DOMESTIC[1]+'/'),script)
        self.assertTrue(any('\u963f\u91cc\u4e91\u955c\u50cf' in call.args[0] for call in tell.call_args_list))
