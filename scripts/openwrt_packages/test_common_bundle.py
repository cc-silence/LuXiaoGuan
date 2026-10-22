"""Focused regression checks for common offline package collection."""
from pathlib import Path
import hashlib
import json
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
from .bundle_ledger import ProfileLedger
import zipfile
import zlib
from .apk_index_reader import AdbReader
from .common_bundle_support import closure, checked_filename, hash_table, ROOTS, TARGETS
from .collect_common_bundle import Collector


def package(name, depends=(), provides=()):
    return dict(name=name, version="1", architecture="test", filename=name+".ipk",
                size=4, sha256="a"*64, depends=list(depends), provides=list(provides),
                install_if=[], feed="base", url="https://downloads.openwrt.org/"+name)


class CommonBundleTests(unittest.TestCase):
    def test_roots_match_current_common_tool_catalog(self):
        from modules.capture_services.openwrt.common_tool_dependencies import TOOLS
        expected = {"openssh-sftp-server", "tmux"}
        for tool in TOOLS:
            expected.update(tool.package_names)
        from modules.capture_services.openwrt.wireless_dependencies import WIRELESS_HELPER_PACKAGES
        expected.update(WIRELESS_HELPER_PACKAGES)
        self.assertEqual(set(ROOTS), expected)
        self.assertEqual(len(TARGETS), 14)
        self.assertNotIn("ramips/mt76x8", TARGETS)
        self.assertNotIn("rockchip/armv8", TARGETS)
        self.assertNotIn("x86/generic", TARGETS)

    def test_transitive_dependencies_and_virtual_providers(self):
        rows=[package("root", ["virtual (>= 1)", "libc"]),
              package("real", ["leaf"], ["virtual"]), package("leaf")]
        selected, firmware=closure(rows, ["root"])
        self.assertEqual({r["name"] for r in selected}, {"root", "real", "leaf"})
        self.assertEqual(firmware, ["libc"])

    def test_missing_dependency_is_fatal(self):
        with self.assertRaisesRegex(ValueError, "Missing required packages"):
            closure([package("root", ["missing"])], ["root"])

    def test_kernel_dependency_is_fatal(self):
        with self.assertRaisesRegex(ValueError, "kernel module"):
            closure([package("root", ["kmod-test"])], ["root"])

    def test_apk_negative_dependencies_are_not_downloaded(self):
        root=package("root", [{"name":"conflicting", "match":16}, {"name":"leaf", "match":0}])
        selected,_=closure([root,package("leaf")],["root"])
        self.assertEqual(len(selected),2)

    def test_exact_name_precedes_virtual_provider(self):
        selected,_=closure([package("provider",provides=["root"]),package("root")],["root"])
        self.assertEqual(selected[0]["name"],"root")

    def test_invalid_package_checksum_is_fatal(self):
        row=package("root");row["sha256"]=""
        with self.assertRaisesRegex(ValueError,"checksum"):
            closure([row],["root"])

    def test_filename_paths_are_rejected(self):
        for name in ["../root.apk","a/b.apk","C:\\root.apk","..","a b.apk"]:
            with self.assertRaises(ValueError):checked_filename(name)
        self.assertEqual(checked_filename("lua-1.2~abc-r1.apk"),"lua-1.2~abc-r1.apk")

    def test_hashes_keep_feed_identity(self):
        hashes=hash_table((("a"*64)+" *base/a.apk\n"+("b"*64)+" *packages/a.apk\n").encode())
        self.assertEqual(len(hashes),2)

    def test_adb_rejects_wrong_format_and_out_of_bounds(self):
        with self.assertRaises(ValueError):AdbReader(b"not an index")
        payload=b"\0"*4+struct.pack("<I",0xe0ffffff)
        raw=b"ADB.indx"+struct.pack("<I",len(payload)+4)+payload
        reader=AdbReader(b"ADBd"+zlib.compress(raw)[2:-4])
        with self.assertRaises(ValueError):reader.object(0xe0ffffff)

    def test_archive_counts_shared_bytes_once_and_crc(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Collector(directory)
            data=b"same verified package"
            obj=task.put(data,"root.ipk")
            task.put(b"unreferenced build-only feed index", "Packages.gz")
            row=package("root");row.update(size=len(data),sha256=obj["sha256"])
            profile=dict(status="index-verified",release="24.10.8",target="a/b",
                         architecture="test",packages=[row],indexes=[])
            inventory=dict(errors=[],profiles=[profile,dict(profile,target="c/d")],
                           releases=["24.10.8"],targets=["a/b","c/d"])
            result=task.finish(inventory,[])
            self.assertTrue(result["complete"])
            self.assertEqual(result["package_references"],2)
            self.assertEqual(result["unique_packages"],1)
            self.assertEqual(result["deduplicated_bytes"],len(data))
            with zipfile.ZipFile(result["zip_path"]) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(len(json.loads(archive.read("catalog.json"))["profiles"]),2)


class IncrementalTests(unittest.TestCase):
    def profile(self,release,target):
        return dict(status="index-verified",release=release,target=target,architecture="test",
                    roots=list(ROOTS),packages=[],indexes=[],package_manager="opkg")

    def test_saved_profile_never_calls_network_architecture_or_dependency_resolver(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Collector(directory);p=self.profile("24.10.8","x86/64");task.ledger.save(p)
            task.http.official=Mock(side_effect=AssertionError("old profile network"))
            task.architecture=Mock(side_effect=AssertionError("old profile parsing"))
            with patch("scripts.openwrt_packages.collect_common_bundle.closure",side_effect=AssertionError("old graph recomputed")):
                self.assertEqual(task.profile("24.10.8","x86/64"),p)
            self.assertEqual(task.reused_profiles,[("24.10.8","x86/64")])

    def test_adding_release_preserves_previous_profiles_and_only_computes_new_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Collector(directory);old=self.profile("24.10.8","x86/64");new=self.profile("26.10.0","x86/64")
            Path(directory,"inventory.json").write_text(json.dumps(dict(profiles=[old],errors=[])),encoding="utf-8")
            task.profile=Mock(return_value=new)
            result=task.scan(["26.10.0"],["x86/64"])
            task.profile.assert_called_once_with("26.10.0","x86/64")
            self.assertEqual(result["profiles"],[old,new])

    def test_damaged_ledger_cannot_silently_recompute(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=ProfileLedger(directory);p=self.profile("24.10.8","x86/64");ledger.save(p)
            path=ledger.path("24.10.8","x86/64");data=json.loads(path.read_text())
            data["profile"]["roots"]=[]
            path.write_text(json.dumps(data),encoding="utf-8")
            with self.assertRaises(ValueError):ledger.load("24.10.8","x86/64")

class ReleaseCoverageTests(unittest.TestCase):
    def test_discovery_includes_21_02_and_22_03_stable_releases_only(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Collector(directory)
            names = ["21.02." + str(i) for i in range(8)] + ["22.03." + str(i) for i in range(8)] + ["23.05.0", "24.10.0", "25.12.0"]
            names += ["22.03.0-rc1", "21.02.0-rc4", "22.04.0"]
            task.http.get = Mock(return_value="".join('<a href="' + n + '/">' for n in names).encode())
            self.assertEqual(task.releases(), names[:19])

    def test_official_missing_target_does_not_create_installable_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Collector(directory)
            task.http.official = Mock(side_effect=FileNotFoundError("upstream 404"))
            profile = task.profile("22.03.7", "mediatek/filogic")
            self.assertEqual(profile["status"], "not-provided-by-upstream")
            self.assertNotIn("packages", profile)

    def test_connection_error_is_not_misreported_as_missing_firmware(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Collector(directory)
            task.http.official = Mock(side_effect=RuntimeError("network unavailable"))
            with self.assertRaises(RuntimeError):
                task.profile("22.03.7", "mediatek/filogic")

class RootExtensionTests(unittest.TestCase):
    def test_extension_keeps_old_package_versions_and_adds_transitive_libraries(self):
        from modules.capture_services.openwrt.bundle_specs import COMMON_ROOTS
        from .extend_common_bundle import extend_profile
        extra = {"wpa-cli", "rpcd", "rpcd-mod-iwinfo"}
        existing = [package(name) for name in sorted(COMMON_ROOTS - extra)]
        old_library = package("kept-library"); old_library["version"] = "old-version"
        existing.append(old_library)
        rows = [package("wpa-cli", ["helper-library", "kept-library"]), package("rpcd"),
                package("rpcd-mod-iwinfo", ["rpcd"]), package("helper-library"),
                dict(old_library, version="new-version")]
        profile = dict(packages=existing, roots=sorted(COMMON_ROOTS-extra), firmware_required=["libc"])
        extended = extend_profile(profile, rows)
        actual = {r["name"]: r for r in extended["packages"]}
        self.assertEqual(actual["kept-library"], old_library)
        self.assertIn("helper-library", actual)
        self.assertEqual(set(extended["roots"]), COMMON_ROOTS)
        self.assertEqual(profile["packages"], existing)

if __name__ == "__main__":
    unittest.main()
