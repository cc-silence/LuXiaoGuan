"""Rebuild private local packages from captured files; no downloads or host extraction."""
import copy
import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
import zipfile
from pathlib import Path
from .installed_dependency_catalog import (NAME, MAX_PACKAGES, MAX_FILES, allowed_payload,
                                            file_path, checked_package, dependency_closure,
                                            dependency_roots)

MAX_TOTAL = 512 * 1024 * 1024
MAX_PACKAGE = 128 * 1024 * 1024
SCRIPTS = ("preinst", "postinst", "prerm", "postrm", "postinst-pkg", "prerm-pkg")
APK_SCRIPTS = ("pre-install", "post-install", "pre-deinstall", "post-deinstall",
               "pre-upgrade", "post-upgrade", "trigger")


def digest_file(path, check=lambda: None):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            check()
            value.update(block)
    return value.hexdigest()


def tar_members(archive, *, payload=False, size_limit=MAX_TOTAL):
    found, size = {}, 0
    for member in archive:
        name = member.name.removeprefix("./").rstrip("/")
        if name in {"", "."} and member.isdir():
            continue
        name = file_path(name)
        if member.name.startswith("/") or name in found or len(found) >= MAX_FILES:
            raise ValueError("Unsafe or duplicate archive entry")
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ValueError("Special device in dependency archive")
        if member.size < 0:
            raise ValueError("Negative archive size")
        size += member.size
        if size > size_limit:
            raise ValueError("Dependency archive is too large")
        if payload and not allowed_payload(name):
            raise ValueError("Protected system path in dependency payload: " + name)
        if member.islnk():
            file_path(member.linkname.removeprefix("./"))
        found[name] = member
    # Do not write a file through a packaged symbolic-link parent.
    links = {name for name, m in found.items() if m.issym() or m.islnk()}
    for name in found:
        parts = name.split("/")
        if any("/".join(parts[:i]) in links for i in range(1, len(parts))):
            raise ValueError("Dependency archive has a link as a parent directory")
    return found


def _entry(archive, name, data, mode=0o644):
    item = tarfile.TarInfo(name)
    item.size, item.mode, item.uid, item.gid = len(data), mode, 0, 0
    archive.addfile(item, io.BytesIO(data))


def _tar_blob(entries, *, terminate=True):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for name, data, mode in entries:
            _entry(archive, name, data, mode)
        end = archive.offset
    raw = target.getvalue() if terminate else target.getvalue()[:end]
    return gzip.compress(raw, mtime=0)


def _write_ipk(path, parts):
    # OpenWrt's compact opkg reads a gzip-compressed tar container, not Debian ar.
    with tarfile.open(path, "w:gz", format=tarfile.GNU_FORMAT) as target:
        for name, source in parts:
            if isinstance(source, Path):
                info = tarfile.TarInfo(name)
                info.size, info.mode = source.stat().st_size, 0o644
                with source.open("rb") as stream:
                    target.addfile(info, stream)
            else:
                _entry(target, name, source)


