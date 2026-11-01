"""Release variants share authenticated sources and native compatibility checks."""
import copy
import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from tests import test_common_offline_bundle as fixtures
from modules.capture_services.openwrt.firmware_profile import compatible_release, release_kind
from modules.capture_services.openwrt.signal_package_feed import (
    normalize_profile, index_urls, fetch_index, download_profile_packages, FeedPackage)
from modules.capture_services.openwrt.apk_package_feed import normalize_apk_profile, index_sources, adb_sources, fetch_sha256sums
from modules.capture_services.openwrt.snapshot_bundle import candidate_profiles
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.candidate_policy import CandidatePlanRejected
from modules.capture_services.openwrt.dependency_resolution import InstalledPackages
from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
from modules.capture_services.openwrt.candidate_installer import install_local_candidates


class ReleaseTests(unittest.TestCase):
    def test_online_addresses_for_both_package_managers(self):
        for version in ("23.05.0-rc2", "24.10-SNAPSHOT", "SNAPSHOT", "24.10.8"):
            with self.subTest(version=version):
                p = normalize_profile(version, "mipsel_24kc", "opkg", distribution="1004")
                a = normalize_apk_profile(version, "mipsel_24kc", "apk")
                ipk = [u for _,u in index_urls(p)]
                apk = [u for _,_,u in index_sources(a)]
                adb = [u for _,_,u in adb_sources(a,"ramips/mt7621")]
                self.assertTrue(p.compatibility_trial)
                for urls in (ipk,apk,adb):
                    self.assertTrue(all(u.startswith("https://") for u in urls))
                    self.assertTrue(any("/snapshots/" in u for u in urls) if version == "SNAPSHOT"
                                    else any("/releases/"+version+"/" in u for u in urls))
                if version == "SNAPSHOT":
                    self.assertFalse(any("/releases/SNAPSHOT/" in u for u in ipk+apk+adb))

    def test_rc_never_crosses_release_series(self):
        d = NS(version="23.05.0-rc2")
        self.assertTrue(compatible_release(d,"23.05.7"))
        self.assertFalse(compatible_release(d,"24.10.8"))
        self.assertFalse(compatible_release(NS(version="garbage"),"24.10.8"))
        self.assertTrue(compatible_release(NS(version="SNAPSHOT"),"24.10.8"))

    def test_branch_source_404_falls_back_to_same_branch_shared_packages(self):
        raw = ("Package: wpa-cli\nVersion: 1\nFilename: wpa-cli.ipk\n"
               "SHA256sum: " + "a"*64 + "\nSize: 1\n\n").encode()
        def fetch(url, **_):
            if "24.10-SNAPSHOT" in url:
                raise RuntimeError("HTTP 404")
            self.assertIn("/releases/packages-24.10/",url)
            return raw
        p = normalize_profile("24.10-SNAPSHOT","mipsel_24kc","opkg")
        self.assertIn("wpa-cli",fetch_index(p,fetch,feeds=("base",),require_complete=True))

    def test_snapshot_apk_hash_source_is_the_same_tree(self):
        urls = []
        def fetch(url, **_):
            urls.append(url)
            return ("a"*64 + " *base/tool-1.apk\n").encode()
        self.assertEqual(fetch_sha256sums(fetch, normalize_apk_profile("SNAPSHOT","mipsel_24kc","apk")),
                         {"tool-1.apk":"a"*64})
        self.assertIn("/snapshots/packages/mipsel_24kc/sha256sums",urls[0])

    def test_unknown_and_unsafe_releases_are_not_guessed(self):
        for version in ("", "../../23.05", "Vendor2026", "23.05.0-rc2/evil"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                normalize_profile(version,"mipsel_24kc","opkg")

    def test_missing_compatible_runtime_library_downloads_with_tool(self):
        raw = b"package"
        def package(name, depends):
            return FeedPackage(name,"1",name+".ipk",hashlib.sha256(raw).hexdigest(),len(raw),
                               "https://example.invalid/"+name+".ipk",depends)
        index = {"hcxdumptool":package("hcxdumptool",("libpcap1",)),
                 "libpcap1":package("libpcap1",("libc",))}
        for version in ("23.05.0-rc2","23.05-SNAPSHOT","SNAPSHOT"):
            with self.subTest(version=version), TemporaryDirectory() as directory:
                p = normalize_profile(version,"mipsel_24kc","opkg",distribution="1004")
                with patch("modules.capture_services.openwrt.signal_package_feed.fetch_index",return_value=index):
                    paths = download_profile_packages(p,InstalledPackages({"libc":"1"}),directory,
                        fetch=Mock(return_value=raw),wanted=("hcxdumptool",))
                self.assertEqual([x.name for x in paths],["libpcap1.ipk","hcxdumptool.ipk"])


class LocalReleaseTests(unittest.TestCase):
    setUp = fixtures.CommonOfflineTests.setUp
    tearDown = fixtures.CommonOfflineTests.tearDown

    def device(self, version):
        d = copy.copy(self.f.device)
        d.distribution = "1004"
        d.version = version
        return d

    def test_rc_branch_and_mainline_use_protected_native_install(self):
        for version in ("24.10.0-rc2","24.10-SNAPSHOT","SNAPSHOT",self.f.device.version):
            with self.subTest(version=version):
                manager=NS(command_runner=Mock(),_execute_stage=Mock(return_value="libc - 1\n"),_install_offline=Mock())
                self.assertTrue(install_from_local(manager,("libuci-lua",),self.device(version),
                    self.root/"work",True,Event(),Mock(),repository=self.repo))
                self.assertTrue(manager._install_offline.call_args.kwargs["candidate_trial"])

    def test_wy_rc2_finds_same_series_and_architecture(self):
        self.repo._load()
        p = copy.deepcopy(self.repo.catalog["profiles"][0])
        p.update(release="23.05.7",target="ramips/mt7621",architecture="mipsel_24kc")
        self.repo.catalog["profiles"].append(p)
        d = self.device("23.05.0-rc2")
        d.target = "ramips/mt7621"
        d.package_architecture = "mipsel_24kc"
        candidates = candidate_profiles(self.repo,d)
        self.assertEqual([r["release"] for r in candidates],["23.05.7"])

    def test_native_preflight_refusal_tries_next_candidate_without_reinstalling(self):
        self.repo._load()
        p = copy.deepcopy(self.repo.catalog["profiles"][0])
        p["release"] = "24.10.0"
        self.repo.catalog["profiles"].append(p)
        manager=NS(command_runner=Mock(),_execute_stage=Mock(return_value="libc - 1\n"),
                   _install_offline=Mock(side_effect=[CandidatePlanRejected(ValueError("ABI mismatch")),None]))
        self.assertTrue(install_from_local(manager,("wpa-cli",),self.device("SNAPSHOT"),
            self.root/"work",True,Event(),Mock(),repository=self.repo))
        self.assertEqual(manager._install_offline.call_count,2)


class ApkCandidateTests(unittest.TestCase):
    def test_apk_candidates_retry_only_before_commit(self):
        repo=NS(catalog={"profiles":[dict(release="25.12.6",target="x86/64",architecture="x86_64",package_manager="apk"),
                                     dict(release="25.12.0",target="x86/64",architecture="x86_64",package_manager="apk")]})
        d=NS(version="SNAPSHOT",distribution="1004",target="x86/64",package_architecture="x86_64",package_manager="apk")
        installer=NS(install_local=Mock(side_effect=[CandidatePlanRejected(ValueError("ABI mismatch")),[]]))
        install_local_candidates(installer,("iw",),d,None,repo,"unused",Event(),Mock())
        self.assertEqual(installer.install_local.call_count,2)
        installer.install_local=Mock(side_effect=RuntimeError("actual install failed"))
        with self.assertRaises(RuntimeError):
            install_local_candidates(installer,("iw",),d,None,repo,"unused",Event(),Mock())
        self.assertEqual(installer.install_local.call_count,1)

    def test_apk_variants_use_native_apk_without_opkg_probe(self):
        from modules.capture_services.openwrt.common_bundle_download import prepare_archive
        from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
        from modules.capture_services.openwrt.package_manager import PackageManager
        for version in ("25.12.0-rc1", "25.12-SNAPSHOT", "SNAPSHOT"):
            with self.subTest(version=version), TemporaryDirectory() as directory:
                root = Path(directory)
                f = fixtures.fixture(root/"source",apk=True)
                prepare_archive(f.archive,f.envelope,root/"repo",Event(),public_key=f.public)
                repo = CommonBundleRepository(root/"repo",f.public)
                d = copy.copy(f.device)
                d.distribution = "1004"
                d.version = version
                def stage(name, command, *_):
                    self.assertNotIn("opkg",command)
                    return "(1/1) Installing tmux (1)" if name == "apk_simulate_plan" else "OK:"
                manager=NS(command_runner=Mock(),_preflight=Mock(return_value=PackageManager("apk")),
                           _execute_stage=Mock(side_effect=stage),_execute=Mock(return_value=""))
                upload=Mock()
                with patch("modules.capture_services.openwrt.package_space.check_plan_space"):
                    self.assertTrue(install_from_local(manager,("tmux",),d,root/"work",True,
                                    Event(),Mock(),upload,repository=repo))
                self.assertIn("apk_install",[c.args[0] for c in manager._execute_stage.call_args_list])
                self.assertTrue(any("tmux-1.apk" in c.args[0] for c in upload.call_args_list))

    def test_apk_trial_rejects_new_core_component(self):
        installer=ApkOfflineInstaller(lambda *_:"(1/1) Installing libc (2-r1)")
        installer.candidate_trial=True
        with self.assertRaises(ValueError):
            installer._simulate(("iw",))


if __name__ == "__main__":
    unittest.main()
