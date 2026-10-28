"""Signed common/capture bundles remain verified after proxy-to-direct fallback."""
import tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
import requests
from tests.test_common_offline_bundle import fixture
from modules.network_policy import NetworkChannel
from modules.capture_services.openwrt.common_bundle_download import _receive,download_bundle
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest


class OfflineBundleRouteFallbackTests(unittest.TestCase):
    def test_manifest_and_archive_fall_back_to_direct_for_both_bundle_types(self):
        for kind in ('common','capture'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                f=fixture(Path(folder)/'source',kind=kind)
                session=MagicMock();session.__enter__.return_value=session
                channels=[NetworkChannel('http_proxy','fixture proxy','http://broken:1'),NetworkChannel('direct','direct')]
                def get(url,**kwargs):
                    if kwargs['proxies']:raise requests.exceptions.ProxyError('fixture proxy down')
                    response=MagicMock();response.__enter__.return_value=response;response.status_code=200
                    response.iter_content.return_value=[f.envelope if url.endswith('/manifest') else f.archive.read_bytes()]
                    return response
                session.get.side_effect=get
                bridge=MagicMock();bridge.__enter__.return_value=bridge
                bridge.http_proxy.side_effect=lambda value:value
                with patch('requests.Session',return_value=session),patch('modules.http_socks_bridge.ProxyBridgePool',return_value=bridge),patch(
                        'modules.capture_services.openwrt.common_bundle_download.verify_bundle_manifest',side_effect=lambda raw,kind='common',**kwargs:verify_bundle_manifest(raw,f.public,kind=kind)),patch(
                        'modules.capture_services.openwrt.bundle_archive_cleanup.discard_prepared_archive',return_value=False):
                    event=threading.Event()
                    raw=_receive('https://fixture/manifest',None,65536,event,lambda *_:None,time.monotonic()+30,channels)
                    result=download_bundle(event,lambda *_:None,lambda *_:None,root=Path(folder)/'resource',channels=channels,kind=kind,manifest=raw)
                self.assertEqual(result['sha256'],f.payload['sha256'])
                self.assertFalse(session.trust_env)
                self.assertEqual([bool(call.kwargs['proxies']) for call in session.get.call_args_list],[True,False,True,False])
                self.assertTrue((Path(result['content_path'])/'catalog.json').is_file())
                for call in session.get.call_args_list:self.assertNotIn('verify',call.kwargs)
