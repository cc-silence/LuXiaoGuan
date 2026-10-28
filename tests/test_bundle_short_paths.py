"""Short local object and archive names preserve signed identities and legacy data."""
import json
import zipfile
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from tests.test_common_offline_bundle import fixture
from tests.test_bundle_layout import prepare, legacy_copy
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository,validate_catalog
from modules.capture_services.openwrt.bundle_object_paths import object_names


class ShortObjectTests(unittest.TestCase):
    def test_numbering_is_order_independent_and_full_hash_prefix_collision_safe(self):
        a="a"*63+"0";b="a"*63+"1"
        self.assertEqual(object_names({a:1,b:2}),object_names({b:2,a:1}))
        self.assertEqual(set(object_names({a:1,b:2}).values()),{"000001","000002"})

    def test_short_objects_keep_exact_signed_catalog_for_both_kinds_and_formats(self):
        for kind in ("common","capture"):
            for apk in (False,True):
                with self.subTest(kind=kind,apk=apk),tempfile.TemporaryDirectory() as folder:
                    root=Path(folder);source=fixture(root/"source",kind=kind,apk=apk)
                    result=prepare(source,root/"cache",kind)
                    repo=CommonBundleRepository(root/"cache",source.public,kind);repo._load()
                    with zipfile.ZipFile(source.archive) as archive:
                        self.assertEqual((repo.content_root/"catalog.json").read_bytes(),archive.read("catalog.json"))
                    for sha,row in validate_catalog(repo.catalog,kind).items():
                        path=repo.object_path(row)
                        self.assertRegex(path.name,r"^[0-9]{6}$")
                        self.assertEqual(path.read_bytes(),source.blobs[sha])
                    self.assertEqual(result["sha256"],source.payload["sha256"])

    def test_preparing_existing_legacy_objects_shortens_in_place_without_changing_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=fixture(root/"source");target=root/"cache"
            legacy=legacy_copy(source,target)
            receipt=(target/"current.json").read_bytes()
            result=prepare(source,target)
            repo=CommonBundleRepository(target,source.public);repo._load()
            self.assertEqual(Path(result["content_path"]),legacy)
            for sha,row in validate_catalog(repo.catalog).items():
                self.assertEqual(repo.object_path(row).read_bytes(),source.blobs[sha])
                self.assertFalse((legacy/row["blob"]).exists())
            self.assertEqual((target/"current.json").read_bytes(),receipt)

    def test_unknown_short_name_in_legacy_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=fixture(root/"source");target=root/"cache"
            legacy=legacy_copy(source,target)
            occupied=legacy/"objects/000001";occupied.write_bytes(b"user data")
            with self.assertRaises(ValueError):prepare(source,target)
            self.assertEqual(occupied.read_bytes(),b"user data")
            self.assertFalse((legacy/"object-layout.json").exists())
            repo=CommonBundleRepository(target,source.public);repo._load()
            for row in source.profile["packages"]:repo.object_path(row)

    def test_interrupted_legacy_rename_remains_readable_and_can_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=fixture(root/"source");target=root/"cache"
            legacy=legacy_copy(source,target);original=Path.replace;count=[0]
            def replace(path,destination):
                if path.parent.name=="objects" and len(path.name)==64:
                    count[0]+=1
                    if count[0]==2:raise PermissionError("file in use")
                return original(path,destination)
            with patch.object(Path,"replace",replace):
                with self.assertRaises(PermissionError):prepare(source,target)
            repo=CommonBundleRepository(target,source.public);repo._load()
            for row in source.profile["packages"]:repo.object_path(row)
            prepare(source,target)
            repo=CommonBundleRepository(target,source.public);repo._load()
            for row in source.profile["packages"]:self.assertEqual(len(repo.object_path(row).name),6)


