"""Quota-only retirement of the same platform's active alpha, with durable resume."""
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit
from modules.app_update_services.manifest import canonical, verify_manifest, version_key
from modules.app_update_services.release_targets import channel_key, validate_platform
from modules.app_update_services.trust import PUBLIC_KEY
from modules.app_update_services.planner import sha256_file
from modules.app_update_services.transaction import atomic_json
from .cloudflare import ROOT
from .storage_api import StorageAPI


def _journal(platform, root=None):
    validate_platform(platform)
    return Path(root or ROOT) / "build/publishing/alpha-replacement" / (platform + ".json")


def prepared_alpha(record):
    archive, manifest = Path(record["archive"]), Path(record["manifest"])
    payload, objects = record["payload"], record["objects"]
    release = verify_manifest(manifest.read_bytes(), PUBLIC_KEY,
                              expected_platform=record["platform"])
    if (release.channel != "alpha" or release.sha256 != payload["sha256"]
            or archive.stat().st_size != release.size or sha256_file(archive) != release.sha256
            or json.loads(manifest.read_bytes())["payload"] != payload):
        raise ValueError("Alpha replacement inputs no longer match the signed release")
    for row in objects.values():
        path = Path(row["path"])
        if path.stat().st_size != row["size"] or sha256_file(path) != row["sha256"]:
            raise ValueError("Alpha replacement objects changed; resume stopped")
    return archive, manifest, payload, objects


def _targets(objects, archive, manifest, payload, channel):
    targets = {row["key"]: row["size"] for row in objects.values()}
    targets[urlsplit(payload["url"]).path.lstrip("/")] = Path(archive).stat().st_size
    targets[channel] = Path(manifest).stat().st_size
    return targets


def _additional(inventory, targets):
    for key, size in targets.items():
        if key in inventory and not key.startswith("channel/") and inventory[key]["size"] != size:
            raise ValueError("Immutable publication object differs from its expected size")
    return sum(size for key, size in targets.items()
               if key not in inventory or key.startswith("channel/"))


def _finish_retirement(record, path, api, limit):
    from .storage import protected_releases
    channel = record["channel_key"]
    marker = canonical(record["maintenance"])
    published = Path(record["manifest"]).read_bytes()
    current = api.read(channel)
    if current == published:
        return record
    if current != marker:
        raise ValueError("Alpha channel changed; replacement stopped")
    before = api.inventory()
    protected_releases(api, before)  # Verify every other channel; tolerate this maintenance slot.
    for key, expected in record["retire"].items():
        if key not in before:
            continue
        row = before[key]
        if (row["size"], row.get("etag")) != (expected["size"], expected.get("etag")):
            raise ValueError("Retired alpha changed; replacement stopped")
        if api.read(channel) != marker:
            raise ValueError("Alpha channel changed before retirement")
        api.delete(row)
        record["deleted"] = sorted(set(record.get("deleted", [])) | {key})
        atomic_json(path, record)
    after = api.inventory()
    if any(key in after for key in record["retire"]):
        raise ValueError("Old alpha deletion was not confirmed")
    if api.read(channel) != marker:
        raise ValueError("Alpha channel changed during retirement")
    targets = _targets(record["objects"], record["archive"], record["manifest"],
                       record["payload"], channel)
    peak = sum(row["size"] for row in after.values()) + _additional(after, targets)
    if peak > limit:
        raise ValueError("Alpha replacement still exceeds storage quota; resume requires reconciliation")
    record.update(state="ready", projected_peak_bytes=peak)
    atomic_json(path, record)
    return record


