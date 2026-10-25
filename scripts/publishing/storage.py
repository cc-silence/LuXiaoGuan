"""Bound release storage before upload; only old, unreferenced full ZIPs are eligible."""
from datetime import datetime, timezone
from functools import wraps
import os
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from modules.app_update_services.manifest import version_key, verify_manifest
from modules.app_update_services.trust import PUBLIC_KEY
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
from .cloudflare import ROOT
from .storage_api import StorageAPI
from modules.app_update_services.release_targets import APPLICATION_CHANNELS, release_channel

class StorageQuotaError(ValueError):
    """Safe ordinary reclamation cannot fit the proposed immutable upload."""


LIMIT = 1_000_000_000
_CHANNELS = {**{key: "app" for key in APPLICATION_CHANNELS},
             "channel/openwrt-common.json": "common", "channel/openwrt-capture.json": "capture"}
_ARCHIVE = re.compile(r"files/(?:(win-x64|linux-x64)/)?([a-f0-9]{64})/(?:LuXiaoGuan|RouterManager)_v(.+)_[0-9]{8}_[0-9]{6}\.zip")


def publication_lock(function):
    """Serialize cleanup plus uploads and channel switching on this publisher host."""
    @wraps(function)
    def locked(*args, **kwargs):
        path = ROOT / "build/publishing/publication.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            if handle.tell() == 0: handle.write(b"0"); handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("Another publisher is running; publication stopped") from None
            try:
                return function(*args, **kwargs)
            finally:
                handle.seek(0)
                if os.name == "nt": msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(handle, fcntl.LOCK_UN)
    return locked


def archive_identity(key):
    match = _ARCHIVE.fullmatch(key)
    if match:
        try:
            version = version_key(match[3])
            release_channel(match[3])
            return (match[1] or "win-x64", version)
        except ValueError:
            pass
    return None


def archive_version(key):
    identity = archive_identity(key)
    return identity[1] if identity else None


def protected_releases(api, inventory):
    """Keep exactly the signed current ZIP in each platform/channel slot."""
    protected, raw_channels, ceilings = set(), {}, {}
    for key in inventory:
        if key.startswith("channel/") and key not in _CHANNELS:
            raise ValueError("Unknown release channel; refusing automated cleanup")
    for key, kind in _CHANNELS.items():
        if key not in inventory:
            continue
        raw = api.read(key)
        raw_channels[key] = raw
        if kind == "app":
            platform, channel = APPLICATION_CHANNELS[key]
            try:
                marker = json.loads(raw)
            except (ValueError, UnicodeError):
                marker = {}
            if isinstance(marker, dict) and marker.get("state") == "publishing":
                if (channel != "alpha" or marker.get("product") != "RouterManager"
                        or marker.get("schema") != 1 or marker.get("platform") != platform
                        or marker.get("channel") != "alpha"
                        or not re.fullmatch("[a-f0-9]{64}", marker.get("sha256", ""))
                        or release_channel(marker.get("version")) != "alpha"):
                    raise ValueError("Invalid alpha maintenance slot; cleanup stopped")
                # No deletion ceiling for this alpha: preserve all its archives,
                # including an upload not yet switched into the signed channel.
                continue
            release = verify_manifest(raw, PUBLIC_KEY, expected_platform=platform)
            if release_channel(release.version) != channel:
                raise ValueError("Application channel/version mismatch; cleanup stopped")
            archive = urlsplit(release.url).path.lstrip("/")
            identity = archive_identity(archive)
            if identity != (platform, version_key(release.version)):
                raise ValueError("Current archive platform/version mismatch; cleanup stopped")
            if (archive not in inventory or inventory[archive]["size"] != release.size
                    or archive.split("/")[-2] != release.sha256):
                raise ValueError("Current application archive is missing or differs from its signature")
            protected.add(archive)
            ceilings[(platform, channel)] = version_key(release.version)
        else:
            payload = verify_bundle_manifest(raw, kind=kind)
            protected.add(urlsplit(payload["url"]).path.lstrip("/"))
    eligible = []
    for key in inventory:
        identity = archive_identity(key)
        if identity is None or key in protected:
            continue
        platform, version = identity
        channel = "stable" if version[1] == 4 else "alpha"
        ceiling = ceilings.get((platform, channel))
        if ceiling is not None and version <= ceiling:
            eligible.append(key)
    eligible.sort(key=lambda key: (archive_identity(key), key))
    from .retention import offline_candidates
    offline_protected, offline_old = offline_candidates(api, inventory, raw_channels)
    protected.update(offline_protected)
    eligible.extend(offline_old)
    return protected, raw_channels, eligible