class ShortArchiveTests(unittest.TestCase):
    def test_legacy_zip_and_partial_move_to_short_names_and_retry_reuses_them(self):
        from modules.capture_services.openwrt.bundle_archive_paths import reserve_archive
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=fixture(root/"source");target=root/"cache"
            legacy=target/"archives"/source.payload["sha256"]/source.payload["filename"]
            legacy.parent.mkdir(parents=True);legacy.write_bytes(source.archive.read_bytes())
            part=legacy.with_name(legacy.name+".part");part.write_bytes(b"already downloaded")
            (legacy.parent/"resume.json").write_bytes(source.envelope)
            archive=reserve_archive(target,source.payload,source.envelope,Event())
            self.assertEqual(archive.name,"bundle.zip")
            self.assertRegex(archive.parent.name,r"^[0-9]{8}-[0-9]{2,4}$")
            self.assertEqual(archive.read_bytes(),source.archive.read_bytes())
            self.assertEqual((archive.parent/"bundle.zip.part").read_bytes(),b"already downloaded")
            self.assertFalse(legacy.parent.exists())
            self.assertEqual(reserve_archive(target,source.payload,source.envelope,Event()),archive)
            self.assertGreater(len(str(legacy))-len(str(archive)),53)

    def test_partial_move_interruption_reuses_same_short_directory(self):
        from modules.capture_services.openwrt.bundle_archive_paths import reserve_archive
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=fixture(root/"source");target=root/"cache"
            legacy=target/"archives"/source.payload["sha256"]/source.payload["filename"]
            legacy.parent.mkdir(parents=True);legacy.write_bytes(source.archive.read_bytes())
            part=legacy.with_name(legacy.name+".part");part.write_bytes(b"partial")
            original=Path.rename
            def rename(path,destination):
                if path==part:raise PermissionError("busy")
                return original(path,destination)
            with patch.object(Path,"rename",rename):
                with self.assertRaises(PermissionError):reserve_archive(target,source.payload,source.envelope,Event())
            archive=reserve_archive(target,source.payload,source.envelope,Event())
            self.assertEqual((archive.parent/"bundle.zip.part").read_bytes(),b"partial")
            self.assertEqual(len([p for p in (target/"archives").iterdir() if p.name!=source.payload["sha256"]]),1)

    def test_success_discards_short_zip_and_partial_but_keeps_external_archive(self):
        from modules.capture_services.openwrt.bundle_archive_paths import reserve_archive
        from modules.capture_services.openwrt.bundle_archive_cleanup import discard_prepared_archive
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=fixture(root/"source");target=root/"cache"
            archive=reserve_archive(target,source.payload,source.envelope,Event())
            archive.write_bytes(source.archive.read_bytes())
            (archive.parent/"bundle.zip.part").write_bytes(b"leftover")
            self.assertTrue(discard_prepared_archive(target,source.payload))
            self.assertFalse(archive.parent.exists());self.assertTrue(source.archive.exists())

    def test_new_revision_retires_old_short_cache_only_after_activation(self):
        from modules.capture_services.openwrt.bundle_archive_paths import reserve_archive
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/"cache"
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            key=Ed25519PrivateKey.generate()
            with patch("tests.test_common_offline_bundle.Ed25519PrivateKey.generate",return_value=key):
                old=fixture(root/"old");new=fixture(root/"new",apk=True)
            prepare(old,target)
            archive=reserve_archive(target,old.payload,old.envelope,Event());archive.write_bytes(old.archive.read_bytes())
            (archive.parent/"bundle.zip.part").write_bytes(b"partial")
            prepare(new,target)
            self.assertFalse(archive.parent.exists())
            self.assertTrue(old.archive.exists())

    def test_short_name_conflict_and_cancellation_preserve_user_data(self):
        from modules.capture_services.openwrt.bundle_archive_paths import reserve_archive
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=fixture(root/"source");target=root/"cache"
            with patch("modules.capture_services.openwrt.bundle_archive_paths.datetime") as clock:
                clock.now.return_value.strftime.return_value="20261028"
                occupied=target/"archives/20261028-01";occupied.mkdir(parents=True)
                (occupied/"bundle.zip").write_bytes(b"user")
                archive=reserve_archive(target,source.payload,source.envelope,Event())
                self.assertEqual(archive.parent.name,"20261028-02")
            self.assertEqual((occupied/"bundle.zip").read_bytes(),b"user")
            cancelled=Event();cancelled.set();before=set(target.rglob("*"))
            with self.assertRaises(InterruptedError):reserve_archive(target,source.payload,source.envelope,cancelled)
            self.assertEqual(set(target.rglob("*")),before)


