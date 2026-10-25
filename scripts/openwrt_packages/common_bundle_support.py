"""Build-time HTTP cache and dependency-closure metadata for common tools."""
import gzip
import hashlib
import json
import re
import threading
from pathlib import Path
from .apk_index_reader import read_apk_index
from .package_index import parse_packages_index

OFFICIAL = "https://downloads.openwrt.org/releases"
TARGETS = (
    "mediatek/filogic", "mediatek/mt7622", "ramips/mt7621",
    "qualcommax/ipq807x", "ipq40xx/generic", "ipq806x/generic",
    "ath79/generic", "ath79/nand", "bcm53xx/generic",
    "mvebu/cortexa9", "mvebu/cortexa53", "bcm27xx/bcm2711",
    "sunxi/cortexa53", "x86/64",
)
from modules.capture_services.openwrt.bundle_specs import COMMON_ROOTS
ROOTS = tuple(sorted(COMMON_ROOTS))
# Firmware-owned runtime components are checked on the device, never replaced.
from modules.capture_services.openwrt.bundle_dependencies import FIRMWARE_OWNED, dependency_name, MissingPackagesError, closure
SAFE_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+~%-]*")
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*")


class HttpCache:
    """\u6709\u8d85\u65f6\u548c\u5bb9\u91cf\u4e0a\u9650\u7684\u53ef\u6062\u590d\u4e0b\u8f7d\uff0c\u4ec5\u5199\u81ea\u6709\u7f13\u5b58\u3002"""
    def __init__(self, root, stop):
        self.root, self.stop = Path(root), stop
        self.root.mkdir(parents=True, exist_ok=True)
        self.locks = [threading.Lock() for _ in range(64)]

    def get(self, url, limit=16 * 1024 * 1024):
        if not url.startswith(("https://downloads.openwrt.org/", "https://archive.openwrt.org/",
                               "https://downloads.cdn.openwrt.org/")):
            raise ValueError("Unapproved download source")
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.root / key
        with self.locks[int(key[:2], 16) % len(self.locks)]:
            if self.stop.is_set():
                raise InterruptedError("Collection cancelled or deadline reached")
            if path.is_file() and not getattr(self, "refresh", False):
                raw = path.read_bytes()
                if len(raw) > limit:
                    raise ValueError("Cached response exceeds limit")
                return raw
            absent = path.with_suffix(".404")
            if absent.exists() and not getattr(self, "refresh", False):
                raise FileNotFoundError(url)
            from .official_download import fetch_official
            try:
                raw = fetch_official(url, limit, self.stop)
            except FileNotFoundError:
                absent.write_text(url, encoding="utf-8")
                raise
            temp = path.with_suffix(".part")
            temp.write_bytes(raw)
            temp.replace(path)
            return raw

    def official(self, url, limit=16 * 1024 * 1024):
        try:
            return self.get(url, limit)
        except (FileNotFoundError, RuntimeError) as first:
            alternate = url.replace("https://downloads.openwrt.org/", "https://archive.openwrt.org/")
            if alternate == url:
                raise
            try:
                return self.get(alternate, limit)
            except FileNotFoundError:
                if isinstance(first, FileNotFoundError):
                    raise
                raise first


def checked_filename(value):
    if not SAFE_FILE.fullmatch(value) or value in (".", ".."):
        raise ValueError("Unsafe package filename: " + value)
    return value


def parse_feed(raw, feed, base, apk, sha_map=None):
    """\u7edf\u4e00\u89e3\u6790\u4e24\u79cd\u5b98\u65b9\u7d22\u5f15\uff0c\u4fdd\u7559\u539f\u59cb\u7248\u672c\u7ea6\u675f\u3002"""
    if apk:
        rows = read_apk_index(raw)
        for row in rows:
            row["sha256"] = (sha_map or {}).get(feed + "/" + row["filename"], "")
    else:
        rows = []
        for p in parse_packages_index(gzip.decompress(raw).decode("utf-8"), feed, base):
            rows.append(dict(name=p.name, version=p.version, architecture=p.architecture,
                             filename=p.filename, size=p.size, sha256=p.sha256,
                             depends=list(p.depends), provides=list(p.provides), install_if=[]))
    for row in rows:
        checked_filename(row["filename"])
        if not SAFE_NAME.fullmatch(row["name"]):
            raise ValueError("Unsafe package name")
        row["feed"], row["url"] = feed, base.rstrip("/") + "/" + row["filename"]
    return rows



def hash_table(raw):
    result = {}
    for line in raw.decode("utf-8").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and re.fullmatch("[0-9a-fA-F]{64}", parts[0]):
            result[parts[1].lstrip("*")] = parts[0].lower()
    if not result:
        raise ValueError("Empty official SHA256 table")
    return result


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
