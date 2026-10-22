"""Release records remain usable while the delivery directory stays clean."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import release_metadata as metadata
from scripts.app_update_release import write_release_descriptor


class ReleaseMetadataTests(unittest.TestCase):
    def test_descriptor_uses_internal_records_with_legacy_read_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=root/"LuXiaoGuan_v1.0.2_alpha_fixture.zip"
            archive.write_bytes(b"verified archive fixture")
            legacy=archive.with_suffix(".release.json")
            legacy.write_text("legacy",encoding="utf-8")
            with patch.object(metadata,"ROOT",root):
                self.assertEqual(metadata.metadata_path(archive,".release.json",existing=True),legacy)
                path=write_release_descriptor(archive,"1.0.2_alpha")
                self.assertTrue(path.is_relative_to(root/"build/release-metadata"))
                self.assertEqual(metadata.metadata_path(archive,".release.json",existing=True),path)
                self.assertEqual(json.loads(path.read_text())["archive"],archive.name)

    def test_only_known_sidecars_and_verified_duplicate_mirrors_are_cleaned(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);dist=root/"dist";dist.mkdir()
            docs=root/"docs/offline-packages";docs.mkdir(parents=True)
            archive=dist/"LuXiaoGuan_v1.0.2_alpha_fixture.zip";archive.write_bytes(b"archive")
            sidecar=archive.with_suffix(".report.json");sidecar.write_bytes(b"report")
            (dist/"user.json").write_bytes(b"keep")
            (docs/"support.json").write_bytes(b"support")
            (dist/"offline_bundle_support.json").write_bytes(b"support")
            (dist/"openwrt_offline_delivery.json").write_bytes(b"different history")
            result=metadata.clean_delivery_metadata(root)
            self.assertEqual(archive.read_bytes(),b"archive")
            self.assertEqual((dist/"user.json").read_bytes(),b"keep")
            self.assertEqual((docs/"support.json").read_bytes(),b"support")
            self.assertEqual(result["removed_duplicates"],["offline_bundle_support.json"])
            self.assertEqual((root/"build/release-metadata"/archive.stem/sidecar.name).read_bytes(),b"report")
            self.assertEqual((root/"build/release-metadata/offline-inventory/openwrt_offline_delivery.json").read_bytes(),b"different history")
            self.assertEqual(metadata.clean_delivery_metadata(root),{"archived":[],"removed_duplicates":[]})

    def test_conflicting_record_is_never_overwritten_or_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);dist=root/"dist";dist.mkdir()
            name="LuXiaoGuan_v1.0.2.report.json"
            source=dist/name;source.write_bytes(b"new")
            target=root/"build/release-metadata/LuXiaoGuan_v1.0.2"/name
            target.parent.mkdir(parents=True);target.write_bytes(b"old")
            with self.assertRaises(ValueError):metadata.clean_delivery_metadata(root)
            self.assertEqual(source.read_bytes(),b"new")
            self.assertEqual(target.read_bytes(),b"old")


if __name__=="__main__":unittest.main()
