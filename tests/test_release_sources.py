import hashlib
from pathlib import Path
import unittest
from unittest.mock import Mock, MagicMock, patch
from modules.app_update_services.sources import *

class SourceTests(unittest.TestCase):
    def test_no_code_never_selects_alpha(self):
        routes=manifest_routes(GATEWAY+'/manifest',app_manifest('win-x64'),channels=[])
        self.assertEqual(len(routes),1)
        self.assertNotIn('alpha',routes[0].url)
        self.assertFalse(routes[0].proxy_url)

    def test_proxy_then_github_direct(self):
        p=NetworkChannel('http_proxy','test','http://localhost:8080')
        routes=manifest_routes(GATEWAY+'/manifest',app_manifest('win-x64',True),channels=[p,NetworkChannel('direct','direct')])
        self.assertEqual([r.source for r in routes],['cloudflare','github'])
        self.assertEqual(routes[1].proxy_url,'')
        self.assertEqual(len(manifest_routes(GATEWAY+'/manifest',app_manifest('win-x64'),mode='direct',channels=[p])),1)

    def test_signed_mirror_download_routes(self):
        gh=asset_url('app-win-x64-1.0.47_alpha','test.zip')
        p=NetworkChannel('http_proxy','test','http://localhost:8080')
        routes=package_routes(GATEWAY+'/files/test.zip',[gh],[p])
        self.assertEqual([(r.url,r.proxy_url) for r in routes],[(GATEWAY+'/files/test.zip',p.proxy_url),(gh,'')])

    def test_testing_policy(self):
        payload={'testing_code_sha256':hashlib.sha256(b'unit-test-code').hexdigest()}
        validate_testing(payload,'unit-test-code')
        for code in ('','wrong-code'):
            with self.assertRaises(ValueError):validate_testing(payload,code)
        with self.assertRaises(ValueError):validate_testing({},'unit-test-code')

    def test_redirect_allowlist_and_credentials(self):
        initial=asset_url('update-channels','win-x64-stable.json')
        self.assertTrue(github_url(initial))
        for bad in ('http://release-assets.githubusercontent.com/file','https://evil.test/file','https://github.com/other/repo/file','https://user:pass@release-assets.githubusercontent.com/a'):
            with self.assertRaises(ValueError):redirect_url(initial,bad)
        session=Mock()
        with self.assertRaises(ValueError):
            with release_response(session,initial,headers={'X-RouterManager-Test-Code':'never-send'}):pass
        session.get.assert_not_called()
        first=MagicMock(status_code=302,headers={'Location':'https://release-assets.githubusercontent.com/file?token=public'})
        second=MagicMock(status_code=200)
        first.__enter__.return_value=first;second.__enter__.return_value=second
        session.get.side_effect=[first,second]
        with release_response(session,initial,headers={}) as response:self.assertIs(response,second)
        first.__exit__.assert_called_once();second.__exit__.assert_called_once()

    def test_offline_host_is_fixed_and_identity_bound(self):
        url=offline_asset('common','a'*64,'RouterManager_OpenWrt_Common_Offline_test.zip')
        self.assertTrue(github_url(url))
        self.assertFalse(github_url(url.replace(REPOSITORY,'another/repo')))
        with self.assertRaises(ValueError):offline_asset('unknown','a'*64,'test.zip')

if __name__=='__main__':unittest.main()


class CheckerSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def check_request(self, code, proxies, legacy_config=False):
        from modules.app_update_services.checker import UpdateChecker
        from pathlib import Path
        import tempfile,json
        with tempfile.TemporaryDirectory() as temp:
            config=Path(temp)/'channel.json'
            data=dict(schema=1,enabled=True,manifest_url=GATEWAY+'/manifest')
            if not legacy_config:data['github_mirror']=True
            config.write_text(json.dumps(data),encoding='utf-8')
            with patch('modules.app_update_services.checker.QNetworkAccessManager') as manager,patch('modules.app_update_services.sources.configured_proxy_channels',return_value=proxies):
                checker=UpdateChecker('1.0.46_alpha',config)
                checker.test_code=code
                self.assertTrue(checker.check())
                request=manager.return_value.get.call_args.args[0]
                requests=[request]
                if proxies:
                    checker._next_channel()
                    requests.append(manager.return_value.get.call_args.args[0])
                checker.cancel()
                return requests

    def test_without_code_does_not_request_alpha_or_send_code(self):
        requests=self.check_request('',[])
        self.assertEqual(requests[0].url().toString(),app_manifest('win-x64'))
        self.assertFalse(requests[0].hasRawHeader(b'X-RouterManager-Test-Code'))

    def test_code_sent_only_to_cf_even_after_failover(self):
        requests=self.check_request('unit-test-code',[NetworkChannel('http_proxy','test','http://127.0.0.1:8080')])
        self.assertEqual(bytes(requests[0].rawHeader(b'X-RouterManager-Test-Code')),b'unit-test-code')
        self.assertEqual(requests[1].url().toString(),app_manifest('win-x64',True))
        self.assertFalse(requests[1].hasRawHeader(b'X-RouterManager-Test-Code'))


class PublisherBoundaryTests(unittest.TestCase):
    def test_wrong_account_cannot_reach_repository(self):
        from scripts.publishing import github_auth
        with patch.object(github_auth,'api',return_value={'login':'wrong-account'}) as api:
            with self.assertRaises(ValueError):github_auth.verify()
            self.assertEqual(api.call_count,1)

    def test_offline_history_never_enters_cf_deletion_plan(self):
        from scripts.publishing.retention import offline_candidates
        keys={'offline/openwrt-common/files/old.zip':{},'offline/openwrt-capture/manifests/old.json':{}}
        protected,eligible=offline_candidates(None,keys,{})
        self.assertEqual(protected,set(keys));self.assertEqual(eligible,[])

    def test_new_offline_publication_never_calls_cf_uploader(self):
        from scripts.publishing import mirrored
        from types import SimpleNamespace
        import tempfile,json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'RouterManager_OpenWrt_Common_Offline_test.zip';archive.write_bytes(b'zip')
            manifest=root/'old.json';manifest.write_bytes(b'old')
            payload=dict(sha256='a'*64,url='old',profiles=1,size=3)
            with patch.object(mirrored,'ROOT',root),patch.object(mirrored.offline,'prepare',return_value=(archive,manifest,payload)),patch.object(mirrored,'sign',side_effect=lambda p:json.dumps(p).encode()),patch.object(mirrored,'verify_bundle_manifest'),patch.object(mirrored.gh,'ensure_repository'),patch.object(mirrored.gh,'upload_immutable'),patch.object(mirrored.gh,'switch_channel',return_value=offline_manifest('common')),patch.object(mirrored.gh,'retire_slot'),patch('scripts.openwrt_packages.support_inventory.write_inventory'),patch('scripts.publishing.cloudflare.upload') as cf:
                result=mirrored.publish_offline.__wrapped__('common',archive,root/'coverage.json')
                self.assertTrue(result['archive_url'].startswith(RELEASES))
                self.assertTrue(result['cloudflare_offline_preserved'])
                cf.assert_not_called()

    # defined separately below to keep the helper's public scope explicit

class LegacyConfigurationTests(CheckerSourceTests):
    def test_upgrading_keeps_old_config_but_enables_new_official_route(self):
        requests=self.check_request('',[],legacy_config=True)
        self.assertEqual(requests[0].url().toString(),app_manifest('win-x64'))


class SignedEligibilityTests(unittest.TestCase):
    def test_github_alpha_requires_matching_signed_code(self):
        from tests.test_app_updates import signed,PAYLOAD,PUBLIC
        from types import SimpleNamespace
        from modules.app_update_services.checker import UpdateChecker
        from PySide6.QtNetwork import QNetworkReply
        raw=signed(dict(PAYLOAD,testing_code_sha256=hashlib.sha256(b'unit-test-code').hexdigest()))
        for code,accepted in [('unit-test-code',True),('incorrect-code',False)]:
            reply=Mock(spec=QNetworkReply);reply.attribute.return_value=200;reply.error.return_value=QNetworkReply.NoError;reply.rawHeader.return_value=b''
            checker=SimpleNamespace(_reply=reply,_read=Mock(),_stopped=False,_error='',_stage='manifest',_data=bytearray(raw),_source='github',test_code=code,public_key=PUBLIC,current_version='2.0.8_alpha',_finish=Mock())
            UpdateChecker._received(checker)
            args=checker._finish.call_args.kwargs
            self.assertEqual('release' in args,accepted)
            self.assertEqual('error' in args,not accepted)


