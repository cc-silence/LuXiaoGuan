"""Alternate hcx builds require source integrity and a per-file user decision."""
import gzip,hashlib
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.hcx_candidate import candidate_confirmation
from modules.capture_services.openwrt.hcx_online import online_primary_package,primary_row
from modules.capture_services.openwrt.signal_manager import SignalError
from tests.test_hcx_online import original

class HcxCandidateTests(unittest.TestCase):
    def setUp(self):
        self.device=SimpleNamespace(package_manager='opkg',package_architecture='aarch64_cortex-a53')
        _,raw=original(self.device)
        self.raw=gzip.compress(gzip.decompress(raw),mtime=123)
        self.pinned=primary_row(self.device)
        self.cancel=Event()
        self.seen=[]

    def fetch(self,url,**kwargs):
        if url.startswith('https://raw.githubusercontent.com/'):
            raise RuntimeError('original unavailable')
        if url.endswith('/Packages.gz'):
            text=('Package: hcxdumptool\nVersion: 6.3.4-r1\nArchitecture: aarch64_cortex-a53\nFilename: '+self.pinned['filename']+
                  '\nSize: '+str(len(self.raw))+'\nSHA256sum: '+hashlib.sha256(self.raw).hexdigest()+'\n\n')
            return gzip.compress(text.encode())
        return self.raw

    def test_verified_repack_requires_confirmation_and_uses_its_hash(self):
        def approve(review):self.seen.append(review);return True
        with patch('modules.capture_services.openwrt.hcx_online.fetch_bytes',side_effect=self.fetch),candidate_confirmation(approve):
            with online_primary_package(self.device,self.cancel,Mock()) as (path,row):
                self.assertEqual(path.read_bytes(),self.raw)
                self.assertEqual(row['sha256'],hashlib.sha256(self.raw).hexdigest())
                self.assertEqual(row['version'],'6.3.4-r1')
        self.assertFalse(path.exists())
        self.assertEqual(len(self.seen),1)
        self.assertNotEqual(self.seen[0].candidate_sha256,self.seen[0].original_sha256)

    def test_decline_never_yields_package(self):
        with patch('modules.capture_services.openwrt.hcx_online.fetch_bytes',side_effect=self.fetch),candidate_confirmation(lambda _:False):
            with self.assertRaises(SignalError) as caught:
                with online_primary_package(self.device,self.cancel,Mock()):self.fail('declined')
        self.assertEqual(caught.exception.code,'candidate_declined')

    def test_missing_presenter_never_auto_approves(self):
        with patch('modules.capture_services.openwrt.hcx_online.fetch_bytes',side_effect=self.fetch):
            with self.assertRaises(SignalError) as caught:
                with online_primary_package(self.device,self.cancel,Mock()):self.fail('not approved')
        self.assertEqual(caught.exception.code,'candidate_declined')

    def test_source_hash_mismatch_never_asks_for_approval(self):
        callback=Mock(return_value=True)
        def wrong_index(url,**kwargs):
            data=self.fetch(url,**kwargs)
            if url.endswith('/Packages.gz'):
                return gzip.compress(gzip.decompress(data).replace(hashlib.sha256(self.raw).hexdigest().encode(),b'0'*64))
            return data
        with patch('modules.capture_services.openwrt.hcx_online.fetch_bytes',side_effect=wrong_index),candidate_confirmation(callback):
            with self.assertRaises(SignalError):
                with online_primary_package(self.device,self.cancel,Mock()):self.fail('bad hash')
        callback.assert_not_called()

    def test_index_architecture_mismatch_never_asks_for_approval(self):
        callback=Mock(return_value=True)
        def wrong_index(url,**kwargs):
            data=self.fetch(url,**kwargs)
            if url.endswith('/Packages.gz'):
                return gzip.compress(gzip.decompress(data).replace(b'Architecture: aarch64_cortex-a53',b'Architecture: other_arch'))
            return data
        with patch('modules.capture_services.openwrt.hcx_online.fetch_bytes',side_effect=wrong_index),candidate_confirmation(callback):
            with self.assertRaises(SignalError):
                with online_primary_package(self.device,self.cancel,Mock()):self.fail('bad architecture')
        callback.assert_not_called()

    def test_cancellation_during_approval_never_yields_package(self):
        def approve(_):self.cancel.set();return True
        with patch('modules.capture_services.openwrt.hcx_online.fetch_bytes',side_effect=self.fetch),candidate_confirmation(approve):
            with self.assertRaises(SignalError) as caught:
                with online_primary_package(self.device,self.cancel,Mock()):self.fail('cancelled')
        self.assertEqual(caught.exception.code,'cancelled')

    def test_larger_valid_same_version_repack_can_be_confirmed(self):
        _,raw=original(self.device)
        self.raw=gzip.compress(gzip.decompress(raw),compresslevel=0,mtime=1)
        self.assertGreater(len(self.raw),self.pinned['size'])
        with patch('modules.capture_services.openwrt.hcx_online.fetch_bytes',side_effect=self.fetch),candidate_confirmation(lambda _:True):
            with online_primary_package(self.device,self.cancel,Mock()) as (path,row):
                self.assertEqual(row['size'],len(self.raw))

    def test_file_changed_after_approval_is_rejected(self):
        import tempfile
        from modules.capture_services.openwrt.hcx_candidate import confirm_alternate
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/self.pinned['filename'];path.write_bytes(self.raw)
            def approve(_):path.write_bytes(b'changed');return True
            with candidate_confirmation(approve):
                with self.assertRaises(SignalError):
                    confirm_alternate(self.device,path,self.pinned,self.fetch,self.cancel,self.pinned['official_url'])

    def test_embedded_control_identity_mismatch_is_never_offered(self):
        callback=Mock(return_value=True)
        with patch('modules.capture_services.openwrt.hcx_online.fetch_bytes',side_effect=self.fetch),patch('modules.capture_services.openwrt.package_space.ipk_metadata',return_value={'Package':'hcxdumptool','Version':'6.3.4-r1','Architecture':'wrong'}),candidate_confirmation(callback):
            with self.assertRaises(SignalError):
                with online_primary_package(self.device,self.cancel,Mock()):self.fail('wrong identity')
        callback.assert_not_called()
