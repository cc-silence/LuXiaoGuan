"""Streamed publishing retries only configured proxy routes and immutable bytes."""
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from scripts.publishing.storage_api import StorageAPI, StorageTransportError

class UploadTests(unittest.TestCase):
    def run_upload(self,key,first_error):
        api=StorageAPI.__new__(StorageAPI);api.session=NS(proxies={'https':'original'})
        calls=[]
        def send(method,suffix,**kwargs):
            calls.append((dict(api.session.proxies),kwargs['data'].read()))
            if len(calls)==1:raise first_error
            return b'ok'
        api._request=send
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'upload.zip';path.write_bytes(b'complete-data')
            routes=[NS(proxy_url='http://127.0.0.1:1081'),NS(proxy_url='socks5h://127.0.0.1:1080')]
            with patch('modules.network_policy.configured_proxy_channels',return_value=routes):
                if isinstance(first_error,StorageTransportError) and not key.startswith('channel/'):
                    self.assertEqual(api.put(path,key,'application/zip'),b'ok')
                    self.assertEqual(len(calls),2)
                    self.assertEqual(calls[0][1],calls[1][1])
                    self.assertTrue(calls[1][0]['https'].startswith('socks5h://'))
                else:
                    with self.assertRaises(ValueError):api.put(path,key,'application/zip')
                    self.assertEqual(len(calls),1)
        self.assertEqual(api.session.proxies,{'https':'original'})
    def test_immutable_network_failure_rewinds_and_uses_configured_socks(self):
        self.run_upload('files/hash/package.zip',StorageTransportError('network'))
    def test_authentication_rejection_never_retries(self):
        self.run_upload('files/hash/package.zip',ValueError('HTTP 401'))
    def test_uncertain_channel_write_never_retries(self):
        self.run_upload('channel/manifest.json',StorageTransportError('network'))

    def test_upload_stream_is_bounded_and_has_deadline(self):
        import io,time,requests
        from scripts.publishing.storage_api import UploadStream
        stream=UploadStream(io.BytesIO(b'x'*100000),time.monotonic()+30)
        self.assertEqual(len(stream.read()),65536)
        stream.deadline=0
        with self.assertRaises(requests.Timeout):stream.read(1)

    def test_upload_stops_without_configured_proxies_even_if_environment_has_one(self):
        api=StorageAPI.__new__(StorageAPI);api.session=NS(proxies={'https':'http://unexpected'})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'upload.zip';path.write_bytes(b'data')
            with patch('modules.network_policy.configured_proxy_channels',return_value=[]),patch.object(api,'_request') as send:
                with self.assertRaises(ValueError):api.put(path,'files/hash/package.zip','application/zip')
                send.assert_not_called()

    def test_all_proxy_failures_never_attempt_a_direct_upload(self):
        api=StorageAPI.__new__(StorageAPI);api.session=NS(proxies={'https':'original'})
        calls=[]
        def send(*args,**kwargs):
            calls.append(dict(api.session.proxies));raise StorageTransportError('network')
        api._request=send
        routes=[NS(proxy_url='http://127.0.0.1:1081'),NS(proxy_url='socks5h://127.0.0.1:1080')]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'upload.zip';path.write_bytes(b'data')
            with patch('modules.network_policy.configured_proxy_channels',return_value=routes),patch(
                    'modules.network_policy.allowed_channels',return_value=[*routes,NS(proxy_url='')]) as generic:
                with self.assertRaises(StorageTransportError):api.put(path,'files/hash/package.zip','application/zip')
                generic.assert_not_called()
        self.assertEqual(len(calls),2)
        self.assertTrue(all(row.get('https') for row in calls))
        self.assertEqual(api.session.proxies,{'https':'original'})
