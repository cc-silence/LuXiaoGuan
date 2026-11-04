"""Prepare Linux update objects without dropping executable modes or file links."""
import hashlib
from pathlib import Path
import stat
import zipfile
from modules.app_update_services.catalog import BASELINE, MAX_CATALOG_BYTES
from modules.app_update_services.linux_catalog import managed_linux_path, validate_entries
from modules.app_update_services.manifest import canonical, version_key
from modules.release_identity import archive_root
from scripts.app_update_catalog import compress_stream
from modules.platform_services.linux_recovery_launcher import LAUNCHER, SCRIPT, verify_launcher_bytes


def artifacts(archive, folder, payload, previous=None):
    if payload.get("platform") != "linux-x64":
        raise ValueError("Linux artifact preparation requires an explicit Linux target")
    previous = previous or {}
    if previous and previous.get("platform") != "linux-x64":
        raise ValueError("Cross-platform catalog history is not accepted")
    if previous and version_key(payload["version"]) <= version_key(previous["version"]):
        raise ValueError("Linux catalog publication must advance its version")
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    entries, objects, names, total = [], {}, set(), 0
    launcher_seen=False
    with zipfile.ZipFile(archive) as zipped:
        prefix = archive_root(zipped.namelist())
        if len(zipped.infolist()) > 24000: raise ValueError("Linux archive has too many members")
        for info in zipped.infolist():
            if (info.filename in names or info.flag_bits & 1 or info.create_system != 3
                    or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                raise ValueError("Duplicate, encrypted or non-Unix Linux archive member")
            names.add(info.filename)
            raw_mode = info.external_attr >> 16
            if info.is_dir():
                if not stat.S_ISDIR(raw_mode): raise ValueError("Invalid Linux directory entry")
                directory = info.filename.removeprefix(prefix).rstrip("/")
                if directory:
                    managed_linux_path(directory + "/.directory-check")
                continue
            relative = info.filename.removeprefix(prefix)
            if relative == info.filename: raise ValueError("Unexpected Linux archive root")
            if relative == LAUNCHER:
                if not stat.S_ISREG(raw_mode) or info.file_size!=len(SCRIPT):raise ValueError('Invalid stable Linux launcher')
                verify_launcher_bytes(zipped.read(info),stat.S_IMODE(raw_mode));launcher_seen=True;continue
            if relative == BASELINE:
                if not stat.S_ISREG(raw_mode) or info.file_size > MAX_CATALOG_BYTES:
                    raise ValueError("Linked or excessive Linux baseline")
                continue
            managed_linux_path(relative)
            if stat.S_ISLNK(raw_mode):
                if info.file_size > 512: raise ValueError("Linux link exceeds its size limit")
                entries.append(dict(path=relative, kind="symlink", target=zipped.read(info).decode("utf-8")))
            elif stat.S_ISREG(raw_mode):
                mode = stat.S_IMODE(raw_mode)
                if mode not in {0o644, 0o755}: raise ValueError("Unsafe Linux archive permissions")
                total += info.file_size
                if info.file_size > 2*1024**3 or total > 4*1024**3:
                    raise ValueError("Linux archive exceeds installation bounds")
                with zipped.open(info) as source:
                    metadata, artifact = compress_stream(source, folder / "blobs")
                entries.append(dict(metadata, path=relative, kind="file", mode=mode))
                objects[metadata["blob_sha256"]] = artifact
            else:
                raise ValueError("Linux archive contains a special file")
    if not launcher_seen:raise ValueError('Linux archive lacks its stable recovery launcher')
    active = validate_entries(entries)
    retired = (set(previous.get("retired", [])) | {row["path"] for row in previous.get("files", [])}) - set(active)
    for path in retired: managed_linux_path(path)
    from scripts.app_update_notes import clean_history
    history = clean_history(previous.get("history", []))
    history.append({key:payload[key] for key in ("version", "notes", "published_at")})
    catalog = dict(schema=2, product="RouterManager", platform="linux-x64", version=payload["version"],
                   resource_revision=payload.get("resource_revision", 0), files=sorted(entries, key=lambda row:row["path"]),
                   retired=sorted(retired), history=history, migrations=[])
    history[-1]["resource_revision"] = catalog["resource_revision"]
    data = canonical(catalog)
    if len(data) > MAX_CATALOG_BYTES: raise ValueError("Linux update catalog exceeds its limit")
    sha = hashlib.sha256(data).hexdigest(); path = folder / (sha + ".json"); path.write_bytes(data)
    objects[sha] = dict(path=str(path), key="catalog/" + sha + ".json", sha256=sha, size=len(data))
    fields = dict(catalog_url=payload["url"].split("/files/")[0] + "/catalog/" + sha + ".json",
                  catalog_sha256=sha, catalog_size=len(data))
    return catalog, objects, fields
