import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.snapshot_configured_opkg import feed_urls, fetch_configured_original


class ConfiguredFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = b'original package'
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.index = (f'Package: curl\nVersion: 1-r0\nFilename: curl.ipk\n'
                      f'Size: {len(self.raw)}\nSHA256sum: {self.digest}\n\n').encode()
        self.task = Mock()
        self.task.reader.run.return_value = 'src kwrt https://dl.openwrt.ai/feed\n'
        self.task.guard.cancel = None
        self.source = SimpleNamespace(task=self.task, local=Path(self.temp.name),
                                      remote='/tmp/private', provenance={})
        self.pkg = SimpleNamespace(name='curl', version='1-r0', metadata={})

    def fetch(self, url, **kwargs):
        return {'Packages': self.index, 'Packages.sig': b'signature', 'curl.ipk': self.raw}[url.rsplit('/', 1)[-1]]

    def test_verified_exact_package(self):
        with patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes', side_effect=self.fetch):
            result = fetch_configured_original(self.source, self.pkg)
        self.assertEqual(result.read_bytes(), self.raw)
        self.assertEqual(self.source.provenance['curl']['sha256'], self.digest)
        self.assertIn('opkg-key verify', self.task.reader.run.call_args_list[-1].args[1])

    def test_bad_signature_and_wrong_version_stop(self):
        self.task.reader.run.side_effect = lambda action, *args: ('src kwrt https://dl.openwrt.ai/feed\n'
            if action == 'original_feeds' else (_ for _ in ()).throw(ValueError('bad signature')))
        with patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes', side_effect=self.fetch):
            with self.assertRaisesRegex(ValueError, 'No signed'):
                fetch_configured_original(self.source, self.pkg)
        self.task.reader.run.side_effect = None
        self.pkg.version = '2-r0'
        with patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes', side_effect=self.fetch):
            with self.assertRaisesRegex(ValueError, 'No signed'):
                fetch_configured_original(self.source, self.pkg)

    def test_bad_checksum_stops(self):
        self.raw = b'changed package!'
        with patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes', side_effect=self.fetch):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                fetch_configured_original(self.source, self.pkg)

    def test_source_rejects_credentials_and_insecure_transport(self):
        for source in ('http://example.org/feed', 'https://u:p@example.org/feed'):
            with self.assertRaises(ValueError):
                feed_urls('src x ' + source)


if __name__ == '__main__':
    unittest.main()