class ShortPathTransferTests(unittest.TestCase):
    def test_legacy_partial_resumes_into_short_archive_then_prepares_short_objects(self):
        from unittest.mock import MagicMock
        from modules.capture_services.openwrt.common_bundle_download import download_bundle
        from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
        from modules.network_policy import NetworkChannel
        for kind in ("common","capture"):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);source=fixture(root/"source",kind=kind);target=root/"cache"
                data=source.archive.read_bytes()
                legacy=target/"archives"/source.payload["sha256"]/source.payload["filename"]
                legacy.parent.mkdir(parents=True)
                legacy.with_name(legacy.name+".part").write_bytes(data[:128])
                session=MagicMock();session.__enter__.return_value=session
                response=MagicMock();response.__enter__.return_value=response
                response.status_code=206
                response.headers={"Content-Range":f"bytes 128-{len(data)-1}/{len(data)}","Content-Length":str(len(data)-128)}
                response.iter_content.return_value=[data[128:]];session.get.return_value=response
                with patch("requests.Session",return_value=session),patch("modules.capture_services.openwrt.common_bundle_download.verify_bundle_manifest",side_effect=lambda raw,**kwargs:verify_bundle_manifest(raw,source.public,**kwargs)):
                    result=download_bundle(Event(),lambda _:None,lambda *_:None,root=target,kind=kind,manifest=source.envelope,channels=[NetworkChannel("direct","direct")])
                self.assertEqual(session.get.call_args.kwargs["headers"]["Range"],"bytes=128-")
                self.assertEqual(result["archive"],"")
                self.assertFalse(list((target/"archives").iterdir()))
                repo=CommonBundleRepository(target,source.public,kind);repo._load()
                for row in source.profile["packages"]:
                    self.assertEqual(len(repo.object_path(row).name),6)
                self.assertTrue(source.archive.is_file())

    def test_failed_layout_commit_leaves_legacy_repository_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=fixture(root/"source");target=root/"cache"
            legacy=legacy_copy(source,target)
            with patch("modules.capture_services.openwrt.bundle_metadata.os.replace",side_effect=PermissionError("busy")):
                with self.assertRaises(PermissionError):prepare(source,target)
            self.assertFalse((legacy/"object-layout.json").exists())
            self.assertFalse(list(legacy.glob(".metadata-*")))
            repo=CommonBundleRepository(target,source.public);repo._load()
            for row in source.profile["packages"]:
                self.assertEqual(repo.object_path(row).name,row["sha256"])


class ShortLayoutCleanupTests(unittest.TestCase):
    def test_layout_deletion_failure_keeps_catalog_and_cleanup_can_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=fixture(root/"old");new=fixture(root/"new",apk=True);target=root/"cache"
            version=Path(prepare(old,target)["content_path"])
            marker=version/"object-layout.json";original=Path.unlink
            def unlink(path,*args,**kwargs):
                if path==marker:raise PermissionError("busy")
                return original(path,*args,**kwargs)
            with patch.object(Path,"unlink",unlink):
                result=prepare(new,target)
            self.assertTrue(result["cleanup"]["incomplete"])
            self.assertTrue((version/"catalog.json").exists())
            prepare(new,target)
            self.assertFalse(version.exists())


class DeepInstallationPathTests(unittest.TestCase):
    def test_new_download_and_reads_do_not_touch_the_absent_long_legacy_path(self):
        from modules.capture_services.openwrt.common_bundle_download import download_bundle
        from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=fixture(root/"source")
            target=root/("deep-"+"x"*110)/"offline_packages/openwrt_common"
            def receive(url,path,*args):
                self.assertLess(len(str(path)),260)
                Path(path).write_bytes(source.archive.read_bytes())
            with patch("modules.capture_services.openwrt.common_bundle_download._receive",side_effect=receive),patch("modules.capture_services.openwrt.common_bundle_download.verify_bundle_manifest",side_effect=lambda raw,**kwargs:verify_bundle_manifest(raw,source.public,**kwargs)):
                download_bundle(Event(),lambda _:None,lambda *_:None,root=target,manifest=source.envelope)
            repo=CommonBundleRepository(target,source.public);repo._load()
            row=source.profile["packages"][0]
            previous=target/"versions"/source.payload["sha256"]/row["blob"]
            self.assertGreaterEqual(len(str(previous)),260)
            with patch("modules.capture_services.openwrt.bundle_object_paths.legacy_object_path",side_effect=AssertionError("short reads must not resolve legacy paths")):
                actual=repo.object_path(row)
            self.assertLess(len(str(actual)),260)
            self.assertEqual(actual.read_bytes(),source.blobs[row["sha256"]])
