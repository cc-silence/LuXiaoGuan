"""Signed local catalog extraction, dependency closure and atomic failure cleanup."""
import base64
import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from threading import Event
from unittest.mock import Mock,patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.test_common_offline_bundle import fixture,canonical
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.bundle_export import list_export_profiles,export_packages,ExportGuard

def prepared(folder,kind="common",apk=False,missing=False):
    folder=Path(folder);source=fixture(folder/"source",kind=kind,apk=apk)
    catalog=copy.deepcopy(source.catalog);profile=catalog["profiles"][0]
    blobs=dict(source.blobs)
    suffix=".apk" if apk else ".ipk"
    def dep(name):
        return dict(name=name,version="",match=0) if apk else name
    def package(name,depends):
        data=("payload:"+name).encode()
        digest=hashlib.sha256(data).hexdigest();blobs[digest]=data
        row=copy.deepcopy(profile["packages"][0])
        row.update(name=name,version="1",filename=name+"-1"+suffix,depends=[dep(n) for n in depends],
                   sha256=digest,blob="objects/"+digest,size=len(data),provides=[])
        return row
    profile["packages"] += [package("lib-alpha",["lib-shared","libc"]),package("lib-shared",["libc"])]
    chosen=sorted(profile["roots"])[:2]
    for row in profile["packages"]:
        if row["name"] in chosen:
            row["depends"]=[dep("not-in-bundle" if missing else "lib-alpha")]
            if apk:
                row["depends"].append(dict(name="conflicting-package",version="",match=16))
    raw=canonical(catalog)
    with zipfile.ZipFile(source.archive,"w",zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("catalog.json",raw)
        for digest,data in blobs.items():zipped.writestr("objects/"+digest,data)
    payload=dict(source.payload)
    payload.update(size=source.archive.stat().st_size,sha256=hashlib.sha256(source.archive.read_bytes()).hexdigest(),
                   catalog_size=len(raw),catalog_sha256=hashlib.sha256(raw).hexdigest(),
                   unpacked_size=len(raw)+sum(len(data) for data in blobs.values()))
    payload["url"]=payload["url"].replace(source.payload["sha256"],payload["sha256"])
    private=Ed25519PrivateKey.generate()
    public=base64.b64encode(private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
    envelope=canonical(dict(payload=payload,signature=base64.b64encode(private.sign(canonical(payload))).decode()))
    root=folder/"resources"
    prepare_archive(source.archive,envelope,root,Event(),kind=kind,public_key=public)
    options=list_export_profiles(kind,Event(),root=root,public_key=public)
    selection={k:options["profiles"][0][k] for k in ("release","target","architecture","package_manager")}
    selection["revision"]=options["revision"]
    return root,public,selection,chosen,profile

class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.output=self.folder/"output";self.output.mkdir()

    def test_both_bundle_kinds_and_both_formats_export_exact_names_and_transitive_packages(self):
        for kind in ("common","capture"):
            for apk in (False,True):
                with self.subTest(kind=kind,apk=apk):
                    root,public,selection,wanted,profile=prepared(self.folder/(kind+str(apk)),kind,apk)
                    result=export_packages(kind,selection,wanted,self.output,Event(),root=root,public_key=public)
                    directory=Path(result["path"])
                    self.assertEqual(result["count"],4)
                    suffix=".apk" if apk else ".ipk"
                    self.assertEqual({p.name for p in directory.glob("*"+suffix)},
                                     {name+"-1"+suffix for name in wanted+["lib-alpha","lib-shared"]})
                    metadata=json.loads((directory/"export.json").read_text(encoding="utf-8"))
                    self.assertEqual(metadata["selected_roots"],wanted)
                    self.assertEqual(metadata["firmware_required"],["libc"])
                    self.assertTrue((directory/"依赖清单.txt").is_file())
                    for row in metadata["packages"]:
                        self.assertEqual(hashlib.sha256((directory/row["filename"]).read_bytes()).hexdigest(),row["sha256"])
                    self.assertTrue((root/"current.json").is_file())

    def test_missing_transitive_dependency_stops_without_output(self):
        root,public,selection,wanted,_=prepared(self.folder/"missing",missing=True)
        with self.assertRaisesRegex(ValueError,"not-in-bundle"):
            export_packages("common",selection,wanted,self.output,Event(),root=root,public_key=public)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_corruption_and_mid_copy_cancellation_remove_private_staging(self):
        for corrupt in (False,True):
            root,public,selection,wanted,profile=prepared(self.folder/str(corrupt))
            event=Event()
            if corrupt:
                row=next(row for row in profile["packages"] if row["name"] in wanted)
                (root/"versions"/selection["revision"]/row["blob"]).write_bytes(b"bad")
            def report(value):
                if not corrupt and value.get("total",0)>0:event.set()
            with self.assertRaises((ValueError,InterruptedError)):
                export_packages("common",selection,wanted,self.output,event,report,root=root,public_key=public)
            self.assertEqual(list(self.output.iterdir()),[])

    def test_stale_revision_wrong_profile_no_roots_and_unlisted_roots_are_rejected(self):
        root,public,selection,wanted,_=prepared(self.folder/"valid")
        for key in ("revision","release","target","architecture","package_manager"):
            changed=dict(selection);changed[key]="wrong"
            with self.subTest(key=key),self.assertRaises(ValueError):
                export_packages("common",changed,wanted,self.output,Event(),root=root,public_key=public)
        for names in ([],["unknown"]):
            with self.assertRaises(ValueError):
                export_packages("common",selection,names,self.output,Event(),root=root,public_key=public)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_export_keeps_existing_files_and_repeated_exports_create_separate_sets(self):
        root,public,selection,wanted,_=prepared(self.folder/"valid")
        (self.output/"keep.txt").write_bytes(b"keep")
        results=[export_packages("common",selection,wanted,self.output,Event(),root=root,public_key=public) for _ in range(2)]
        self.assertNotEqual(results[0]["path"],results[1]["path"])
        self.assertEqual((self.output/"keep.txt").read_bytes(),b"keep")

    def test_destination_cannot_be_inside_managed_resources_and_space_is_checked(self):
        root,public,selection,wanted,_=prepared(self.folder/"valid")
        with self.assertRaises(ValueError):
            export_packages("common",selection,wanted,root,Event(),root=root,public_key=public)
        with patch("modules.capture_services.openwrt.bundle_export.shutil.disk_usage",return_value=Mock(free=0)),self.assertRaises(ValueError):
            export_packages("common",selection,wanted,self.output,Event(),root=root,public_key=public)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_missing_catalog_tampered_catalog_and_deadline_stop(self):
        with self.assertRaises(ValueError):
            list_export_profiles("common",Event(),root=self.folder/"absent")
        root,public,selection,_,_=prepared(self.folder/"valid")
        (root/"versions"/selection["revision"]/"catalog.json").write_bytes(b"{}")
        with self.assertRaises(ValueError):
            list_export_profiles("common",Event(),root=root,public_key=public)
        with self.assertRaises(TimeoutError):ExportGuard(Event(),seconds=-1).check()

    def test_profile_list_contains_only_small_filter_metadata(self):
        root,public,selection,wanted,_=prepared(self.folder/"valid")
        result=list_export_profiles("common",Event(),root=root,public_key=public)
        profile=result["profiles"][0]
        self.assertNotIn("packages",profile)
        self.assertEqual(profile["target"],selection["target"])
        self.assertTrue(set(wanted).issubset({row["name"] for row in profile["roots"]}))

if __name__=="__main__":unittest.main()
