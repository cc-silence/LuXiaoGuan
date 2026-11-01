"""Any package architecture can use exactly hcx 6.3.4 without library replacement."""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest

from tests.test_common_offline_bundle import fixture
from tests.hcx_bundle_fixture import resign, prepare_hcx
from modules.capture_services.openwrt.hcx_primary_installer import install_primary
from modules.capture_services.openwrt.hcx_online import online_primary_package
from modules.capture_services.openwrt.hcx_policy import supports_modern_hcx
from modules.capture_services.openwrt.signal_manager import SignalError

ARCHES=("aarch64_cortex-a53","aarch64_cortex-a72","arm_cortex-a15_neon-vfpv4",
        "arm_cortex-a7_neon-vfpv4","arm_cortex-a9","arm_cortex-a9_vfpv3-d16",
        "mips_24kc","mipsel_24kc","x86_64")


class LocalArchitectureTests(unittest.TestCase):
    def test_all_architectures_and_package_formats_use_signed_exact_version(self):
        for pm in ("opkg","apk"):
            for arch in ARCHES:
                with self.subTest(pm=pm,arch=arch),TemporaryDirectory() as directory:
                    root=Path(directory)
                    f=fixture(root/"source",apk=pm=="apk",kind="capture")
                    f.device.package_architecture=arch
                    f.device.version="25.12.6" if pm=="apk" else "23.05.0-rc2"
                    f.device.kernel_version="5.15.118"
                    f.profile["architecture"]=arch
                    for row in f.profile["packages"]:
                        row["architecture"]=arch
                        if row["name"]=="hcxdumptool":
                            row["version"]="6.3.4-r1"
                    resign(f)
                    repo=prepare_hcx(f,root/"repo")
                    with patch("modules.capture_services.openwrt.common_bundle_repository.CommonBundleRepository",return_value=repo), \
                         patch("modules.capture_services.openwrt.hcx_primary_installer._install_package",return_value=True) as install, \
                         patch("modules.capture_services.openwrt.hcx_online.fetch_bytes",side_effect=AssertionError("offline")):
                        self.assertTrue(install_primary(Mock(),f.device,True,Event(),Mock(),None,None))
                        selected=install.call_args.args[3]
                        self.assertEqual((selected["version"],selected["architecture"]),("6.3.4-r1",arch))
                        path=install.call_args.args[2]
                    self.assertFalse(path.exists())

    def test_unknown_architecture_does_not_bypass_firmware_kernel_gate(self):
        for version,kernel,expected in (("23.05.0-rc2","5.15.118",True),
                                        ("24.10-SNAPSHOT","6.6.1",True),
                                        ("22.03.7","5.15.118",False),
                                        ("23.05.6","5.4.188",False)):
            d=NS(version=version,kernel_version=kernel,package_manager="opkg",package_architecture="new_arch")
            self.assertEqual(supports_modern_hcx(d),expected)
            if not expected:
                with patch("modules.capture_services.openwrt.hcx_bundle.primary_package",side_effect=AssertionError("forbidden")):
                    self.assertFalse(install_primary(Mock(),d,True,Event(),Mock(),None,None))


