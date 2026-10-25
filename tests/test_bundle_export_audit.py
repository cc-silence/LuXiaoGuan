"""Two-round official-contract and failure-path audit for offline extraction."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from scripts.openwrt_packages.test_common_bundle import package
from tests.test_bundle_export import prepared
from modules.capture_services.openwrt.bundle_dependencies import closure
from modules.capture_services.openwrt.bundle_export import export_packages,_export_rows
from modules.capture_services.openwrt import apk_version

class DependencyAuditTests(unittest.TestCase):
    def test_ipk_version_constraints_and_virtual_provider_versions_are_checked(self):
        root=package("root",["virtual (>= 2)"])
        old=package("library",provides=["virtual"]);old["version"]="1"
        with self.assertRaises(ValueError):closure([root,old],["root"])
        old["version"]="2"
        self.assertEqual({r["name"] for r in closure([root,old],["root"])[0]},{"root","library"})

    def test_apk_versions_and_provided_alias_versions_are_not_discarded(self):
        root=package("root",[dict(name="virtual",match=5,version="2.0")])
        library=package("library",provides=[dict(name="virtual",version="1.0",match=1)])
        library["version"]="9.0"
        with self.assertRaises(ValueError):closure([root,library],["root"])
        library["provides"][0]["version"]="2.0"
        self.assertEqual(len(closure([root,library],["root"])[0]),2)

    def test_apk_exact_and_minimum_requirement_reject_older_real_package(self):
        for mask in (1,5):
            root=package("root",[dict(name="library",match=mask,version="2.0-r1")])
            library=package("library");library["version"]="1.9-r9"
            with self.subTest(mask=mask),self.assertRaises(ValueError):closure([root,library],["root"])
            library["version"]="2.0-r1"
            self.assertEqual(len(closure([root,library],["root"])[0]),2)

    def test_alternative_and_provider_backtracking_drops_failed_candidate(self):
        root=package("root",["broken | good"])
        broken=package("broken",["missing"])
        good=package("good")
        selected,_=closure([root,broken,good],["root"])
        self.assertEqual({r["name"] for r in selected},{"root","good"})
        root["depends"]=["virtual (>= 2)"]
        broken["provides"]=["virtual"];broken["version"]="3"
        good["provides"]=["virtual"];good["version"]="2"
        selected,_=closure([root,broken,good],["root"])
        self.assertEqual({r["name"] for r in selected},{"root","good"})

    def test_known_apk_negative_dependency_rejects_conflicting_multi_selection(self):
        root=package("root",[dict(name="other",match=16,version="")])
        other=package("other")
        self.assertEqual(len(closure([root,other],["root"])[0]),1)
        with self.assertRaises(ValueError):closure([root,other],["root","other"])

    def test_kernel_module_cannot_hide_behind_virtual_alias(self):
        root=package("root",["virtual"])
        with self.assertRaisesRegex(ValueError,"kernel module"):
            closure([root,package("kmod-example",provides=["virtual"])],["root"])

    def test_windows_case_insensitive_package_filename_collision_is_rejected(self):
        root=package("root",["upper","lower"])
        upper=package("upper");upper["filename"]="Lib-1.ipk"
        lower=package("lower");lower["filename"]="lib-1.ipk"
        profile=dict(roots=["root"],packages=[root,upper,lower])
        with self.assertRaises(ValueError):_export_rows(profile,["root"])

    def test_upstream_apk_golden_samples_and_invalid_package_versions(self):
        path=Path("tests/fixtures/official_apk_versions.data")
        evidence=json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),evidence["sha256"])
        valid=invalid=0
        for raw in path.read_text(encoding="utf-8").splitlines():
            line=raw.split("#",1)[0].strip()
            if not line:continue
            parts=line.split()
            if len(parts)==1:
                if line.startswith("!"):
                    with self.assertRaises(ValueError):apk_version.tokens(line[1:])
                    invalid+=1
                else:
                    apk_version.tokens(line);valid+=1
                continue
            left,operator,right=parts
            try:apk_version.tokens(left);apk_version.tokens(right)
            except ValueError:
                # The upstream legacy comparison corpus also includes invalid forms.
                invalid+=1;continue
            mask=0
            for char in operator.lstrip("!"):mask|={"<":2,">":4,"=":1,"~":9}[char]
            if operator.startswith("!"):mask|=16
            self.assertTrue(apk_version.matches(left,mask,right),raw)
            valid+=1
        self.assertEqual((valid,invalid),(756,29))

class FileFailureAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.root,self.public,self.selection,self.wanted,_=prepared(self.folder/"fixture")
        self.output=self.folder/"output";self.output.mkdir()

    def export(self,progress=None):
        return export_packages("common",self.selection,self.wanted,self.output,Event(),progress,root=self.root,public_key=self.public)

    def test_cleanup_failure_retains_original_error_and_temporary_location(self):
        with patch("modules.capture_services.openwrt.bundle_export._copy_verified",side_effect=ValueError("original-checksum-error")),patch(
                "modules.capture_services.openwrt.bundle_export.shutil.rmtree",side_effect=PermissionError("cleanup denied")):
            with self.assertRaises(RuntimeError) as error:self.export()
        self.assertIn("original-checksum-error",str(error.exception))
        self.assertIn(".lxg-export-",str(error.exception))
        self.assertIsInstance(error.exception.__cause__,ValueError)

    def test_publish_failure_removes_partial_files_without_touching_existing_files(self):
        marker=self.output/"keep.txt";marker.write_bytes(b"keep")
        with patch("modules.capture_services.openwrt.bundle_export.Path.rename",side_effect=PermissionError("rename denied")):
            with self.assertRaises(PermissionError):self.export()
        self.assertEqual(list(self.output.iterdir()),[marker])
        self.assertEqual(marker.read_bytes(),b"keep")

    def test_copy_deadline_stops_and_cleans_without_reported_success(self):
        clock=[0.0]
        def progress(value):
            if value.get("completed",0)>0:clock[0]=400.0
        with patch("modules.capture_services.openwrt.bundle_export.time.monotonic",side_effect=lambda:clock[0]):
            with self.assertRaises(TimeoutError):self.export(progress)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_export_into_other_managed_bundle_directory_is_rejected(self):
        managed=self.folder/"managed";other=managed/"openwrt_capture";other.mkdir(parents=True)
        with patch("modules.capture_services.openwrt.bundle_export.bundle_root",return_value=managed/"openwrt_common"):
            with self.assertRaises(ValueError):
                export_packages("common",self.selection,self.wanted,other,Event(),root=self.root,public_key=self.public)
        self.assertEqual(list(other.iterdir()),[])

if __name__=="__main__":unittest.main()
