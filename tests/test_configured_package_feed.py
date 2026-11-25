"""PC downloads from a device's own configured opkg feeds (Kwrt and other third-party firmware).

These tests never touch a router or the network: the device read is a plain
callable and every byte is fetched through a patched ``fetch_bytes``.
"""
from contextlib import nullcontext
import gzip
import hashlib
import tempfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.dependency_resolution import InstalledPackages
from modules.capture_services.openwrt.package_manager import PackageManager, package_error_reason
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.signal_package_feed import (
    download_configured_packages,
    normalize_profile,
)

BASE = 'https://dl.openwrt.ai/releases/25.12/packages/aarch64_cortex-a53/base'
CONFIG = 'src/gz kwrt_base ' + BASE + '\n'


def index_text(name, filename, raw, *, version='1-r1', extra=''):
    return (f'Package: {name}\nVersion: {version}\nFilename: {filename}\n'
            f'Size: {len(raw)}\nSHA256sum: {hashlib.sha256(raw).hexdigest()}\n'
            f'{extra}\n').encode('utf-8')


class ConfiguredDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = b'wpa-cli package bytes'
        self.index = index_text('wpa-cli', 'wpa-cli_1-r1_aarch64_cortex-a53.ipk', self.raw,
                                extra='Depends: libc')
        self.gz_index = gzip.compress(self.index)
        self.installed = InstalledPackages({'libc': '1.2.3'})
        self.profile = normalize_profile('25.12-SNAPSHOT', 'aarch64_cortex-a53', 'opkg',
                                         distribution='Kwrt')

    def reader(self, config=CONFIG):
        return lambda name, command, timeout: config

    def fetch(self, url, **kwargs):
        if url == BASE + '/Packages.gz':
            return self.gz_index
        if url == BASE + '/wpa-cli_1-r1_aarch64_cortex-a53.ipk':
            return self.raw
        raise RuntimeError('unexpected url ' + url)

    def test_downloads_missing_closure_from_the_device_real_source(self):
        with patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes',
                   side_effect=self.fetch) as fetch:
            paths = download_configured_packages(
                self.profile, self.reader(), self.installed, self.temp.name,
                wanted=('wpa-cli',), message=Mock())
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].read_bytes(), self.raw)
        fetched = [call.args[0] for call in fetch.call_args_list]
        self.assertTrue(all(url.startswith('https://dl.openwrt.ai/') for url in fetched))

    def test_insecure_or_credentialed_source_is_rejected(self):
        for bad in ('src x http://dl.openwrt.ai/feed\n',
                    'src x https://u:p@dl.openwrt.ai/feed\n'):
            with self.subTest(bad=bad), patch(
                    'modules.capture_services.openwrt.signal_package_feed.fetch_bytes',
                    side_effect=self.fetch):
                with self.assertRaises(ValueError):
                    download_configured_packages(self.profile, self.reader(bad),
                                                 self.installed, self.temp.name, wanted=('wpa-cli',))

    def test_one_dead_feed_does_not_hide_a_usable_feed(self):
        dead = 'https://dl.openwrt.ai/dead/base'
        config = CONFIG + 'src/gz kwrt_dead ' + dead + '\n'

        def fetch(url, **kwargs):
            if dead in url:
                raise RuntimeError('dead feed')
            return self.fetch(url, **kwargs)

        with patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes',
                   side_effect=fetch):
            paths = download_configured_packages(self.profile, self.reader(config),
                                                 self.installed, self.temp.name, wanted=('wpa-cli',))
        self.assertEqual(paths[0].read_bytes(), self.raw)

    def test_core_package_is_rejected_before_any_package_download(self):
        self.index = index_text('wpad-basic', 'wpad-basic_1-r1_aarch64_cortex-a53.ipk', b'x')
        self.gz_index = gzip.compress(self.index)
        with patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes',
                   side_effect=self.fetch) as fetch:
            with self.assertRaises(ValueError):
                download_configured_packages(self.profile, self.reader(), self.installed,
                                             self.temp.name, wanted=('wpad-basic',))
        installs = [call.args[0] for call in fetch.call_args_list if call.args[0].endswith('.ipk')]
        self.assertEqual(installs, [])

    def test_unreadable_sources_report_honestly(self):
        def fetch(url, **kwargs):
            raise RuntimeError('no route')

        with patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes',
                   side_effect=fetch):
            with self.assertRaises(RuntimeError) as caught:
                download_configured_packages(self.profile, self.reader(), self.installed,
                                             self.temp.name, wanted=('wpa-cli',))
        self.assertIn('真实软件源', str(caught.exception))

    def test_cancel_stops_before_downloading(self):
        cancel = Event()
        cancel.set()
        with patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes',
                   side_effect=self.fetch) as fetch:
            with self.assertRaises(RuntimeError):
                download_configured_packages(self.profile, self.reader(), self.installed,
                                             self.temp.name, cancel_event=cancel, wanted=('wpa-cli',))
        fetch.assert_not_called()


