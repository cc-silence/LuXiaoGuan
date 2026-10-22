"""Persist completed profiles so additions do not re-resolve old systems."""
import hashlib
import json
import re
import zipfile
from pathlib import Path
from .common_bundle_support import ROOTS, json_bytes


class ProfileLedger:
    """\u4fdd\u5b58\u5df2\u9a8c\u8bc1\u7ec4\u5408\u7684\u4f9d\u8d56\u7ed3\u679c\uff0c\u65b0\u7248\u672c\u53ea\u5904\u7406\u65b0\u7ec4\u5408\u3002"""
    def __init__(self, root, roots=ROOTS, kind="common"):
        self.roots, self.kind = frozenset(roots), kind
        self.root = Path(root)
        self.folder = self.root / "completed_profiles"

    def path(self, release, target):
        parts = (release, *target.split("/"))
        if not all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", p) for p in parts):
            raise ValueError("Unsafe ledger identity")
        return self.folder.joinpath(*parts).with_suffix(".json")

    def load(self, release, target):
        path = self.path(release, target)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        p = data.get("profile", {})
        if data.get("schema") != 1 or hashlib.sha256(json_bytes(p)).hexdigest() != data.get("metadata_sha256"):
            raise ValueError("Completed profile receipt is damaged: " + release + "/" + target)
        if (p.get("release") != release or p.get("target_family", p.get("target")) != target
                or p.get("status") != "index-verified" or set(p.get("roots", [])) != self.roots):
            raise ValueError("Completed profile receipt no longer matches current roots")
        return p

    def save(self, profile):
        if profile.get("status") != "index-verified":
            return
        path = self.path(profile["release"], profile.get("target_family", profile["target"]))
        digest = hashlib.sha256(json_bytes(profile)).hexdigest()
        if path.is_file():
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("metadata_sha256") == digest:
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".pending")
        tmp.write_bytes(json_bytes(dict(schema=1, metadata_sha256=digest, profile=profile)))
        tmp.replace(path)

    def seed_inventory(self, inventory):
        """\u4ec5\u4ece\u5b8c\u6574\u9a8c\u8bc1\u7ed3\u679c\u521d\u59cb\u5316\u5b58\u6863\uff0c\u4e0d\u91cd\u7b97\u4f9d\u8d56\u3002"""
        if inventory.get("errors"):
            raise ValueError("Cannot archive an incomplete inventory")
        for profile in inventory["profiles"]:
            self.save(profile)

    def seed_archive(self, archive, expected_sha256):
        """\u6362\u5de5\u4f5c\u76ee\u5f55\u65f6\u53ef\u4ece\u5df2\u77e5\u6307\u7eb9\u7684\u79bb\u7ebf\u5305\u6062\u590d\u5b58\u6863\uff0c\u4e0d\u8054\u7f51\u91cd\u7b97\u3002"""
        from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
        from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
        archive = Path(archive)
        if not re.fullmatch("[a-f0-9]{64}", expected_sha256) or sha256_file(archive) != expected_sha256:
            raise ValueError("Seed archive differs from approved fingerprint")
        with zipfile.ZipFile(archive) as z:
            data = json.loads(z.read("catalog.json"))
            objects = validate_catalog(data, self.kind)
            if set(z.namelist()) != {"catalog.json"} | {"objects/"+s for s in objects}:
                raise ValueError("Seed archive inventory mismatch")
            for sha, row in objects.items():
                path = self.root / "bundle" / "objects" / sha
                if path.is_file() and path.stat().st_size == row["size"] and sha256_file(path) == sha:
                    continue
                raw = z.read("objects/"+sha)
                if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != sha:
                    raise ValueError("Seed object checksum mismatch")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            inventory = dict(schema_version=1, purpose=data["purpose"], releases=data["releases"],
                             targets=data["targets"], roots=data["roots"], profiles=data["profiles"], errors=[])
            self.seed_inventory(inventory)
            (self.root/"inventory.json").write_bytes(json_bytes(inventory))
            return inventory
