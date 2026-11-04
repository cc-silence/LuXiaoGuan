"""Signed Linux inventory keeps permissions and links without weakening Windows admission."""
import base64
import copy
from dataclasses import replace
import hashlib
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from modules.app_update_services.catalog import parse_catalog
from modules.app_update_services.linux_catalog import managed_linux_path
from modules.app_update_services.manifest import canonical, verify_manifest
from scripts.linux_update_catalog import artifacts
from modules.platform_services.linux_recovery_launcher import LAUNCHER, SCRIPT

KEY = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def signed(payload):
    return canonical(dict(payload=payload, signature=base64.b64encode(KEY.sign(canonical(payload))).decode()))


def write_member(archive, path, data, mode, system=3):
    info = zipfile.ZipInfo("LuXiaoGuan/" + path); info.create_system = system
    info.external_attr = mode << 16; info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


class LinuxUpdateCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "package.zip"
        with zipfile.ZipFile(self.archive, "w") as archive:
            write_member(archive, LAUNCHER, SCRIPT, stat.S_IFREG | 0o755)
            write_member(archive, "LuXiaoGuan", b"synthetic executable bytes", stat.S_IFREG | 0o755)
            write_member(archive, "resources/libterminal.so.1", b"synthetic library", stat.S_IFREG | 0o755)
            write_member(archive, "resources/libterminal.so", b"libterminal.so.1", stat.S_IFLNK | 0o777)
            write_member(archive, "images/A.png", b"A", stat.S_IFREG | 0o644)
            write_member(archive, "images/a.png", b"a", stat.S_IFREG | 0o644)
        self.payload = dict(schema=1, product="RouterManager", platform="linux-x64", channel="alpha",
            version="1.0.21_alpha", notes="synthetic metadata only", published_at="2026-10-27T12:00:00+08:00",
            url="https://example.invalid/files/linux-x64/package.zip", size=self.archive.stat().st_size,
            sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.catalog, self.objects, fields = artifacts(self.archive, self.root / "objects", self.payload)
        self.payload.update(fields)
        self.release = verify_manifest(signed(self.payload), PUBLIC, expected_platform="linux-x64")

    def parse(self, catalog):
        data = canonical(catalog)
        release = replace(self.release, catalog_sha256=hashlib.sha256(data).hexdigest(), catalog_size=len(data))
        return parse_catalog(data, release)

    def test_signed_round_trip_preserves_mode_case_and_internal_links(self):
        parsed = parse_catalog(canonical(self.catalog), self.release)
        rows = {row["path"]:row for row in parsed["files"]}
        self.assertEqual(parsed["schema"], 2)
        self.assertEqual(rows["LuXiaoGuan"]["mode"], 0o755)
        self.assertIn("images/A.png", rows); self.assertIn("images/a.png", rows)
        self.assertEqual(rows["resources/libterminal.so"]["target"], "libterminal.so.1")
        self.assertNotIn("blob_sha256", rows["resources/libterminal.so"])

    def test_mode_tampering_changes_signed_catalog_fingerprint(self):
        changed = copy.deepcopy(self.catalog); changed["files"][0]["mode"] = 0o644
        with self.assertRaises(ValueError): parse_catalog(canonical(changed), self.release)

    def test_unsafe_modes_and_non_executable_primary_are_rejected(self):
        for mode in (0o777, 0o4755, True, 0o644):
            changed = copy.deepcopy(self.catalog)
            next(row for row in changed["files"] if row["path"] == "LuXiaoGuan")["mode"] = mode
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.parse(changed)

    def test_escaping_missing_and_cyclic_links_are_rejected(self):
        for target in ("/etc/passwd", "../../../config/settings.ini", "missing.so", "libterminal.so"):
            changed = copy.deepcopy(self.catalog)
            next(row for row in changed["files"] if row["kind"] == "symlink")["target"] = target
            with self.subTest(target=target), self.assertRaises(ValueError): self.parse(changed)

    def test_file_or_link_cannot_be_parent_of_other_entries(self):
        changed = copy.deepcopy(self.catalog)
        extra = copy.deepcopy(next(row for row in changed["files"] if row["path"] == "images/a.png"))
        extra["path"] = "resources/libterminal.so/child"; changed["files"].append(extra)
        with self.assertRaises(ValueError): self.parse(changed)

    def test_windows_client_rejects_linux_catalog_and_manifest(self):
        with self.assertRaises(ValueError): verify_manifest(signed(self.payload), PUBLIC, expected_platform="win-x64")
        with self.assertRaises(ValueError): parse_catalog(canonical(self.catalog), replace(self.release, platform="win-x64"))

    def test_user_configuration_foreign_binaries_and_traversal_are_protected(self):
        for path in ("config/settings.ini", "../outside", "resources/../../config/settings.ini", "LuXiaoGuan.exe", "bin/tool.dll", "/absolute", "resources/app-update-baseline.json"):
            with self.subTest(path=path), self.assertRaises(ValueError): managed_linux_path(path)

    def test_linux_migrations_stay_closed_until_native_backend_exists(self):
        changed = copy.deepcopy(self.catalog); changed["migrations"] = [{"file":"settings.ini"}]
        with self.assertRaises(ValueError): self.parse(changed)

    def test_zip_foreign_metadata_special_files_and_directory_traversal_are_rejected(self):
        for name, mode, system in (("resources/foreign", stat.S_IFREG|0o644, 0),
                                   ("resources/pipe", stat.S_IFIFO|0o644, 3),
                                   ("../outside/", stat.S_IFDIR|0o755, 3)):
            with zipfile.ZipFile(self.archive, "a") as archive:
                write_member(archive, name, b"", mode, system)
            with self.subTest(name=name), self.assertRaises(ValueError):
                artifacts(self.archive, self.root / "bad-objects", self.payload)
            # Rebuild the original fixture for the next format failure.
            self.setUp()

    def test_missing_or_modified_stable_launcher_cannot_be_published(self):
        with zipfile.ZipFile(self.archive) as source:
            members=[(info,source.read(info)) for info in source.infolist()]
        for mode in ('missing','modified'):
            archive=self.root/(mode+'.zip')
            with zipfile.ZipFile(archive,'w') as zipped:
                for info,data in members:
                    if info.filename=='LuXiaoGuan/'+LAUNCHER:
                        if mode=='missing':continue
                        data+=b'\n'
                    zipped.writestr(info,data)
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                artifacts(archive,self.root/mode,self.payload)