class LegacyPublisherEntryTests(unittest.TestCase):
    def test_legacy_offline_entry_uses_the_new_github_coordinator(self):
        from scripts.publishing import offline
        with patch('scripts.publishing.mirrored.publish_offline',return_value={'status':'verified'}) as publish:
            self.assertEqual(offline.publish('common','archive','proof'),{'status':'verified'})
            publish.assert_called_once_with('common','archive','proof',min_app_version=None)


class CloudflarePreservationTests(unittest.TestCase):
    def test_low_level_delete_also_refuses_old_offline_objects(self):
        from scripts.publishing.storage_api import StorageAPI
        api=StorageAPI.__new__(StorageAPI)
        api._request=Mock()
        for key in ('offline/openwrt-common/files/old.zip','offline/openwrt-capture/manifests/old.json'):
            with self.assertRaisesRegex(ValueError,'preserved'):
                api.delete({'key':key,'etag':'old'})
        api._request.assert_not_called()


class OfflineProxySelectionTests(unittest.TestCase):
    def test_offline_github_honors_selected_proxy_and_direct_channels(self):
        url=offline_manifest('capture')
        proxy=NetworkChannel('http_proxy','proxy','http://127.0.0.1:8080')
        direct=NetworkChannel('direct','direct')
        routes=package_routes(url,channels=[proxy,direct],github_direct=False)
        self.assertEqual([r.proxy_url for r in routes],[proxy.proxy_url,''])
        self.assertEqual([r.url for r in routes],[url,url])
        self.assertEqual([r.proxy_url for r in package_routes(url,channels=[proxy],github_direct=False)],[proxy.proxy_url])

    def test_software_github_remains_direct(self):
        url=asset_url('app-win-x64-1.0.48_alpha','app.zip')
        proxy=NetworkChannel('http_proxy','proxy','http://127.0.0.1:8080')
        self.assertEqual([r.proxy_url for r in package_routes(url,channels=[proxy])],[''])


class GithubRetentionTests(unittest.TestCase):
    def test_shared_commit_timestamp_does_not_protect_old_software(self):
        from scripts.publishing import github_release as gh
        base=dict(created_at='same-commit-time',body='Signed LuXiaoGuan release assets.')
        rows=[dict(base,tag_name='app-win-x64-1.0.48_alpha',published_at='2026-11-06T01:30:00Z'),dict(base,tag_name='app-win-x64-1.0.47_alpha',published_at='2026-11-06T01:10:00Z')]
        with patch.object(gh,'api',return_value=rows),patch.object(gh,'release',return_value={'assets':[]}),patch.object(gh,'command') as command:
            gh.retire_slot('app-win-x64-','app-win-x64-1.0.48_alpha','alpha')
            command.assert_called_once_with('release','delete','app-win-x64-1.0.47_alpha','--repo',REPOSITORY,'--cleanup-tag','--yes')

    def test_offline_history_uses_publication_time_with_shared_commit(self):
        from scripts.publishing import github_release as gh
        base=dict(created_at='same-commit-time',body='Signed LuXiaoGuan release assets.')
        current='offline-common-'+'a'*64;old='offline-common-'+'b'*64
        rows=[dict(base,tag_name=current,published_at='2026-11-06T01:30:00Z'),dict(base,tag_name=old,published_at='2026-11-06T01:10:00Z')]
        with patch.object(gh,'api',return_value=rows),patch.object(gh,'release',return_value={'assets':[]}),patch.object(gh,'command') as command:
            gh.retire_slot('offline-common-',current)
            self.assertEqual(command.call_args.args[2],old)
