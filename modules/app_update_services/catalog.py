"""\u7b7e\u540d\u6e05\u5355\u5f15\u7528\u7684\u6587\u4ef6\u76ee\u5f55\u4e0e\u8de8\u7248\u5386\u53f2\u5408\u7ea6\u3002"""
import hashlib
import json
import re
from pathlib import PurePosixPath
from .manifest import version_key, https_url
from modules.release_identity import APPLICATION_EXECUTABLES

MAX_CATALOG_BYTES = 8 * 1024 * 1024
BASELINE = "resources/app-update-baseline.json"
ROOTS = {"resources", "bin", "themes", "user_scripts", "images"}

def managed_path(value):
    """\u53ea\u5141\u8bb8\u5b98\u65b9\u7a0b\u5e8f\u8d44\u6e90\uff0c\u4e25\u683c\u6392\u9664\u7528\u6237\u914d\u7f6e\u548c\u6570\u636e\u3002"""
    if not isinstance(value, str) or not value or len(value) > 240 or "\\" in value:
        raise ValueError("Invalid managed path")
    parts = value.split("/")
    if any(p in {"", ".", ".."} or p[-1:] in {".", " "} or any(ord(c)<32 or c in ':*?"<>|' for c in p)
           or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in parts):
        raise ValueError("Unsafe managed path: " + value)
    if value.casefold() == BASELINE or any(p.casefold() == "__pycache__" for p in parts) or value.lower().endswith((".pyc", ".tmp")):
        raise ValueError("Reserved managed path")
    if value not in APPLICATION_EXECUTABLES and (len(parts)<2 or parts[0] not in ROOTS):
        raise ValueError("Protected user path: " + value)
    return value

def digest(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
        raise ValueError("Invalid file fingerprint")
    return value

def parse_catalog(data, release):
    """\u5148\u6821\u9a8c\u7b7e\u540d\u5f15\u7528\u7684\u5185\u5bb9\u6307\u7eb9\uff0c\u518d\u6821\u9a8c\u8def\u5f84\u4e0e\u5386\u53f2\u3002"""
    if len(data) > MAX_CATALOG_BYTES or len(data) != release.catalog_size or hashlib.sha256(data).hexdigest() != release.catalog_sha256:
        raise ValueError("Catalog fingerprint mismatch")
    catalog = json.loads(data)
    if not isinstance(catalog, dict) or catalog.get("schema") != 1 or catalog.get("version") != release.version or catalog.get("resource_revision",0) != release.resource_revision:
        raise ValueError("Invalid target catalog")
    entries = catalog.get("files")
    if not isinstance(entries,list) or not 1 <= len(entries) <= 12000:
        raise ValueError("Invalid file inventory")
    seen=set(); total=0
    for entry in entries:
        path=managed_path(entry["path"])
        if path.casefold() in seen: raise ValueError("Duplicate file path")
        seen.add(path.casefold()); digest(entry["sha256"]); digest(entry["blob_sha256"])
        for name in ("size","blob_size"):
            if type(entry[name]) is not int or not 0 <= entry[name] <= 2*1024**3:
                raise ValueError("Invalid file size")
        total+=entry["size"]
    if total>4*1024**3: raise ValueError("Target exceeds installation size limit")
    retired=catalog.get("retired",[])
    if not isinstance(retired,list) or len(retired)>12000: raise ValueError("Invalid removed inventory")
    for path in retired:
        if managed_path(path).casefold() in seen: raise ValueError("Active file is marked removed")
    history=catalog.get("history")
    if not isinstance(history,list) or not 1<=len(history)<=512: raise ValueError("Invalid history")
    versions=set(); previous=None
    for item in history:
        revision=item.get("resource_revision",0)
        if type(revision) is not int or not 0<=revision<=1000000000: raise ValueError("Invalid historical revision")
        key=(version_key(item["version"]),revision)
        if key in versions or (previous is not None and key<=previous): raise ValueError("Unordered history")
        if not isinstance(item.get("notes"),str) or len(item["notes"])>16000: raise ValueError("Invalid historical notes")
        if not isinstance(item.get("published_at"),str) or len(item["published_at"])>40: raise ValueError("Invalid date")
        versions.add(key); previous=key
    if history[-1]["version"] != release.version or history[-1].get("resource_revision",0)!=release.resource_revision: raise ValueError("History target mismatch")
    validate_migrations(catalog.get("migrations",[]))
    if any(version_key(m["version"])>version_key(release.version) for m in catalog.get("migrations",[])):
        raise ValueError("Migration exceeds target version")
    return catalog

def validate_migrations(migrations):
    """\u914d\u7f6e\u8fc1\u79fb\u53ea\u652f\u6301\u58f0\u660e\u5f0f\u8865\u7f3a\u7701\u503c\uff0c\u4e0d\u6267\u884c\u8fdc\u7aef\u4ee3\u7801\u3002"""
    if not isinstance(migrations,list) or len(migrations)>1024: raise ValueError("Invalid migrations")
    previous=None
    for migration in migrations:
        key=version_key(migration["version"])
        if previous is not None and key<previous: raise ValueError("Unordered migrations")
        previous=key
        if migration.get("file") not in {"settings.ini","download.ini"}: raise ValueError("Protected configuration")
        defaults=migration.get("defaults")
        if not isinstance(defaults,dict) or len(defaults)>100: raise ValueError("Invalid defaults")
        for section,values in defaults.items():
            if not isinstance(section,str) or not section or len(section)>100 or not isinstance(values,dict) or len(values)>200:
                raise ValueError("Invalid configuration section")
            if any(not isinstance(k,str) or not isinstance(v,str) or len(k)>100 or len(v)>4096 for k,v in values.items()):
                raise ValueError("Invalid configuration values")

def history_text(catalog, current, all_versions=False, current_revision=0):
    """\u9ed8\u8ba4\u663e\u793a\u5f53\u524d\u7248\u672c\u4e4b\u540e\u7684\u5168\u90e8\u53d8\u5316\uff0c\u4e5f\u53ef\u67e5\u770b\u6240\u6709\u5386\u53f2\u3002"""
    rows=[item for item in catalog["history"] if all_versions or (version_key(item["version"]),item.get("resource_revision",0))>(version_key(current),current_revision)]
    return "\n\n".join(item["version"]+(" (\u8d44\u6e90\u66f4\u65b0 "+str(item["resource_revision"])+")" if item.get("resource_revision") else "")+"  "+item["published_at"][:10]+"\n"+item["notes"] for item in reversed(rows))