def rebuild_package(pkg, manager, payload, destination, check, *, directory_metadata=None):
    if pkg.extended_attributes:
        raise ValueError("Cannot preserve package extended attributes: " + pkg.name
                         + " (" + ", ".join(pkg.extended_attributes[:5]) + ")")
    data_path = destination.with_suffix(".data")
    installed_size = 0
    try:
        with tarfile.open(payload, "r:gz") as captured:
            index = tar_members(captured, payload=True)
            with tarfile.open(data_path, "w:gz", format=tarfile.PAX_FORMAT if manager == "apk" else tarfile.GNU_FORMAT) as data:
                directories = set()
                for name in pkg.files:
                    if name in index:
                        parts = name.split("/")
                        directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
                if directory_metadata is not None:
                    directories.update(name for name in (*pkg.files, *pkg.directories)
                                       if name not in index and name in directory_metadata and allowed_payload(name))
                for directory in tuple(directories):
                    parts = directory.split("/")
                    directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
                for name in sorted(directories, key=lambda value: (value.count("/"), value)):
                    check()
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.DIRTYPE
                    if directory_metadata is None:
                        member.mode, member.uid, member.gid, member.mtime = 0o755, 0, 0, 0
                    else:
                        if name not in directory_metadata:
                            raise ValueError("Missing captured directory metadata: " + name)
                        member.mode, member.uid, member.gid, member.mtime = directory_metadata[name]
                    data.addfile(member)
                for name in pkg.files:
                    check()
                    member = index.get(name)
                    if member is None:
                        if (directory_metadata is not None and name in directory_metadata
                                and allowed_payload(name)):
                            continue  # Proven directory; never recursively capture its contents.
                        raise ValueError("Missing captured package payload: " + name)
                    member = copy.copy(member)
                    member.name = name
                    member.pax_headers = {}
                    source = None
                    if member.isfile() or member.islnk():
                        if member.islnk():
                            linked = index.get(file_path(member.linkname.removeprefix("./")))
                            if linked is None or not linked.isfile():
                                raise ValueError("Unresolved hard link in dependency files")
                            member.type, member.size, member.linkname = tarfile.REGTYPE, linked.size, ""
                        source = captured.extractfile(index[name])
                        if source is None:
                            raise ValueError("Missing captured file: " + name)
                        installed_size += member.size
                        if manager == "apk":
                            checksum = hashlib.sha1()  # Required by the native APK v2 tar reader.
                            for block in iter(lambda: source.read(1024 * 1024), b""):
                                check()
                                checksum.update(block)
                            source.seek(0)
                            member.pax_headers["APK-TOOLS.checksum.SHA1"] = checksum.hexdigest()
                    elif member.issym() and manager == "apk":
                        member.pax_headers["APK-TOOLS.checksum.SHA1"] = hashlib.sha1(member.linkname.encode("utf-8")).hexdigest()
                    try:
                        data.addfile(member, source)
                    finally:
                        if source is not None:
                            source.close()
        if manager == "opkg":
            fields = {k:v for k,v in pkg.metadata.items()
                      if k not in {"Status", "Installed-Time", "Auto-Installed", "Conffiles",
                                   "Size", "Filename", "MD5sum", "SHA256sum"}}
            fields["Installed-Size"] = str(installed_size)
            control = "\n".join(k + ": " + v for k,v in fields.items()) + "\n"
            entries = [("control", control.encode("utf-8"), 0o644)]
            entries += [(name, value, 0o755) for name,value in pkg.scripts.items() if name in SCRIPTS]
            if "conffiles" in pkg.scripts:
                entries.append(("conffiles", pkg.scripts["conffiles"], 0o644))
            _write_ipk(destination, [("debian-binary", b"2.0\n"),
                                   ("control.tar.gz", _tar_blob(entries)), ("data.tar.gz", data_path)])
        elif manager == "apk":
            names = {"P":"pkgname", "V":"pkgver", "A":"arch", "T":"pkgdesc", "U":"url",
                     "L":"license", "o":"origin", "m":"maintainer", "t":"builddate",
                     "c":"commit", "k":"provider_priority", "q":"replaces_priority"}
            lines = [names[k] + " = " + v for k,v in pkg.metadata.items() if k in names]
            for key, title in (("D", "depend"), ("p", "provides"), ("r", "replaces"), ("i", "install_if")):
                lines += [title + " = " + value for value in pkg.metadata.get(key, "").split()]
            lines += ["size = " + str(installed_size), "datahash = " + digest_file(data_path, check)]
            if pkg.triggers:
                lines.append("triggers = " + " ".join(pkg.triggers))
            entries = [(".PKGINFO", ("\n".join(lines) + "\n").encode("utf-8"), 0o644)]
            entries += [("." + name, value, 0o755) for name,value in pkg.scripts.items() if name in APK_SCRIPTS]
            # APK v2 is a control gzip stream without tar EOF followed by data gzip.
            # APK 3 retains the native v2 reader. Native simulation is required before restore.
            with destination.open("xb") as target, data_path.open("rb") as source:
                target.write(_tar_blob(entries, terminate=False))
                shutil.copyfileobj(source, target, 1024 * 1024)
        else:
            raise ValueError("Unknown package format")
        if destination.stat().st_size > MAX_PACKAGE:
            raise ValueError("Rebuilt dependency package exceeds size limit")
        return installed_size
    finally:
        data_path.unlink(missing_ok=True)


def save_snapshot(path, manifest, packages, check):
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True, sort_keys=True))
        for row in manifest["packages"]:
            check()
            if not row["builtin"]:
                archive.write(packages / (row["name"] + "." + manifest["manager"]), row["file"])
            if row.get("configuration"):
                archive.write(packages / (row["name"] + ".config.tgz"), row["configuration"]["file"])
    if path.stat().st_size > MAX_TOTAL:
        raise ValueError("Dependency backup exceeds size limit")


