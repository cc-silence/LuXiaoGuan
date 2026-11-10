"""Signed two-bundle reuse regressions without a router or public network."""
import base64
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from modules.app_update_services.manifest import canonical
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.shared_bundle_packages import SharedBundlePackages
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.signal_manager import SignalError
from tests.test_common_offline_bundle import fixture


def prepare(root, kind, apk, extra=(), need=None):
    f = fixture(root / (kind + '-source'), apk, kind)
    with zipfile.ZipFile(f.archive) as z:
        objects = {n: z.read(n) for n in z.namelist() if n != 'catalog.json'}
    for name, version in extra:
        data = ('shared:' + name + ':' + version).encode()
        sha = hashlib.sha256(data).hexdigest()
        row = dict(name=name, version=version, filename=name + '-' + version + ('.apk' if apk else '.ipk'),
                   architecture=f.profile['architecture'], size=len(data), sha256=sha, blob='objects/' + sha,
                   feed='target', depends=[], provides=[], url='https://downloads.openwrt.org/fixture/' + name)
        f.profile['packages'].append(row)
        objects[row['blob']] = data
    if need:
        next(row for row in f.profile['packages'] if row['name'] == need[0])['depends'].extend(need[1] if isinstance(need[1], list) else [need[1]])
    raw = json.dumps(f.catalog).encode()
    with zipfile.ZipFile(f.archive, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('catalog.json', raw)
        for name, data in objects.items(): z.writestr(name, data)
    sha = hashlib.sha256(f.archive.read_bytes()).hexdigest()
    f.payload.update(sha256=sha, size=f.archive.stat().st_size, catalog_size=len(raw),
                     catalog_sha256=hashlib.sha256(raw).hexdigest(),
                     unpacked_size=len(raw) + sum(map(len, objects.values())))
    f.payload['url'] = f.payload['url'].rsplit('/', 2)[0] + '/' + sha + '/' + f.archive.name
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    envelope = canonical(dict(payload=f.payload, signature=base64.b64encode(private.sign(canonical(f.payload))).decode()))
    resource = root / (kind + '-resource')
    prepare_archive(f.archive, envelope, resource, Event(), public_key=public, kind=kind)
    repo = CommonBundleRepository(resource, public, kind=kind)
    return f, repo


class SharedBundleTests(unittest.TestCase):
    def test_ipk_reuses_in_both_directions_without_network_or_extra_roots(self):
        for kind, wanted in (('common', 'tmux'), ('capture', 'aircrack-ng')):
            with self.subTest(kind=kind), TemporaryDirectory() as temp:
                root = Path(temp)
                f, primary = prepare(root, kind, False, need=(wanted, 'shared-lib'))
                _, donor = prepare(root, 'capture' if kind == 'common' else 'common', False,
                                   extra=(('shared-lib', '1'),))
                manager = NS(command_runner=Mock(), _execute_stage=Mock(return_value='libc - 1\n'),
                             _install_offline=Mock())
                observed = []
                def install(*args, **kwargs):
                    observed.extend(p.read_bytes() for p in kwargs['local_paths'])
                    self.assertEqual(set(kwargs['local_packages']), {'shared-lib', wanted})
                manager._install_offline.side_effect = install
                with patch('modules.capture_services.openwrt.shared_bundle_packages.CommonBundleRepository', return_value=donor):
                    self.assertTrue(install_from_local(manager, (wanted,), f.device, root / 'cache',
                                                     True, Event(), Mock(), repository=primary))
                self.assertIn(b'shared:shared-lib:1', observed)
                self.assertEqual(len(observed), 2)

    def run_apk(self, root, donor_version='1.2.5-r5', corrupt=False, mismatch=False, kind='capture'):
        wanted = 'aircrack-ng' if kind == 'capture' else 'tmux'
        f, primary = prepare(root, kind, True)
        _, donor = prepare(root, 'common' if kind == 'capture' else 'capture', True, extra=(('libpthread', donor_version),))
        donor_profile = donor.profile(f.device)
        if corrupt:
            row = next(r for r in donor_profile['packages'] if r['name'] == 'libpthread')
            donor.object_source(row).write_bytes(b'broken')
        if mismatch:
            donor_profile['release'] = '25.12.5'
        manager = NS(_preflight=Mock(return_value=PackageManager('apk')), _execute=Mock(), command_runner=Mock())
        plan = '(1/2) Installing libpthread (1.2.5-r5)\n(2/2) Installing ' + wanted + ' (1)'
        manager._execute_stage = Mock(side_effect=lambda name, *args: plan if name == 'apk_simulate_plan' else 'OK:')
        uploads = []
        def upload(src, dst): uploads.append((dst, Path(src).read_bytes()))
        with patch('modules.capture_services.openwrt.shared_bundle_packages.CommonBundleRepository', return_value=donor), \
             patch('modules.capture_services.openwrt.package_space.check_plan_space') as space:
            try:
                result = install_from_local(manager, (wanted,), f.device, root / 'cache', True,
                                            Event(), Mock(), uploader=upload, repository=primary)
                self.assertTrue(result)
                space.assert_called_once()
            finally:
                self.uploads, self.commands = uploads, manager._execute_stage.call_args_list
        return uploads

    def test_customer_apk_plan_borrows_exact_pthread_with_network_disabled(self):
        with TemporaryDirectory() as temp:
            uploads = self.run_apk(Path(temp))
        self.assertTrue(any(dst.endswith('libpthread-1.2.5-r5.apk') and data == b'shared:libpthread:1.2.5-r5'
                            for dst, data in uploads))
        for call in self.commands:
            if call.args[0] in ('apk_simulate_plan', 'apk_install'):
                self.assertIn('apk --no-network add', call.args[1])

    def test_common_apk_can_borrow_from_capture_bundle(self):
        with TemporaryDirectory() as temp:
            uploads = self.run_apk(Path(temp), kind='common')
        self.assertTrue(any(dst.endswith('libpthread-1.2.5-r5.apk') for dst, _ in uploads))
        self.assertTrue(any(dst.endswith('tmux-1.apk') for dst, _ in uploads))
        self.assertFalse(any(dst.endswith('aircrack-ng-1.apk') for dst, _ in uploads))

    def test_invalid_secondary_signature_is_not_used(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            f, primary = prepare(root, 'common', False, need=('tmux', 'shared-lib'))
            _, donor = prepare(root, 'capture', False, extra=(('shared-lib', '1'),))
            receipt = donor.root / 'current.json'
            envelope = json.loads(receipt.read_text(encoding='utf-8'))
            envelope['payload']['size'] += 1
            receipt.write_bytes(canonical(envelope))
            manager = NS(command_runner=Mock(), _execute_stage=Mock(return_value='libc - 1\n'), _install_offline=Mock())
            from cryptography.exceptions import InvalidSignature
            with patch('modules.capture_services.openwrt.shared_bundle_packages.CommonBundleRepository', return_value=donor):
                with self.assertRaises((InvalidSignature, ValueError, SignalError)):
                    install_from_local(manager, ('tmux',), f.device, root / 'cache', True,
                                       Event(), Mock(), repository=primary)
            manager._install_offline.assert_not_called()

    def test_wrong_version_identity_and_corrupt_object_stop_before_package_upload(self):
        for options in ({'donor_version':'1.2.5-r4'}, {'mismatch':True}, {'corrupt':True}):
            with self.subTest(options=options), TemporaryDirectory() as temp:
                with self.assertRaises(SignalError) as caught: self.run_apk(Path(temp), **options)
                self.assertEqual(caught.exception.code, 'environment')
                self.assertFalse(any(dst.endswith('.apk') for dst, _ in self.uploads))
                self.assertNotIn('apk_install', [call.args[0] for call in self.commands])

    def test_complete_primary_does_not_load_another_bundle(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            f, primary = prepare(root, 'common', False)
            manager = NS(command_runner=Mock(), _execute_stage=Mock(return_value='libc - 1\n'), _install_offline=Mock())
            with patch('modules.capture_services.openwrt.shared_bundle_packages.CommonBundleRepository') as factory:
                self.assertTrue(install_from_local(manager, ('tmux',), f.device, root / 'cache', True,
                                                 Event(), Mock(), repository=primary))
            factory.assert_not_called()

    def test_updated_ipk_dependency_can_replace_only_an_uninstalled_local_candidate(self):
        from modules.capture_services.openwrt.dependency_resolution import InstalledPackages, UnsatisfiedDependency
        from scripts.openwrt_packages.opkg_version import satisfies
        with TemporaryDirectory() as temp:
            root = Path(temp)
            f, primary = prepare(root, 'common', False, extra=(('shared-lib', '1'),),
                                 need=('tmux', 'shared-lib (>= 2)'))
            _, donor = prepare(root, 'capture', False, extra=(('shared-lib', '2'),))
            profile = primary.profile(f.device)
            pool = SharedBundlePackages(primary, profile, f.device, lambda repo: repo, Event(), Mock())
            with patch('modules.capture_services.openwrt.shared_bundle_packages.CommonBundleRepository', return_value=donor):
                result = pool.ipk_selection(profile, ('tmux',), InstalledPackages({'libc': '1'}), satisfies)
                self.assertEqual([(p.name, p.version) for p in result], [('shared-lib', '2'), ('tmux', '1')])
                row = pool.package_row(profile, 'shared-lib', '2')
                self.assertEqual(pool.materialize(row, root / 'work').read_bytes(), b'shared:shared-lib:2')
                with self.assertRaises(UnsatisfiedDependency):
                    pool.ipk_selection(profile, ('tmux',), InstalledPackages({'libc': '1', 'shared-lib': '1'}), satisfies)
            self.assertEqual(next(r for r in profile['packages'] if r['name'] == 'shared-lib')['version'], '1')

    def test_version_choice_changes_only_the_unsatisfied_dependency(self):
        from modules.capture_services.openwrt.dependency_resolution import InstalledPackages
        from scripts.openwrt_packages.opkg_version import satisfies
        with TemporaryDirectory() as temp:
            root = Path(temp)
            f, primary = prepare(root, 'common', False, extra=(('lib-a', '1'), ('lib-b', '1')),
                                 need=('tmux', ['lib-a (>= 2)', 'lib-b (< 2)']))
            _, donor = prepare(root, 'capture', False, extra=(('lib-a', '2'), ('lib-b', '2')))
            profile = primary.profile(f.device)
            check = Mock(side_effect=satisfies)
            pool = SharedBundlePackages(primary, profile, f.device, lambda repo: repo, Event(), Mock())
            with patch('modules.capture_services.openwrt.shared_bundle_packages.CommonBundleRepository', return_value=donor):
                result = pool.ipk_selection(profile, ('tmux',), InstalledPackages({'libc': '1'}), check)
            self.assertEqual([(p.name, p.version) for p in result], [('lib-a', '2'), ('lib-b', '1'), ('tmux', '1')])
            calls = [call.args for call in check.call_args_list]
            self.assertEqual(len(calls), len(set(calls)))
            self.assertEqual([row['version'] for row in profile['packages'] if row['name'] in ('lib-a','lib-b')], ['1','1'])

    def test_active_reader_blocks_update_and_next_task_reads_new_revision(self):
        from modules.capture_services.openwrt.bundle_storage_lock import bundle_lock
        with TemporaryDirectory() as temp:
            root = Path(temp)
            f, primary = prepare(root / 'primary', 'capture', True)
            _, old = prepare(root / 'old', 'common', True, extra=(('libpthread', '1.2.5-r4'),))
            newer, updated = prepare(root / 'new', 'common', True, extra=(('libpthread', '1.2.5-r5'),))
            envelope = (updated.root / 'current.json').read_bytes()
            original_receipt = (old.root / 'current.json').read_bytes()
            profile = primary.profile(f.device)
            with ExitStack() as leases:
                def retain(repo):
                    leases.enter_context(bundle_lock(repo.root, shared=True))
                    return repo
                pool = SharedBundlePackages(primary, profile, f.device, retain, Event(), Mock())
                with patch('modules.capture_services.openwrt.shared_bundle_packages.CommonBundleRepository', return_value=old):
                    self.assertIsNone(pool.package_row(profile, 'libpthread', '1.2.5-r5'))
                    with self.assertRaises(OSError):
                        prepare_archive(newer.archive, envelope, old.root, Event(), public_key=updated.public_key)
                    self.assertEqual((old.root / 'current.json').read_bytes(), original_receipt)
            prepare_archive(newer.archive, envelope, old.root, Event(), public_key=updated.public_key)
            latest = CommonBundleRepository(old.root, updated.public_key)
            with ExitStack() as leases:
                def retain(repo):
                    leases.enter_context(bundle_lock(repo.root, shared=True))
                    return repo
                next_task = SharedBundlePackages(primary, profile, f.device, retain, Event(), Mock())
                with patch('modules.capture_services.openwrt.shared_bundle_packages.CommonBundleRepository', return_value=latest):
                    row = next_task.package_row(profile, 'libpthread', '1.2.5-r5')
                    self.assertIsNotNone(row)
                    self.assertEqual(next_task.materialize(row, root / 'work').read_bytes(), b'shared:libpthread:1.2.5-r5')
                    self.assertNotEqual(latest.revision, old.revision)

    def test_borrow_failure_or_cancel_releases_both_resource_leases(self):
        from modules.capture_services.openwrt.bundle_storage_lock import bundle_lock
        for failure in ('cancel', 'corrupt'):
            with self.subTest(failure=failure), TemporaryDirectory() as temp:
                root = Path(temp)
                f, primary = prepare(root, 'common', False, need=('tmux', 'shared-lib'))
                _, donor = prepare(root, 'capture', False, extra=(('shared-lib', '1'),))
                row = next(r for r in donor.profile(f.device)['packages'] if r['name'] == 'shared-lib')
                if failure == 'corrupt':
                    donor.object_source(row).write_bytes(b'corrupt')
                event = Event()
                def tell(message, level):
                    if failure == 'cancel' and 'shared-lib' in message:
                        event.set()
                manager = NS(command_runner=Mock(), _execute_stage=Mock(return_value='libc - 1\n'), _install_offline=Mock())
                receipts = [(repo.root / 'current.json').read_bytes() for repo in (primary, donor)]
                with patch('modules.capture_services.openwrt.shared_bundle_packages.CommonBundleRepository', return_value=donor):
                    with self.assertRaises(SignalError) as caught:
                        install_from_local(manager, ('tmux',), f.device, root / 'cache', True,
                                           event, tell, repository=primary)
                self.assertEqual(caught.exception.code, 'cancelled' if failure == 'cancel' else 'environment')
                manager._install_offline.assert_not_called()
                for repo, receipt in zip((primary, donor), receipts):
                    with bundle_lock(repo.root):
                        self.assertEqual((repo.root / 'current.json').read_bytes(), receipt)

    def test_pool_rejects_unsigned_rows_cancel_and_kernel_packages(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            f, primary = prepare(root, 'common', False)
            _, donor = prepare(root, 'capture', False, extra=(('kmod-tun','1'), ('libpthread','1')))
            event = Event()
            pool = SharedBundlePackages(primary, primary.profile(f.device), f.device, lambda repo:repo, event, Mock())
            with patch('modules.capture_services.openwrt.shared_bundle_packages.CommonBundleRepository', return_value=donor):
                self.assertIsNone(pool.package_row(pool.original, 'kmod-tun', '1'))
                row = pool.package_row(pool.original, 'libpthread', '1')
                with self.assertRaises(ValueError): pool.materialize(dict(row), root / 'temp', event)
            pool = SharedBundlePackages(primary, primary.profile(f.device), f.device, lambda repo:repo, event, Mock())
            event.set()
            with self.assertRaises(InterruptedError): pool.package_row(pool.original, 'libpthread', '1')


if __name__ == '__main__': unittest.main()
