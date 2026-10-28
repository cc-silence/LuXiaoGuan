"""Validated, immutable local repository for downloaded common dependencies."""
import hashlib
import json
import re
import shutil
from pathlib import Path
from .common_bundle_manifest import MAX_CATALOG, verify_bundle_manifest, sha256_file
from .bundle_specs import bundle_spec

_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*")
_SAFE_PACKAGE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+~\-]*\.(ipk|apk)")


def bundle_root(kind="common"):
    """\u72ec\u7acb\u8d44\u6e90\u653e\u5728\u8f6f\u4ef6\u76ee\u5f55\uff0c\u53ef\u968f\u8f6f\u4ef6\u4e00\u8d77\u62f7\u8d1d\u79bb\u7ebf\u4f7f\u7528\u3002"""
    from modules.config_manager import BASE_DIR
    return Path(BASE_DIR) / "offline_packages" / bundle_spec(kind).directory


def safe_path(root, relative):
    """\u62d2\u7edd\u8d8a\u754c\u548c\u91cd\u89e3\u6790\u8def\u5f84\uff0c\u4ec5\u8bbf\u95ee\u81ea\u6709\u8d44\u6e90\u5b50\u76ee\u5f55\u3002"""
    root = Path(root)
    path = root / relative
    if root.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("\u79bb\u7ebf\u5305\u8def\u5f84\u8d8a\u754c\u3002")
    for item in (path, *path.parents):
        if item == root.parent:
            break
        if item.exists() and (item.is_symlink() or getattr(item.stat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("\u79bb\u7ebf\u5305\u8def\u5f84\u4e0d\u5141\u8bb8\u4f7f\u7528\u94fe\u63a5\u3002")
    return path


def validate_catalog(data, kind="common"):
    """Convert malformed local catalog shapes into one recoverable data error."""
    try:
        return _validate_catalog(data, kind)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("\u79bb\u7ebf\u5305\u6e05\u5355\u683c\u5f0f\u65e0\u6548\u3002") from exc


def _validate_catalog(data, kind="common"):
    """\u6821\u9a8c\u9002\u914d\u7ec4\u5408\u548c\u6240\u6709\u5bf9\u8c61\u8fb9\u754c\uff0c\u8fd4\u56de\u53bb\u91cd\u5bf9\u8c61\u8868\u3002"""
    roots = bundle_spec(kind).roots
    if (not isinstance(data, dict) or data.get("schema_version") != 1
            or data.get("purpose") != bundle_spec(kind).catalog_purpose or set(data.get("roots", [])) != roots or data.get("complete") is not True
            or data.get("index_errors") or data.get("download_errors") or data.get("unsupported")):
        raise ValueError("\u79bb\u7ebf\u5305\u6e05\u5355\u4e0d\u5b8c\u6574\u3002")
    profiles = data.get("profiles")
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 5000:
        raise ValueError("\u79bb\u7ebf\u5305\u9002\u914d\u6e05\u5355\u65e0\u6548\u3002")
    keys, objects = set(), {}
    for p in profiles:
        key = (p["release"], p["target"], p["architecture"])
        if key in keys or not all(_SAFE_SEGMENT.fullmatch(v) for v in (key[0], key[2], *key[1].split("/"))):
            raise ValueError("\u79bb\u7ebf\u5305\u9002\u914d\u6807\u8bc6\u65e0\u6548\u6216\u91cd\u590d\u3002")
        keys.add(key)
        if p["package_manager"] not in ("opkg", "apk") or set(p.get("roots", [])) != roots:
            raise ValueError("\u79bb\u7ebf\u5305\u529f\u80fd\u8303\u56f4\u65e0\u6548\u3002")
        packages, indexes = p.get("packages"), p.get("indexes")
        if not isinstance(packages, list) or not len(roots) <= len(packages) <= 256:
            raise ValueError("\u79bb\u7ebf\u5305\u4f9d\u8d56\u6570\u91cf\u65e0\u6548\u3002")
        expected_feeds = {"target", "base", "packages", "luci"} if kind == "common" or p["package_manager"] == "apk" else set()
        if len({r["name"] for r in packages}) != len(packages) or not roots.issubset({r["name"] for r in packages}) or not isinstance(indexes, list) or len(indexes) != len(expected_feeds):
            raise ValueError("\u79bb\u7ebf\u5305\u7f3a\u5c11\u6839\u4f9d\u8d56\u6216\u8f6f\u4ef6\u6e90\u3002")
        if {r["feed"] for r in indexes} != expected_feeds:
            raise ValueError("\u79bb\u7ebf\u5305\u8f6f\u4ef6\u6e90\u6e05\u5355\u65e0\u6548\u3002")
        for row in packages:
            if (not _SAFE_NAME.fullmatch(row["name"]) or not _SAFE_PACKAGE.fullmatch(row["filename"])
                    or row["architecture"] not in (p["architecture"], "all", "noarch")
                    or not isinstance(row.get("depends"), list) or len(row["depends"]) > 64):
                raise ValueError("\u79bb\u7ebf\u5b89\u88c5\u5305\u8bb0\u5f55\u65e0\u6548\u3002")
            suffix = ".ipk" if p["package_manager"] == "opkg" else ".apk"
            if not row["filename"].endswith(suffix):
                raise ValueError("\u79bb\u7ebf\u5b89\u88c5\u5305\u683c\u5f0f\u4e0d\u5339\u914d\u3002")
        for row in packages + indexes:
            sha = row.get("sha256", "")
            if (not re.fullmatch("[a-f0-9]{64}", sha) or row.get("blob") != "objects/" + sha
                    or type(row.get("size")) is not int or not 0 < row["size"] <= 32 * 1024 * 1024):
                raise ValueError("\u79bb\u7ebf\u5bf9\u8c61\u6307\u7eb9\u6216\u5bb9\u91cf\u65e0\u6548\u3002")
            previous = objects.setdefault(sha, row)
            if previous["size"] != row["size"]:
                raise ValueError("\u5171\u4eab\u79bb\u7ebf\u5bf9\u8c61\u8bb0\u5f55\u4e0d\u4e00\u81f4\u3002")
    return objects


class CommonBundleRepository:
    """\u5b89\u88c5\u65f6\u624d\u8bfb\u53d6\u7b7e\u540d\u6e05\u5355\uff0c\u4e0d\u5728\u6253\u5f00\u9875\u9762\u65f6\u626b\u63cf\u8d44\u6e90\u3002"""
    def __init__(self, root=None, public_key=None, kind="common"):
        self.kind = bundle_spec(kind).kind
        self.root = Path(root) if root is not None else bundle_root(kind)
        self.public_key = public_key
        self.catalog = None
        self.content_root = None
        self.revision = None
        self.object_names = {}

    def _load(self):
        receipt = safe_path(self.root, "current.json")
        if not receipt.is_file():
            return False
        if receipt.stat().st_size > 65536:
            raise ValueError("\u672c\u5730\u79bb\u7ebf\u5305\u7b7e\u540d\u6e05\u5355\u8fc7\u5927\u3002")
        raw = receipt.read_bytes()
        payload = verify_bundle_manifest(raw, kind=self.kind, **({"public_key": self.public_key} if self.public_key else {}))
        from .bundle_layout import version_path
        self.content_root = version_path(self.root, payload["sha256"], self.kind, payload=payload)
        if self.content_root is None:
            raise ValueError("\u672c\u5730\u79bb\u7ebf\u8d44\u6e90\u7f3a\u5931\uff0c\u8bf7\u91cd\u65b0\u4e0b\u8f7d\u3002")
        self.revision = payload["sha256"]
        path = safe_path(self.content_root, "catalog.json")
        if not path.is_file() or path.stat().st_size != payload["catalog_size"]:
            raise ValueError("\u79bb\u7ebf\u5305\u9002\u914d\u6e05\u5355\u7f3a\u5931\uff0c\u8bf7\u91cd\u65b0\u4e0b\u8f7d\u3002")
        catalog = path.read_bytes()
        if hashlib.sha256(catalog).hexdigest() != payload["catalog_sha256"]:
            raise ValueError("\u79bb\u7ebf\u5305\u9002\u914d\u6e05\u5355\u6821\u9a8c\u5931\u8d25\uff0c\u8bf7\u91cd\u65b0\u4e0b\u8f7d\u3002")
        self.catalog = json.loads(catalog.decode("utf-8"))
        from .bundle_object_paths import object_names, numbered_layout
        self.object_names = object_names(validate_catalog(self.catalog, self.kind))
        self.numbered_objects = numbered_layout(self.content_root)
        return True

    def profile(self, device):
        """\u53ea\u63a5\u53d7\u5b98\u65b9\u56fa\u4ef6\u7684\u51c6\u786e\u7248\u672c\u3001\u76ee\u6807\u3001\u67b6\u6784\u4e0e\u5305\u7ba1\u7406\u5668\u5339\u914d\u3002"""
        if getattr(device, "distribution", "") != "OpenWrt":
            return None
        if self.catalog is None and not self._load():
            return None
        for p in self.catalog["profiles"]:
            if (p["release"], p["target"], p["architecture"], p["package_manager"]) == (
                    getattr(device, "version", ""), getattr(device, "target", ""),
                    getattr(device, "package_architecture", ""), getattr(device, "package_manager", "")):
                return p
        return None

    def object_source(self, row):
        from .bundle_object_paths import object_source
        return object_source(self.content_root, row["sha256"], self.object_names, self.numbered_objects)

    def object_path(self, row, cancel=None):
        path = self.object_source(row)
        if (not path.is_file() or path.stat().st_size != row["size"]
                or sha256_file(path, cancel) != row["sha256"]):
            raise ValueError("\u79bb\u7ebf\u5305\u5bf9\u8c61\u7f3a\u5931\u6216\u6821\u9a8c\u5931\u8d25\uff1a" + row["filename"])
        return path

    def materialize(self, row, directory, cancel=None):
        """\u6309\u771f\u5b9e\u6587\u4ef6\u540d\u751f\u6210\u4efb\u52a1\u4e34\u65f6\u5305\uff0c\u4e0d\u6539\u5199\u53ea\u8bfb\u5171\u4eab\u5bf9\u8c61\u3002"""
        source = self.object_path(row, cancel)
        target = Path(directory) / row["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target

    def ipk_selection(self, profile, wanted, installed, version_check=None):
        """\u5229\u7528\u672c\u5730\u5143\u6570\u636e\u8ba1\u7b97\u7f3a\u5931\u4f9d\u8d56\uff0c\u4e0d\u8054\u7f51\u4e5f\u4e0d\u66ff\u6362\u5df2\u88c5\u7ec4\u4ef6\u3002"""
        from .signal_package_feed import FeedPackage
        from .dependency_resolution import resolve_packages
        records, rows = {}, {}
        for r in profile["packages"]:
            record = FeedPackage(r["name"], r["version"], r["filename"], r["sha256"], r["size"],
                                 r["url"], tuple(r["depends"]), tuple(r.get("provides", [])))
            records[r["name"]] = record
            rows[r["name"]] = r
        for r in profile["packages"]:
            for alias in r.get("provides", []):
                records.setdefault(re.split(r"[ (]", alias.strip())[0], records[r["name"]])
        return tuple(resolve_packages(records, wanted, installed, version_check))

    def ipk_paths(self, profile, wanted, installed, directory, version_check=None, cancel=None):
        rows = {row["name"]: row for row in profile["packages"]}
        selected = self.ipk_selection(profile, wanted, installed, version_check)
        return tuple(self.materialize(rows[p.name], directory, cancel) for p in selected)