def load_snapshot(path, workspace, check):
    if Path(path).is_symlink() or Path(path).stat().st_size > MAX_TOTAL:
        raise ValueError("Invalid dependency backup file")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if (len(entries) > 2 * MAX_PACKAGES + 1 or len({x.filename for x in entries}) != len(entries)
                or sum(x.file_size for x in entries) > MAX_TOTAL):
            raise ValueError("Dependency backup is too large or contains duplicates")
        info = archive.getinfo("manifest.json")
        if info.file_size > 8 * 1024 * 1024:
            raise ValueError("Dependency manifest exceeds limit")
        manifest = json.loads(archive.read(info))
        if (not isinstance(manifest, dict) or manifest.get("format") != "luxiaoguan-installed-dependencies"
                or manifest.get("schema") not in {1, 2} or manifest.get("manager") not in {"opkg", "apk"}
                or not isinstance(manifest.get("identity"), dict)):
            raise ValueError("Unsupported dependency backup format")
        rows = manifest.get("packages")
        if not isinstance(rows, list) or not 0 < len(rows) <= MAX_PACKAGES:
            raise ValueError("Invalid dependency backup package list")
        expected, parsed = {"manifest.json"}, {}
        for row in rows:
            check()
            pkg = checked_package(row["name"], row["version"], row["metadata"])
            if pkg.name in parsed or type(row.get("builtin")) is not bool:
                raise ValueError("Invalid dependency backup package")
            parsed[pkg.name] = pkg
            if not row["builtin"]:
                name = "packages/" + pkg.name + (".ipk" if manifest["manager"] == "opkg" else ".apk")
                if (row.get("file") != name or type(row.get("size")) is not int
                        or not 0 < row["size"] <= MAX_PACKAGE
                        or not re.fullmatch("[a-f0-9]{64}", row.get("sha256", ""))):
                    raise ValueError("Invalid backup package metadata")
                item = archive.getinfo(name)
                if item.file_size != row["size"]:
                    raise ValueError("Backup package size mismatch")
                expected.add(name)
                local = workspace / (pkg.name + "." + manifest["manager"])
                with archive.open(item) as source, local.open("xb") as target:
                    size = 0
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        check()
                        size += len(block)
                        if size > row["size"]:
                            raise ValueError("Oversized backup package")
                        target.write(block)
                if digest_file(local, check) != row["sha256"]:
                    raise ValueError("Backup package checksum mismatch")
            settings = row.get("configuration")
            if settings is not None:
                if manifest["schema"] != 2 or not isinstance(settings, dict):
                    raise ValueError("Unexpected backup configuration")
                filename = "configurations/" + pkg.name + ".tgz"
                if (settings.get("file") != filename or type(settings.get("size")) is not int
                        or not 0 < settings["size"] <= MAX_PACKAGE
                        or not re.fullmatch("[a-f0-9]{64}", settings.get("sha256", ""))):
                    raise ValueError("Invalid backup configuration metadata")
                entry = archive.getinfo(filename)
                if entry.file_size != settings["size"]:
                    raise ValueError("Backup configuration size mismatch")
                configuration = workspace / (pkg.name + ".config.tgz")
                with archive.open(entry) as source, configuration.open("xb") as target:
                    received = 0
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        check()
                        received += len(block)
                        if received > settings["size"]:
                            raise ValueError("Oversized backup configuration")
                        target.write(block)
                    if received != settings["size"]:
                        raise ValueError("Truncated backup configuration")
                check()
                if digest_file(configuration, check) != settings["sha256"]:
                    raise ValueError("Backup configuration checksum mismatch")
                from .snapshot_configuration import inspect_configuration
                inspect_configuration(configuration, settings)
                expected.add(filename)
        roots = set(manifest.get("roots", []))
        if not roots or not roots <= dependency_roots() or not roots <= parsed.keys():
            raise ValueError("Backup roots are not in the application dependency catalog")
        closure = dependency_closure(parsed, roots, manifest["manager"])
        if {p.name for p in closure} != set(parsed):
            raise ValueError("Backup includes unrelated or incomplete packages")
        if expected != {x.filename for x in entries}:
            raise ValueError("Unexpected files in dependency backup")
        return manifest

