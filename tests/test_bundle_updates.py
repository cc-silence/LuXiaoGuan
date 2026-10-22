"""Local package detection and source progress remain explicit and offline-testable."""
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from tests.test_common_offline_bundle import fixture
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.bundle_status import local_bundle_status, compare_bundle
from modules.capture_services.openwrt.installation_sources import install_with_sources, report_verified
from modules.capture_services.openwrt.signal_manager import SignalError


class BundleUpdateTests(unittest.TestCase):
    def test_missing_valid_and_corrupt_local_resource_states(self):
        for kind in ('common','capture'):
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder); source=fixture(root/'source',kind=kind); target=root/'resources'
                self.assertFalse(local_bundle_status(Event(),kind=kind,root=target,public_key=source.public)['installed'])
                prepare_archive(source.archive,source.envelope,target,Event(),public_key=source.public,kind=kind)
                state=local_bundle_status(Event(),kind=kind,root=target,public_key=source.public)
                self.assertTrue(state['installed'])
                self.assertEqual(compare_bundle(state,source.payload),'current')
                path=target/'versions'/source.payload['sha256']/'catalog.json'
                path.write_text('{}',encoding='utf-8')
                self.assertFalse(local_bundle_status(Event(),kind=kind,root=target,public_key=source.public)['installed'])

    def test_update_comparison_and_no_downgrade(self):
        local=dict(installed=True,revision='a'*64,published_at='2026-10-21T01:00:00+00:00')
        self.assertEqual(compare_bundle(local,dict(sha256='a'*64)),'current')
        self.assertEqual(compare_bundle(local,dict(sha256='b'*64,published_at='2026-10-22T01:00:00+00:00')),'update')
        self.assertEqual(compare_bundle(local,dict(sha256='b'*64,published_at='2026-10-20T01:00:00+00:00')),'local_newer')

    def test_all_installation_routes_and_verified_summary(self):
        for local_result, online_error, expected in ((True,None,0),(False,None,1),
                                                    (False,SignalError('offline_retry','DNS failed'),2)):
            tell=Mock(); local=Mock(return_value=local_result); online=Mock(side_effect=online_error);pc=Mock()
            source=install_with_sources(local,online,pc,tell)
            local.assert_called_once()
            self.assertEqual(online.call_count,expected>=1)
            self.assertEqual(pc.call_count,expected==2)
            report_verified(source,True,tell)
            text='\n'.join(call.args[0] for call in tell.call_args_list)
            self.assertIn(source,text)
            self.assertIn('1/3',text)
            if expected>=1:self.assertIn('2/3',text)
            if expected==2:self.assertIn('3/3',text)
            self.assertEqual(tell.call_args.args[1],'success')

    def test_cancel_and_conflict_do_not_fall_back_or_report_success(self):
        for code in ('cancelled','environment'):
            tell=Mock();pc=Mock()
            with self.assertRaises(SignalError):
                install_with_sources(lambda:False,Mock(side_effect=SignalError(code,'stop')),pc,tell)
            pc.assert_not_called()
            self.assertFalse(any(call.args[1]=='success' for call in tell.call_args_list))


class BundleServiceUpdateTests(unittest.TestCase):
    def service(self):
        from PySide6.QtCore import QObject, Signal
        from modules.capture_services.common_bundle_service import CommonBundleDownloadService
        class Owner(QObject):
            activityChanged=Signal()
            settingsMessageGenerated=Signal(str,str)
            _shutting_down=False
            is_busy=False
            def _start_openwrt_worker(self,*args):self.pending=args
        owner=Owner()
        service=CommonBundleDownloadService(owner)
        return owner,service

    def test_current_resource_does_not_offer_or_start_download(self):
        owner,service=self.service()
        self.assertTrue(service.inspect())
        offers=[];done=[]
        service.offerReady.connect(offers.append)
        service.finished.connect(lambda *args:done.append(args))
        service._inspected(dict(comparison='current',local_status=dict(installed=True,label='revision-a'),payload={}))
        self.assertEqual(offers,[])
        self.assertEqual(service.phase,'idle')
        self.assertIsNone(service.offer)
        self.assertTrue(done[0][0])
        self.assertFalse(service.start())

    def test_new_revision_requires_confirmation_and_uses_exact_manifest(self):
        owner,service=self.service()
        service.inspect()
        offers=[];service.offerReady.connect(offers.append)
        payload=dict(sha256='a'*64,size=100)
        service._inspected(dict(comparison='update',local_status=dict(installed=True,label='old'),
                                payload=payload,manifest=b'signed revision'))
        self.assertEqual(service.phase,'confirming')
        self.assertEqual(offers,[payload])
        self.assertTrue(service.start())
        self.assertEqual(service.phase,'downloading')
        self.assertEqual(owner.pending[1].keywords['manifest'],b'signed revision')

    def test_local_detection_is_background_only_and_does_not_contact_network(self):
        owner,service=self.service()
        self.assertTrue(service.refresh_local())
        self.assertEqual(service.phase,'local_check')
        self.assertIn('local_bundle_status',owner.pending[1].func.__name__)
        owner.pending[3](dict(installed=True,label='revision-a',revision='a'*64))
        self.assertEqual(service.phase,'idle')
        self.assertTrue(service.local_status['installed'])