class OnlineArchitectureTests(unittest.TestCase):
    def device(self,pm="opkg",arch="x86_64"):
        return NS(package_manager=pm,package_architecture=arch)

    def fetch(self,raw,pm="opkg",arch="x86_64",version="6.3.4-r1",index_arch=None,wrong_feed=False):
        digest=hashlib.sha256(raw).hexdigest()
        def fetch(url,**kwargs):
            if url.endswith("/Packages"):
                return ("Package: hcxdumptool\nVersion: "+version+"\nArchitecture: "+(index_arch or arch)+
                        "\nFilename: hcxdumptool_"+version+"_"+arch+".ipk\nSize: "+str(len(raw))+
                        "\nSHA256sum: "+digest+"\n\n").encode()
            if url.endswith("/index.json"):
                return json.dumps(dict(architecture=index_arch or arch,packages={"hcxdumptool":version})).encode()
            if url.endswith("/sha256sums"):
                return (digest+" *"+("base/" if wrong_feed else "packages/")+"hcxdumptool-"+version+".apk\n").encode()
            self.assertTrue(url.endswith((".ipk",".apk")))
            return raw
        return fetch

    def test_new_architectures_download_exact_634_for_both_formats(self):
        raw=b"hcx architecture fixture"
        for pm in ("opkg","apk"):
            for arch in ("x86_64","mips_24kc","arm_cortex-a7_neon-vfpv4","new_arch"):
                with self.subTest(pm=pm,arch=arch),patch("modules.capture_services.openwrt.hcx_online.fetch_bytes",side_effect=self.fetch(raw,pm,arch)):
                    with online_primary_package(self.device(pm,arch),Event(),Mock()) as (path,row):
                        self.assertEqual(path.read_bytes(),raw)
                        self.assertEqual((row["version"],row["architecture"],row["size"]),("6.3.4-r1",arch,len(raw)))
                    self.assertFalse(path.exists())

    def test_different_version_or_architecture_never_downloads_body(self):
        for pm in ("opkg","apk"):
            for version,index_arch in (("6.2.4-r1",None),("6.3.40-r1",None),("6.4.0-r1",None),("6.3.4-r1","mips_24kc")):
                with self.subTest(pm=pm,version=version,arch=index_arch):
                    fetch=Mock(side_effect=self.fetch(b"body",pm,version=version,index_arch=index_arch))
                    with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes",fetch),self.assertRaises(SignalError):
                        with online_primary_package(self.device(pm),Event(),Mock()):
                            self.fail("No substitute version or architecture")
                    self.assertFalse(any(c.args[0].endswith((".ipk",".apk")) for c in fetch.call_args_list))

    def test_apk_hash_from_other_feed_is_not_accepted(self):
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes",side_effect=self.fetch(b"body","apk",wrong_feed=True)),self.assertRaises(SignalError):
            with online_primary_package(self.device("apk"),Event(),Mock()):pass

    def test_new_architecture_hash_mismatch_never_reaches_installer(self):
        fetch=self.fetch(b"body")
        def changed(url,**kwargs):
            return b"evil" if url.endswith(".ipk") else fetch(url,**kwargs)
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes",side_effect=changed),self.assertRaises(SignalError):
            with online_primary_package(self.device(),Event(),Mock()):pass

    def test_index_cancellation_never_downloads_body(self):
        cancel=Event()
        def stop(*args,**kwargs):
            cancel.set()
            raise InterruptedError("cancelled")
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes",side_effect=stop) as fetch,self.assertRaises(InterruptedError):
            with online_primary_package(self.device(),cancel,Mock()):pass
        fetch.assert_called_once()

    def test_unreadable_index_is_network_failure_not_unsupported_architecture(self):
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes",side_effect=RuntimeError("network timeout")) as fetch:
            with self.assertRaises(SignalError) as result:
                with online_primary_package(self.device(),Event(),Mock()):pass
        self.assertEqual(result.exception.code,"transport")
        self.assertEqual(fetch.call_count,3)
        self.assertFalse(any(c.args[0].endswith((".ipk",".apk")) for c in fetch.call_args_list))

    def test_eligible_missing_architecture_package_cannot_fall_back_to_old_feed(self):
        from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
        from modules.capture_services.openwrt.models import DependencyInfo, DependencyState
        manager=OpenWrtDependencyManager(Mock())
        manager.check=Mock(return_value=(DependencyInfo("pmkid","PMKID",DependencyState.MISSING,("hcxdumptool",)),))
        manager._install_online=Mock()
        manager._install_offline=Mock()
        d=NS(version="23.05.6",kernel_version="5.15.118",package_manager="opkg",package_architecture="new_arch")
        with patch("modules.capture_services.openwrt.hcx_bundle.primary_package",side_effect=SignalError("dependencies","No 6.3.4")), \
             patch("modules.capture_services.openwrt.common_bundle_installer.install_from_local") as local, \
             self.assertRaises(SignalError):
            manager.install_all({"pmkid":True},True,d,None,Event(),Mock(),include_sftp=False)
        manager._install_online.assert_not_called()
        manager._install_offline.assert_not_called()
        local.assert_not_called()
