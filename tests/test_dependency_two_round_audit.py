"""Independent boundary checks for firmware candidates and fixed hcx installation."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest

from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
from modules.capture_services.openwrt.candidate_installer import install_local_candidates
from modules.capture_services.openwrt.candidate_policy import CandidatePlanRejected
from modules.capture_services.openwrt.firmware_profile import repository_paths, compatible_release
from modules.capture_services.openwrt.signal_package_feed import normalize_profile, index_urls
from modules.capture_services.openwrt.apk_package_feed import normalize_apk_profile, index_sources
from modules.capture_services.openwrt.signal_manager import SignalError


class CandidateCommitBoundaryTests(unittest.TestCase):
    def setup_case(self, root):
        device=NS(version="SNAPSHOT",distribution="1004",target="x86/64",
                  package_architecture="x86_64",package_manager="apk")
        base=dict(target="x86/64",architecture="x86_64",package_manager="apk",indexes=[])
        row=dict(name="tmux",version="1",filename="tmux-1.apk",feed="base")
        profiles=[dict(base,release="25.12.6",packages=[]),dict(base,release="25.12.0",packages=[row])]
        path=root/"tmux-1.apk";path.write_bytes(b"fixture")
        repo=NS(catalog={"profiles":profiles},materialize=Mock(return_value=path))
        commands=[]
        def stage(name,*_):
            commands.append(name)
            return "(1/1) Installing tmux (1)" if name=="apk_simulate_plan" else "OK:"
        installer=ApkOfflineInstaller(stage,uploader=Mock())
        return device,profiles,repo,installer,commands

    def test_missing_candidate_file_tries_next_before_any_install(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            d,profiles,repo,installer,commands=self.setup_case(root)
            result=install_local_candidates(installer,("tmux",),d,None,repo,root,Event(),Mock())
            self.assertEqual(result,[("tmux","1")])
            self.assertEqual(commands.count("apk_simulate_plan"),2)
            self.assertEqual(commands.count("apk_install"),1)
            repo.materialize.assert_called_once()
            self.assertEqual(commands.count("apk_cleanup"),2)

    def test_all_candidates_incomplete_are_retryable_without_installing(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            d,profiles,repo,installer,commands=self.setup_case(root)
            profiles[1]["packages"]=[]
            with self.assertRaises(CandidatePlanRejected):
                install_local_candidates(installer,("tmux",),d,None,repo,root,Event(),Mock())
            repo.materialize.assert_not_called()
            self.assertNotIn("apk_install",commands)

    def test_signature_or_blob_failure_never_retries_another_candidate(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            d,profiles,repo,installer,commands=self.setup_case(root)
            profiles[0]["packages"]=profiles[1]["packages"]
            repo.materialize.side_effect=ValueError("blob integrity failure")
            with self.assertRaisesRegex(ValueError,"integrity failure"):
                install_local_candidates(installer,("tmux",),d,None,repo,root,Event(),Mock())
            self.assertEqual(commands.count("apk_simulate_plan"),1)
            self.assertNotIn("apk_install",commands)

    def test_actual_commit_error_never_installs_next_candidate(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            d,profiles,repo,installer,commands=self.setup_case(root)
            profiles[0]["packages"]=profiles[1]["packages"]
            original=installer.stage
            def fail(name,*args):
                if name=="apk_install":
                    commands.append(name)
                    raise SignalError("transport","connection lost during actual install")
                return original(name,*args)
            installer.stage=fail
            with self.assertRaises(SignalError):
                install_local_candidates(installer,("tmux",),d,None,repo,root,Event(),Mock())
            self.assertEqual(commands.count("apk_simulate_plan"),1)
            self.assertEqual(commands.count("apk_install"),1)


class CanonicalIdentityTests(unittest.TestCase):
    def test_official_urls_use_canonical_case_and_numeric_release(self):
        cases=(("23.05.00-RC02","23.05.0-rc2"),("24.10-snapshot","24.10-SNAPSHOT"),
               ("snapshot","SNAPSHOT"),("24.10.08","24.10.8"))
        for identity,expected in cases:
            for pm in ("opkg","apk"):
                with self.subTest(identity=identity,pm=pm):
                    p=(normalize_profile if pm=="opkg" else normalize_apk_profile)(identity,"x86_64",pm)
                    self.assertEqual(p.release,expected)
                    urls=([u for _,u in index_urls(p)] if pm=="opkg" else [u for _,_,u in index_sources(p)])
                    if expected=="SNAPSHOT":self.assertTrue(all("/snapshots/" in u for u in urls))
                    else:self.assertTrue(any("/releases/"+expected+"/" in u for u in urls))
                    self.assertEqual(repository_paths(identity),repository_paths(expected))
        self.assertTrue(compatible_release(NS(version="23.05.00-RC02"),"23.05.6"))

    def test_missing_fields_keep_their_specific_error(self):
        for normalize,pm in ((normalize_profile,"opkg"),(normalize_apk_profile,"apk")):
            with self.assertRaises(ValueError):normalize("","x86_64",pm)
            with self.assertRaises(ValueError):normalize("23.05.0-rc2","",pm)


class PrimaryIdentityBoundaryTests(unittest.TestCase):
    def test_wrong_hcx_identity_is_rejected_before_touching_libraries(self):
        from modules.capture_services.openwrt.hcx_primary_installer import _install_package
        d=NS(package_manager="opkg",package_architecture="x86_64")
        for row in (dict(name="hcxdumptool",version="6.2.4-r1",architecture="x86_64"),
                    dict(name="hcxdumptool",version="6.3.4-r1",architecture="mips_24kc"),
                    dict(name="other",version="6.3.4-r1",architecture="x86_64")):
            manager=Mock()
            with self.subTest(row=row),self.assertRaises(SignalError):
                _install_package(manager,d,Path("unused"),row,True,Event(),Mock(),None,None)
            manager.command_runner.run.assert_not_called()


if __name__=="__main__":unittest.main()
