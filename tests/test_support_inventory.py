"""Source support inventory follows real archives and retains unknown model evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from tests.test_common_offline_bundle import fixture
from scripts.openwrt_packages.support_inventory import write_inventory


class SupportInventoryTests(unittest.TestCase):
    def test_delivery_regenerates_exact_profiles_and_keeps_unknown_models(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); docs=root/"docs";docs.mkdir();f=fixture(root/"bundle")
            f.archive.with_suffix(".report.json").write_text(json.dumps(dict(complete=True,zip_sha256=f.payload["sha256"],zip_bytes=f.payload["size"])),encoding="utf-8")
            initial=dict(product="RouterManager",packages=[],router_model_verification=[dict(model="JCG Q30",status="actual-firmware-unconfirmed"),dict(model="old platform",status="package-platform-covered",targets=["removed/target"],architecture="removed")])
            (docs/"support.json").write_text(json.dumps(initial),encoding="utf-8")
            path=write_inventory("common",f.archive,docs=docs);data=json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["packages"][0]["exact_profiles"],[{k:f.profile[k] for k in ["release","target","architecture","package_manager"]}])
            self.assertEqual(data["router_model_verification"][0]["status"],"actual-firmware-unconfirmed")
            self.assertEqual(data["router_model_verification"][1]["status"],"platform-not-covered")
            self.assertIn("24.10.8",(docs/"support.txt").read_text(encoding="utf-8"))
            receipt=dict(kind="common",sha256=f.payload["sha256"],status="published-and-client-verified",manifest_url="https://example.com/manifest",archive_url="https://example.com/archive")
            write_inventory("common",f.archive,publication=receipt,docs=docs)
            write_inventory("common",f.archive,docs=docs)
            self.assertEqual(json.loads(path.read_text())["packages"][0]["manifest_url"],receipt["manifest_url"])

    def test_changed_archive_cannot_update_support_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);f=fixture(root/"bundle")
            f.archive.with_suffix(".report.json").write_text(json.dumps(dict(complete=True,zip_sha256="0"*64,zip_bytes=f.payload["size"])),encoding="utf-8")
            with self.assertRaises(ValueError):write_inventory("common",f.archive,docs=root/"docs")
            self.assertFalse((root/"docs").exists())


if __name__=="__main__":unittest.main()
