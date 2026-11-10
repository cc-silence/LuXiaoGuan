"""Independent boundary checks for optional dependency installation review."""
from dataclasses import replace
import gzip
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.dependency_resolution import InstalledPackages,resolve_packages
from modules.capture_services.openwrt.kernel_package_feed import kernel_sources,fetch_tun_index
from modules.capture_services.openwrt.package_mirror_policy import request_urls,DOMESTIC
from modules.capture_services.openwrt.signal_manager import SignalError
from tests.test_other_dependency_install_fix import ABI,PROFILE,package,index_blob


class BoundaryReviewTests(unittest.TestCase):
    def test_conflict_version_probe_preserves_cancellation(self):
        cancelled=SignalError('cancelled','version query cancelled')
        index={'tool':package('tool',conflicts=('installed (>= 1)',))}
        compare=Mock(side_effect=cancelled)
        with self.assertRaises(SignalError) as caught:
            resolve_packages(index,('tool',),InstalledPackages({'installed':'1'}),compare)
        self.assertIs(caught.exception,cancelled)
        compare.assert_called_once_with('1','>=','1')

    def test_conflicting_alternative_rolls_back_its_transitive_packages(self):
        index={'root':package('root',('bad | good',)),
            'bad':package('bad',('helper',),('helper',)),
            'helper':package('helper'),'good':package('good')}
        original=dict(index)
        installed=InstalledPackages({'existing':'1'})
        result=resolve_packages(index,('root',),installed)
        self.assertEqual([p.name for p in result],['good','root'])
        self.assertEqual(index,original)
        self.assertEqual(dict(installed.versions),{'existing':'1'})

    def test_mirror_paths_preserve_full_kernel_identity(self):
        installed=InstalledPackages({'kernel':ABI})
        _,urls=kernel_sources(PROFILE,'mediatek/filogic',installed,'5.15.162')
        for url in urls:
            with self.subTest(url=url):
                suffix=url.split('.org',1)[1]
                self.assertEqual(request_urls(url,True),(url,))
                self.assertEqual(request_urls(url,False),tuple(root+suffix for root in DOMESTIC))

    def test_ambiguous_or_incomplete_kernel_metadata_is_rejected(self):
        valid=gzip.decompress(index_blob())
        bad=[valid+valid,
            valid.replace(b'Architecture:',b'Architecture: unexpected\nArchitecture:'),
            valid.replace(b'SHA256sum:',b'SHA256sum: bad\nUnused:'),
            valid.replace(b'Architecture:',b'Unknown:')]
        installed=InstalledPackages({'kernel':ABI})
        for body in bad:
            fetch=Mock(return_value=gzip.compress(body))
            with self.subTest(body=body),self.assertRaises(ValueError):
                fetch_tun_index(PROFILE,'mediatek/filogic',installed,'5.15.162',fetch)
            self.assertEqual(fetch.call_count,1)


if __name__=='__main__':unittest.main()
