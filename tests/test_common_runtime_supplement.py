"""Supplement only missing runtimes and retain every previous package."""
import unittest
from scripts.openwrt_packages.common_bundle_support import ROOTS
from scripts.openwrt_packages.supplement_common_runtime import supplement
from scripts.openwrt_packages.test_common_bundle import package

class RuntimeSupplementTests(unittest.TestCase):
    def profile(self):
        rows = [package(name, ["libpthread"] if name == "tmux" else []) for name in ROOTS]
        return dict(packages=rows, roots=list(ROOTS), firmware_required=["libc", "libpthread", "librt"])

    def test_runtime_closure_preserves_versions_and_corrects_firmware_requirements(self):
        original = self.profile()
        target = [package("libpthread", ["libgcc1"]), package("libgcc1", ["libc"])]
        result = supplement(original, target)
        self.assertEqual(result["packages"][:len(original["packages"])], original["packages"])
        self.assertEqual({p["name"] for p in result["packages"]} - set(ROOTS), {"libpthread", "libgcc1"})
        self.assertNotIn("libpthread", result["firmware_required"])
        self.assertIn("libc", result["firmware_required"])
        self.assertIn("librt", result["firmware_required"])
        self.assertIn("libpthread", original["firmware_required"])

    def test_missing_runtime_stops_build(self):
        with self.assertRaisesRegex(ValueError, "libpthread"):
            supplement(self.profile(), [])

    def test_unexpected_new_dependency_stops_build(self):
        with self.assertRaisesRegex(ValueError, "Unexpected supplementary"):
            supplement(self.profile(), [package("libpthread", ["unrelated"]), package("unrelated")])

if __name__ == "__main__":
    unittest.main()
