"""Protected KWrt/SNAPSHOT candidates use the original installation pipeline."""
import copy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from tests import test_common_offline_bundle as bundle_tests
from modules.capture_services.openwrt.candidate_policy import verify_candidate_packages
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.dependency_resolution import InstalledPackages
from modules.capture_services.openwrt.signal_package_feed import normalize_profile, index_urls, download_profile_packages, FeedPackage
from modules.capture_services.openwrt.snapshot_bundle import candidate_profiles, select_profile, SnapshotBundleMismatch
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.package_manager import PackageManager


class LocalCandidateTests(unittest.TestCase):
    setUp = bundle_tests.CommonOfflineTests.setUp
    tearDown = bundle_tests.CommonOfflineTests.tearDown

    def kwrt(self, version="24.10-SNAPSHOT"):
        d=copy.copy(self.f.device)
        d.distribution="Kwrt"
        d.version=version
        return d

    def test_kwrt_snapshot_and_exact_stable_use_signed_local_wpa_cli(self):
        for version in ("24.10-SNAPSHOT", self.f.device.version):
            with self.subTest(version=version):
                manager=NS(command_runner=Mock(), _execute_stage=Mock(return_value="libc - 1\n"), _install_offline=Mock())
                self.assertTrue(install_from_local(manager, ("wpa-cli",), self.kwrt(version),
                    self.root/"work", True, Event(), Mock(), repository=self.repo))
                self.assertEqual(manager._install_offline.call_args.kwargs["local_packages"], {"wpa-cli":"1"})

    def test_identity_mismatch_never_gets_a_candidate(self):
        d=self.kwrt()
        self.assertEqual(len(candidate_profiles(self.repo,d)),1)
        for key,value in (("target","x86/64"),("package_architecture","mips_24kc"),
                          ("version","23.05-SNAPSHOT"),("version","SNAPSHOT"),
                          ("distribution","OtherFirmware"),("package_manager","apk"),("version","24.10.1")):
            other=copy.copy(d);setattr(other,key,value)
            self.assertEqual(candidate_profiles(self.repo,other),())

    def test_new_system_library_skips_candidate_without_upload(self):
        manager=NS(command_runner=Mock(), _execute_stage=Mock(return_value="libc - 1\n"), _install_offline=Mock())
        self.assertFalse(install_from_local(manager, ("libuci-lua",), self.kwrt(),
            self.root/"work", True, Event(), Mock(), repository=self.repo))
        manager._install_offline.assert_not_called()

    def test_incompatible_dependency_does_not_replace_existing_library(self):
        profile=self.repo.profile(self.f.device)
        next(r for r in profile["packages"] if r["name"]=="wpa-cli")["depends"]=["libc (>= 2)"]
        with self.assertRaises(SnapshotBundleMismatch):
            select_profile(self.repo,self.kwrt(),("wpa-cli",),InstalledPackages({"libc":"1"}),
                           lambda *_:False,Event(),Mock())

    def test_missing_base_version_is_not_assumed_compatible(self):
        with self.assertRaises(ValueError):
            select_profile(self.repo,self.kwrt(),("wpa-cli",),InstalledPackages({}),None,Event(),Mock())

    def test_signed_blob_corruption_stops_instead_of_trying_install(self):
        profile=self.repo.profile(self.f.device)
        row=next(r for r in profile["packages"] if r["name"]=="wpa-cli")
        (self.repo.content_root/row["blob"]).write_bytes(b"bad")
        manager=NS(command_runner=Mock(), _execute_stage=Mock(return_value="libc - 1\n"), _install_offline=Mock())
        with self.assertRaises(Exception):
            install_from_local(manager,("wpa-cli",),self.kwrt(),self.root/"work",True,Event(),Mock(),repository=self.repo)
        manager._install_offline.assert_not_called()

    def test_precommit_candidate_rejection_uses_next_route_and_retains_detail(self):
        from modules.capture_services.openwrt.candidate_policy import CandidatePlanRejected
        from modules.capture_services.openwrt.signal_manager import SignalError
        from modules.capture_services.openwrt.installation_sources import install_with_sources
        reason=SignalError("environment","native plan conflict")
        reason.package_diagnostic="dependency_local_plan exit=1\nfile conflict"
        manager=NS(command_runner=Mock(),_execute_stage=Mock(return_value="libc - 1\n"),
                   _install_offline=Mock(side_effect=CandidatePlanRejected(reason)))
        online=Mock();computer=Mock();tell=Mock()
        install_with_sources(lambda:install_from_local(manager,("wpa-cli",),self.kwrt(),
            self.root/"work",True,Event(),tell,repository=self.repo),online,computer,tell)
        online.assert_called_once_with()
        computer.assert_not_called()
        self.assertIn("file conflict",str(tell.call_args_list))

    def test_actual_install_failure_does_not_start_another_install_route(self):
        from modules.capture_services.openwrt.signal_manager import SignalError
        from modules.capture_services.openwrt.installation_sources import install_with_sources
        original=SignalError("transport","actual install failure")
        manager=NS(command_runner=Mock(),_execute_stage=Mock(return_value="libc - 1\n"),
                   _install_offline=Mock(side_effect=original))
        online=Mock();computer=Mock()
        with self.assertRaises(SignalError) as caught:
            install_with_sources(lambda:install_from_local(manager,("wpa-cli",),self.kwrt(),
                self.root/"work",True,Event(),Mock(),repository=self.repo),online,computer,Mock())
        self.assertIs(caught.exception,original)
        online.assert_not_called();computer.assert_not_called()

    def test_unsafe_new_components_are_rejected(self):
        for name in ("libc","kmod-mt76","wpad-basic","hostapd-common","wpa-supplicant","netifd","lua","luci-app-commands"):
            with self.subTest(name=name),self.assertRaises(ValueError):
                verify_candidate_packages((NS(name=name),),InstalledPackages({"libc":"1"}),kwrt=True)

    def test_existing_package_is_never_reinstalled(self):
        with self.assertRaises(ValueError):
            verify_candidate_packages((NS(name="wpa-cli"),),InstalledPackages({"libc":"1","wpa-cli":"1"}),kwrt=True)