def ensure_space(objects, archive, manifest, payload, channel_key, *, api=None, limit=LIMIT, report_path=None):
    """Check peak bytes, recheck channel references, then delete only enough old full ZIPs."""
    owned = api is None
    api = api or StorageAPI()
    try:
        before = api.inventory()
        targets = {row["key"]: row["size"] for row in objects.values()}
        archive_key = urlsplit(payload["url"]).path.lstrip("/")
        targets[archive_key] = Path(archive).stat().st_size
        # Count the whole mutable manifest as temporary headroom, not merely its net growth.
        targets[channel_key] = Path(manifest).stat().st_size
        for key, size in targets.items():
            if not isinstance(key, str) or type(size) is not int or size < 0:
                raise ValueError("Invalid proposed upload inventory")
            if key in before and not key.startswith("channel/") and before[key]["size"] != size:
                raise ValueError("Existing immutable update object has a different size")
        additional = sum(size for key, size in targets.items() if key not in before or key.startswith("channel/"))
        used = sum(row["size"] for row in before.values())
        report = dict(limit=limit, before_bytes=used, additional_bytes=additional, deleted=[], status="checking")
        protected, channels, eligible = protected_releases(api, before)
        protected.update(targets)
        selected = []
        remaining = used
        for key in eligible:
            if remaining + additional <= limit: break
            if key not in protected:
                selected.append(key)
                remaining -= before[key]["size"]
        if remaining + additional > limit:
            raise StorageQuotaError("Release would exceed the 1 GB storage limit after safe cleanup; publication stopped")
        # Recheck immutable object metadata and all current channels before the first deletion.
        latest = api.inventory()
        if {k: v["size"] for k, v in latest.items()} != {k: v["size"] for k, v in before.items()}:
            raise ValueError("Storage changed during planning; publication stopped")
        for key, raw in channels.items():
            if api.read(key) != raw: raise ValueError("Release channel changed; cleanup stopped")
        if report_path is None:
            report_path = ROOT / "build/publishing/storage" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")+".json")
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        report.update(status="cleaning", planned_delete=selected)
        Path(report_path).write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        for key in selected:
            for channel, raw in channels.items():
                if api.read(channel) != raw: raise ValueError("Release channel changed; cleanup stopped")
            current = latest[key]
            if current.get("etag") != before[key].get("etag"):
                raise ValueError("Old archive changed; cleanup stopped")
            api.delete(current)
            report["deleted"].append(dict(key=key, size=current["size"]))
            Path(report_path).write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        after = api.inventory()
        for key, raw in channels.items():
            if key not in after or api.read(key) != raw: raise ValueError("Release channel changed during cleanup")
        if any(key in after for key in selected): raise ValueError("Old archive deletion was not confirmed")
        report.update(after_bytes=sum(row["size"] for row in after.values()), status="ready")
        report["peak_bytes"] = report["after_bytes"] + additional
        if report["peak_bytes"] > limit: raise ValueError("Storage headroom changed; publication stopped")
        if report_path is None:
            report_path = ROOT / "build/publishing/storage" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")+".json")
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        print("Storage verified: " + str(report["after_bytes"]) + " bytes used, " + str(report["peak_bytes"]) + " peak; removed " + str(len(selected)) + " old full ZIPs", flush=True)
        return report
    finally:
        if owned: api.close()
