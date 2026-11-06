"""Expanded real catalogs need a bounded reader and an application minimum."""
import unittest
from tempfile import TemporaryDirectory
from tests.test_common_offline_bundle import fixture
from tests.test_bundle_client_compatibility import signing_key
from modules.capture_services.openwrt.common_bundle_manifest import MAX_CATALOG,verify_bundle_manifest
from modules.capture_services.openwrt.bundle_compatibility import BundleClientUpdateRequired

class FullBundleReadLimitTests(unittest.TestCase):
    def test_expanded_signed_catalog_requires_the_new_reader_version(self):
        with TemporaryDirectory() as folder:
            f=fixture(folder);public,sign=signing_key()
            payload=dict(f.payload,catalog_size=45840610,min_app_version='1.0.46_alpha')
            self.assertEqual(verify_bundle_manifest(sign(payload),public,current_version='1.0.46_alpha')['catalog_size'],45840610)
            with self.assertRaises(BundleClientUpdateRequired):
                verify_bundle_manifest(sign(payload),public,current_version='1.0.45_alpha')

    def test_expansion_still_has_a_finite_catalog_limit(self):
        with TemporaryDirectory() as folder:
            f=fixture(folder);public,sign=signing_key()
            self.assertEqual(MAX_CATALOG,64*1024*1024)
            with self.assertRaises(ValueError):
                verify_bundle_manifest(sign(dict(f.payload,catalog_size=MAX_CATALOG+1)),public)
