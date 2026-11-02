"""curl uses the existing verified offline installer and old catalogs explain updates."""
import copy
import unittest
from modules.capture_services.openwrt.bundle_specs import COMMON_ROOTS
from modules.capture_services.openwrt.common_tool_dependencies import REQUIRED_TOOL_KEYS
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
from tests.test_common_offline_bundle import CommonOfflineTests


class CurlOfflineTests(CommonOfflineTests):
    def test_curl_is_required_and_installable_offline(self):
        self.assertIn("curl", REQUIRED_TOOL_KEYS)
        self.assertIn("curl", COMMON_ROOTS)
        from modules.capture_services.openwrt.dependency_resolution import InstalledPackages
        selected = self.repo.ipk_selection(self.repo.profile(self.f.device), ["curl"], InstalledPackages({"libc": "1"}))
        self.assertEqual([row.name for row in selected], ["curl"])

    def test_previous_catalog_requests_update(self):
        old = copy.deepcopy(self.f.catalog)
        old["roots"].remove("curl")
        for row in old["profiles"]:
            row["roots"].remove("curl")
        with self.assertRaisesRegex(ValueError, "\u7248\u672c\u8fc7\u65e7.*\u66f4\u65b0"):
            validate_catalog(old)

    def test_official_opkg_versioned_provider_uses_advertised_version(self):
        from modules.capture_services.openwrt.bundle_dependency_rules import requirements, matches
        row = {"version": "21.0-3"}
        self.assertTrue(matches(requirements("erlang-erts (= 10.0.1)")[0], row, "erlang-erts=10.0.1"))
        self.assertFalse(matches(requirements("erlang-erts (= 21.0-3)")[0], row, "erlang-erts=10.0.1"))
        with self.assertRaises(ValueError):
            requirements("erlang-erts=10.0.1=extra")

    def test_unknown_extra_root_remains_invalid(self):
        invalid = copy.deepcopy(self.f.catalog)
        invalid["roots"].append("unapproved-tool")
        with self.assertRaises(ValueError):
            validate_catalog(invalid)


if __name__ == "__main__":
    unittest.main()
