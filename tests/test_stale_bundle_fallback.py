"""Older verified bundle coverage must not block base or network sources."""
from types import SimpleNamespace as NS
from threading import Event
from unittest.mock import Mock, patch
import unittest
from modules.capture_services.openwrt.bundle_specs import bundle_spec
from modules.capture_services.openwrt.common_bundle_repository import BundleCoverageUnavailable, validate_catalog
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.installation_sources import install_with_sources, report_verified
from modules.capture_services.openwrt.signal_manager import SignalError


class StaleBundleFallbackTests(unittest.TestCase):
    def catalog(self):
        spec = bundle_spec('common')
        return dict(schema_version=1, purpose=spec.catalog_purpose, complete=True,
                    roots=sorted(spec.roots - {'curl'}))

    def local(self, wanted, builtin, tell):
        repo = Mock(kind='common')
        repo.profile.side_effect = lambda device: validate_catalog(self.catalog())
        manager = Mock()
        device = NS(distribution='OpenWrt', version='23.05.4', target='mediatek/filogic',
                    package_architecture='aarch64_cortex-a53', package_manager='opkg')
        with patch('modules.capture_services.openwrt.common_bundle_installer.CommonBundleRepository', return_value=repo), \
             patch('modules.capture_services.openwrt.common_bundle_installer.bundle_lock') as lock, \
             patch('modules.capture_services.openwrt.builtin_base_installer.install_builtin', builtin):
            return install_from_local(manager, wanted, device, None, True, Event(), tell)

    def test_stale_catalog_has_distinct_non_integrity_reason(self):
        with self.assertRaises(BundleCoverageUnavailable):
            validate_catalog(self.catalog())

    def test_old_full_bundle_then_successful_builtin_never_uses_network(self):
        for root in ('libuci-lua', 'wpa-cli'):
            tell, builtin, online, computer = Mock(), Mock(return_value=True), Mock(), Mock()
            source = install_with_sources(lambda: self.local((root,), builtin, tell), online, computer, tell)
            builtin.assert_called_once()
            online.assert_not_called();computer.assert_not_called()
            self.assertTrue(source)

    def test_both_base_roots_still_reach_router_then_computer_when_builtin_not_usable(self):
        for wanted in [('libuci-lua',), ('wpa-cli',), ('libuci-lua', 'wpa-cli')]:
            tell, builtin, computer = Mock(), Mock(return_value=False), Mock()
            online = Mock(side_effect=SignalError('offline_retry', 'router network unavailable'))
            install_with_sources(lambda: self.local(wanted, builtin, tell), online, computer, tell)
            builtin.assert_called_once();online.assert_called_once();computer.assert_called_once()

    def test_other_roots_skip_builtin_and_use_router_online(self):
        for root in ('curl', 'tmux'):
            tell, builtin, online, computer = Mock(), Mock(), Mock(), Mock()
            install_with_sources(lambda: self.local((root,), builtin, tell), online, computer, tell)
            builtin.assert_not_called();online.assert_called_once();computer.assert_not_called()

    def test_signature_corruption_and_cancel_never_become_missing_bundle(self):
        for error in (ValueError('Invalid signature'), ValueError('Object checksum mismatch'), InterruptedError('cancelled')):
            repo = Mock(kind='common');repo.profile.side_effect = error
            builtin, online, computer = Mock(), Mock(), Mock()
            with patch('modules.capture_services.openwrt.common_bundle_installer.CommonBundleRepository', return_value=repo), \
                 patch('modules.capture_services.openwrt.common_bundle_installer.bundle_lock'), \
                 patch('modules.capture_services.openwrt.builtin_base_installer.install_builtin', builtin):
                with self.assertRaises(SignalError):
                    install_with_sources(lambda: install_from_local(Mock(), ('wpa-cli',), NS(), None,
                                         True, Event(), Mock()), online, computer, Mock())
            builtin.assert_not_called();online.assert_not_called();computer.assert_not_called()

    def test_missing_source_summary_has_no_blank_route(self):
        tell = Mock();report_verified('', False, tell)
        self.assertNotIn('：。', tell.call_args.args[0])

    def test_rejected_signed_catalog_is_not_reused_as_a_loaded_repository(self):
        import base64, copy, hashlib, json, tempfile
        from pathlib import Path
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        from modules.app_update_services.manifest import canonical
        from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
        from tests.test_common_offline_bundle import fixture
        for rejection in ('old_coverage', 'malformed_profile', 'tampered_old_coverage'):
            with self.subTest(rejection=rejection), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                f = fixture(root/'source')
                catalog = copy.deepcopy(f.catalog)
                if rejection in ('old_coverage', 'tampered_old_coverage'):
                    catalog['roots'].remove('curl')
                else:
                    catalog['profiles'][0]['target'] = 123
                raw = json.dumps(catalog).encode('utf-8')
                payload = dict(f.payload, catalog_size=len(raw), catalog_sha256=hashlib.sha256(raw).hexdigest())
                if rejection == 'tampered_old_coverage':payload['catalog_sha256']='0'*64
                key = Ed25519PrivateKey.generate()
                public = base64.b64encode(key.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
                directory = root/'repository'/'versions'/payload['sha256']
                directory.mkdir(parents=True)
                (directory/'catalog.json').write_bytes(raw)
                receipt = dict(payload=payload, signature=base64.b64encode(key.sign(canonical(payload))).decode())
                (root/'repository'/'current.json').write_bytes(canonical(receipt))
                repo = CommonBundleRepository(root/'repository', public)
                for attempt in range(2):
                    with self.assertRaises(ValueError, msg=f'{rejection} attempt {attempt}') as caught:
                        repo.profile(f.device)
                    if rejection == 'old_coverage':
                        self.assertIsInstance(caught.exception,BundleCoverageUnavailable)
                    else:
                        self.assertNotIsInstance(caught.exception,BundleCoverageUnavailable)
                    self.assertIsNone(repo.catalog)