class DownloadCandidateTests(unittest.TestCase):
    def test_versioned_snapshot_uses_its_own_branch_and_all_supported_series(self):
        for family in ("19.07","21.02","22.03","23.05","24.10","25.12"):
            p=normalize_profile(family+"-SNAPSHOT","aarch64_cortex-a53","opkg")
            self.assertEqual(p.compatibility_trial,"snapshot")
            self.assertTrue(all("/releases/"+family+"-SNAPSHOT/" in url for _,url in index_urls(p)))
        with self.assertRaises(ValueError):
            normalize_profile("SNAPSHOT","aarch64_cortex-a53","opkg")

    def test_stable_official_profile_keeps_existing_policy(self):
        self.assertEqual(normalize_profile("24.10.4","aarch64_cortex-a53","opkg").compatibility_trial,"")
        self.assertEqual(normalize_profile("24.10.4","aarch64_cortex-a53","opkg",distribution="Kwrt").compatibility_trial,"kwrt")

    def test_missing_required_library_is_rejected_before_package_download(self):
        profile=normalize_profile("24.10-SNAPSHOT","aarch64_cortex-a53","opkg",distribution="Kwrt")
        dep=FeedPackage("libextra","1","libextra.ipk","a"*64,1,"https://example.invalid/libextra.ipk")
        root=FeedPackage("wpa-cli","1","wpa-cli.ipk","b"*64,1,"https://example.invalid/wpa-cli.ipk",("libextra",))
        fetch=Mock()
        with TemporaryDirectory() as folder,patch("modules.capture_services.openwrt.signal_package_feed.fetch_index",return_value={"wpa-cli":root,"libextra":dep}):
            with self.assertRaises(ValueError):
                download_profile_packages(profile,InstalledPackages({"libc":"1"}),folder,fetch=fetch,wanted=("wpa-cli",))
        fetch.assert_not_called()

    def test_compatible_tool_download_is_hashed_before_original_install_path(self):
        raw=b"fixture package"
        root=FeedPackage("wpa-cli","1","wpa-cli.ipk",hashlib.sha256(raw).hexdigest(),len(raw),
                        "https://example.invalid/wpa-cli.ipk",("libc",))
        profile=normalize_profile("24.10-SNAPSHOT","aarch64_cortex-a53","opkg",distribution="Kwrt")
        with TemporaryDirectory() as folder,patch("modules.capture_services.openwrt.signal_package_feed.fetch_index",return_value={"wpa-cli":root}):
            paths=download_profile_packages(profile,InstalledPackages({"libc":"1"}),folder,fetch=Mock(return_value=raw),wanted=("wpa-cli",))
            self.assertEqual(len(paths),1)
            self.assertEqual(paths[0].read_bytes(),raw)

    def test_native_plan_refusal_never_starts_install(self):
        from modules.capture_services.openwrt.signal_manager import SignalError
        for plan in ("Installing wpa-cli (1) to root...\nUpgrading libc from 1 to 2...",
                     "Installing other (1) to root..."):
            with self.subTest(plan=plan),TemporaryDirectory() as folder:
                path=Path(folder)/"wpa-cli.ipk";path.write_bytes(b"fixture")
                manager=OpenWrtDependencyManager(Mock())
                manager._preflight=Mock(return_value=PackageManager("opkg"))
                manager._sftp_usable=Mock(return_value=False)
                manager._execute=Mock(return_value="")
                manager._execute_stage=Mock(side_effect=lambda key,*args:plan if key=="dependency_local_plan" else "")
                with patch("modules.capture_services.openwrt.package_space.ipk_installed_size",return_value=100):
                    with self.assertRaises(SignalError):
                        manager._install_offline(("wpa-cli",),NS(),folder,False,Event(),Mock(),Mock(),
                            local_paths=(path,),local_packages={"wpa-cli":"1"})
                self.assertNotIn("dependency_offline_install",[c.args[0] for c in manager._execute_stage.call_args_list])

    def test_trial_native_plan_rejection_is_marked_precommit(self):
        from modules.capture_services.openwrt.candidate_policy import CandidatePlanRejected
        with TemporaryDirectory() as folder:
            path=Path(folder)/"wpa-cli.ipk";path.write_bytes(b"fixture")
            manager=OpenWrtDependencyManager(Mock())
            manager._preflight=Mock(return_value=PackageManager("opkg"))
            manager._sftp_usable=Mock(return_value=False)
            manager._execute=Mock(return_value="")
            manager._execute_stage=Mock(side_effect=lambda key,*args:
                "Installing other (1) to root..." if key=="dependency_local_plan" else "")
            with patch("modules.capture_services.openwrt.package_space.ipk_installed_size",return_value=100):
                with self.assertRaises(CandidatePlanRejected):
                    manager._install_offline(("wpa-cli",),NS(),folder,False,Event(),Mock(),Mock(),
                        local_paths=(path,),local_packages={"wpa-cli":"1"},candidate_trial=True)
            self.assertNotIn("dependency_offline_install",[c.args[0] for c in manager._execute_stage.call_args_list])

    def test_cancel_does_not_download(self):
        cancel=Event();cancel.set()
        with TemporaryDirectory() as folder,self.assertRaises(RuntimeError):
            download_profile_packages(normalize_profile("24.10-SNAPSHOT","aarch64_cortex-a53","opkg"),
                InstalledPackages({"libc":"1"}),folder,cancel_event=cancel,fetch=Mock(side_effect=InterruptedError()),wanted=("wpa-cli",))