def reserve_alpha_space(objects, archive, manifest, payload, previous_raw, *,
                        api=None, root=None, limit=1_000_000_000):
    """Called only after ordinary safe reclamation cannot fit an alpha upload."""
    from .storage import archive_identity, protected_releases
    platform = validate_platform(payload.get("platform", "win-x64"))
    channel = channel_key(platform, "alpha")
    path = _journal(platform, root)
    owned = api is None
    api = api or StorageAPI()
    try:
        if previous_raw is None:
            raise ValueError("No same-platform current alpha is available for quota replacement")
        old = verify_manifest(previous_raw, PUBLIC_KEY, expected_platform=platform)
        new = verify_manifest(Path(manifest).read_bytes(), PUBLIC_KEY, expected_platform=platform)
        if old.channel != "alpha" or new.channel != "alpha" or version_key(new.version) <= version_key(old.version):
            raise ValueError("Quota retirement requires an advancing alpha on the same platform")
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("state") != "complete":
                raise ValueError("Resume the unfinished alpha publication before replacing it")
        before = api.inventory()
        protected, channels, eligible = protected_releases(api, before)
        if channels.get(channel) != previous_raw:
            raise ValueError("Current alpha channel differs from the expected signed release")
        current_key = urlsplit(old.url).path.lstrip("/")
        if current_key not in before:
            raise ValueError("The old current alpha archive is already missing")
        # Retire no current stable or other-platform archive, even under quota pressure.
        retired = [key for key in eligible
                   if (archive_identity(key) or (None, None))[0] == platform
                   and archive_identity(key)[1][1] == 1]
        retired.append(current_key)
        marker = dict(schema=1, product="RouterManager", state="publishing",
                      platform=platform, channel="alpha", sha256=new.sha256,
                      version=new.version)
        marker_raw = canonical(marker)
        if len(marker_raw) > 65536:
            raise ValueError("Alpha maintenance metadata exceeds the channel size limit")
        targets = _targets(objects, archive, manifest, payload, channel)
        used = sum(row["size"] for row in before.values())
        peak = used - sum(before[key]["size"] for key in retired) + _additional(before, targets)
        if peak > limit or used + max(0, len(marker_raw)-before[channel]["size"]) > limit:
            raise ValueError("Even retiring this alpha cannot provide sufficient quota")
        record = dict(schema=1, state="preparing", platform=platform, channel_key=channel,
                      archive=str(Path(archive).resolve()), manifest=str(Path(manifest).resolve()),
                      payload=payload, objects=objects, maintenance=marker,
                      previous_manifest=base64.b64encode(previous_raw).decode("ascii"),
                      retire={key:before[key] for key in retired}, deleted=[])
        prepared_alpha(record)
        latest = api.inventory()
        if {key:(row["size"],row.get("etag")) for key,row in latest.items()} != {
                key:(row["size"],row.get("etag")) for key,row in before.items()}:
            raise ValueError("Cloud inventory changed before alpha replacement")
        for key, raw in channels.items():
            if api.read(key) != raw:
                raise ValueError("A release channel changed before alpha replacement")
        atomic_json(path, record)
        maintenance_path = path.with_suffix(".maintenance.json")
        maintenance_path.write_bytes(marker_raw)
        # Save the recovery record before changing the channel or deleting bytes.
        api.put(maintenance_path, channel, "application/json")
        if api.read(channel) != marker_raw:
            raise ValueError("Alpha maintenance state was not confirmed; no retirement attempted")
        return _finish_retirement(record, path, api, limit)
    finally:
        if owned:
            api.close()


def resume_alpha_space(archive, platform, *, api=None, root=None, limit=1_000_000_000):
    path = _journal(platform, root)
    if not path.is_file():
        return None
    if path.stat().st_size > 16*1024*1024:
        raise ValueError("Invalid alpha recovery journal")
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("state") == "complete":
        return None
    if sha256_file(archive) != record["payload"]["sha256"]:
        raise ValueError("An unfinished alpha publication requires the same verified archive")
    prepared_alpha(record)
    owned = api is None
    api = api or StorageAPI()
    try:
        previous = base64.b64decode(record["previous_manifest"], validate=True)
        marker = canonical(record["maintenance"])
        current = api.read(record["channel_key"])
        if current == previous:
            # A crash before the marker left the old release usable. Retry from
            # exactly the original signed preparation rather than regenerate it.
            record["state"] = "complete"
            atomic_json(path, record)
            return reserve_alpha_space(record["objects"], record["archive"], record["manifest"],
                                       record["payload"], previous, api=api, root=root, limit=limit)
        if current not in (marker, Path(record["manifest"]).read_bytes()):
            raise ValueError("Another alpha replaced the pending transaction; reconciliation required")
        return _finish_retirement(record, path, api, limit)
    finally:
        if owned:
            api.close()


def assert_alpha_channel(record, *, api=None):
    owned = api is None
    api = api or StorageAPI()
    try:
        if api.read(record["channel_key"]) not in (
                canonical(record["maintenance"]), Path(record["manifest"]).read_bytes()):
            raise ValueError("Alpha channel changed during its quota replacement")
    finally:
        if owned:
            api.close()


def complete_alpha_space(record, *, root=None):
    record["state"] = "complete"
    atomic_json(_journal(record["platform"], root), record)