class KwrtRouteDispatchTests(unittest.TestCase):
    def setUp(self):
        source = patch('modules.capture_services.openwrt.temporary_package_sources.domestic_sources',
                       side_effect=lambda manager, base, cancel, tell, **kwargs: nullcontext(base))
        source.start()
        self.addCleanup(source.stop)

    def device(self, distribution):
        return SimpleNamespace(version='25.12-SNAPSHOT', package_architecture='aarch64_cortex-a53',
                               package_manager='opkg', distribution=distribution)

    def test_kwrt_network_failure_allows_the_pc_route(self):
        manager = OpenWrtDependencyManager(Mock())
        manager._preflight = Mock(return_value=PackageManager('opkg'))
        manager._execute_stage = Mock(
            side_effect=SignalError('transport', package_error_reason('Failed to download index, wget returned 4')))
        with self.assertRaises(SignalError) as caught:
            manager._install_online(('wpa-cli',), Event(), Mock(), device=self.device('Kwrt'))
        self.assertEqual(caught.exception.code, 'offline_retry')

    def test_kwrt_signal_dependency_failure_allows_the_pc_route(self):
        from modules.capture_services.openwrt.signal_manager import RouterSignalManager
        manager = RouterSignalManager(Mock())
        manager.probe_dependencies = Mock(return_value=dict(lua=True, uci=False, ubus=True, jsonc=True))
        manager._installation_environment = Mock(return_value=PackageManager('opkg'))
        manager._execute_stage = Mock(
            side_effect=SignalError('transport', package_error_reason('Failed to download Packages.gz, wget returned 4.')))
        with self.assertRaises(SignalError) as caught:
            manager.install_dependencies(Event(), Mock(), device=self.device('Kwrt'))
        self.assertEqual(caught.exception.code, 'offline_retry')

    def test_kwrt_offline_download_prefers_device_source_with_official_fallback(self):
        manager = OpenWrtDependencyManager(Mock())
        manager._execute_stage = Mock(return_value='')
        sentinel = SignalError('transport', 'stop after dispatch')
        with patch('modules.capture_services.openwrt.dependency_manager.download_third_party_packages',
                   side_effect=sentinel) as third_party, patch(
                'modules.capture_services.openwrt.dependency_manager.download_profile_packages') as official:
            with self.assertRaises(SignalError) as caught:
                manager._install_offline(('wpa-cli',), self.device('Kwrt'), None, False,
                                         Event(), Mock(), uploader=Mock())
        self.assertIs(caught.exception, sentinel)
        third_party.assert_called_once()
        official.assert_not_called()

    def test_openwrt_offline_download_keeps_official_mirror(self):
        manager = OpenWrtDependencyManager(Mock())
        manager._execute_stage = Mock(return_value='')
        sentinel = SignalError('transport', 'stop after dispatch')
        with patch('modules.capture_services.openwrt.dependency_manager.download_third_party_packages') as third_party, \
                patch('modules.capture_services.openwrt.dependency_manager.download_profile_packages',
                      side_effect=sentinel) as official:
            with self.assertRaises(SignalError) as caught:
                manager._install_offline(('wpa-cli',), self.device('OpenWrt'), None, False,
                                         Event(), Mock(), uploader=Mock())
        self.assertIs(caught.exception, sentinel)
        official.assert_called_once()
        third_party.assert_not_called()


class ThirdPartyFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = normalize_profile('25.12-SNAPSHOT', 'aarch64_cortex-a53', 'opkg',
                                         distribution='Kwrt')
        self.installed = InstalledPackages({'libc': '1.2.3'})
        self.run = lambda name, command, timeout: CONFIG

    def test_device_source_success_does_not_touch_official_mirror(self):
        from modules.capture_services.openwrt.signal_package_feed import download_third_party_packages
        with patch('modules.capture_services.openwrt.signal_package_feed.download_configured_packages',
                   return_value=[Path('device.ipk')]) as device, patch(
                'modules.capture_services.openwrt.signal_package_feed.download_profile_packages') as official:
            paths = download_third_party_packages(self.profile, self.run, self.installed, self.temp.name,
                                                  wanted=('wpa-cli',), message=Mock())
        self.assertEqual(paths, [Path('device.ipk')])
        device.assert_called_once()
        official.assert_not_called()

    def test_device_source_failure_falls_back_to_official_mirror(self):
        from modules.capture_services.openwrt.signal_package_feed import download_third_party_packages
        with patch('modules.capture_services.openwrt.signal_package_feed.download_configured_packages',
                   side_effect=RuntimeError('device source down')) as device, patch(
                'modules.capture_services.openwrt.signal_package_feed.download_profile_packages',
                return_value=[Path('official.ipk')]) as official:
            paths = download_third_party_packages(self.profile, self.run, self.installed, self.temp.name,
                                                  wanted=('wpa-cli',), message=Mock())
        self.assertEqual(paths, [Path('official.ipk')])
        device.assert_called_once()
        official.assert_called_once()

    def test_cancellation_does_not_fall_back_to_official_mirror(self):
        from modules.capture_services.openwrt.signal_package_feed import download_third_party_packages
        cancel = Event()
        cancel.set()
        with patch('modules.capture_services.openwrt.signal_package_feed.download_configured_packages',
                   side_effect=RuntimeError('download cancelled')) as device, patch(
                'modules.capture_services.openwrt.signal_package_feed.download_profile_packages') as official:
            with self.assertRaises(RuntimeError):
                download_third_party_packages(self.profile, self.run, self.installed, self.temp.name,
                                              cancel_event=cancel, wanted=('wpa-cli',), message=Mock())
        device.assert_called_once()
        official.assert_not_called()


class KwrtBackupOriginalTests(unittest.TestCase):
    def test_kwrt_backup_original_runs_on_the_pc_from_device_feeds(self):
        from modules.capture_services.openwrt.snapshot_original_packages import SnapshotOriginalPackages
        source = SnapshotOriginalPackages.__new__(SnapshotOriginalPackages)
        source.task = Mock()
        source.local = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(source.local, ignore_errors=True))
        source.provenance = {}
        pkg = SimpleNamespace(name='wpa-cli', version='1', metadata={}, files=())
        device = SimpleNamespace(distribution='Kwrt', version='25.12-SNAPSHOT',
                                 target='mediatek/filogic',
                                 package_architecture='aarch64_cortex-a53',
                                 package_manager='opkg')
        with patch('modules.capture_services.openwrt.snapshot_configured_opkg.fetch_configured_original',
                   return_value='PKG') as fetch:
            self.assertEqual(source._online(pkg, device), 'PKG')
        fetch.assert_called_once_with(source, pkg)


if __name__ == '__main__':
    unittest.main()
