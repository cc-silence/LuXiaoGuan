"""Stream package inspection so large private backups do not duplicate payloads in RAM."""
from contextlib import contextmanager
import hashlib
import io
import tarfile
import zlib
from .dependency_snapshot_archive import tar_members, SCRIPTS, APK_SCRIPTS, MAX_PACKAGE

MAX_CONTROL = 4 * 1024 * 1024


@contextmanager
def _parts(path, manager):
    if manager == "opkg":
        with tarfile.open(path, "r:gz") as archive:
            entries = tar_members(archive)
            if set(entries) != {"debian-binary", "control.tar.gz", "data.tar.gz"}:
                raise ValueError("Invalid private IPK container")
            if any(not entry.isfile() or entry.size > MAX_PACKAGE for entry in entries.values()):
                raise ValueError("Invalid private IPK component")
            if entries["control.tar.gz"].size > MAX_CONTROL:
                raise ValueError("Private IPK control data exceeds limit")
            if entries["debian-binary"].size != 4 or archive.extractfile(entries["debian-binary"]).read() != b"2.0\n":
                raise ValueError("Invalid private IPK format version")
            control = archive.extractfile(entries["control.tar.gz"]).read()
            with archive.extractfile(entries["data.tar.gz"]) as payload:
                yield control, payload, "r:gz"
    elif manager == "apk":
        with path.open("rb") as source:
            decoder = zlib.decompressobj(31)
            control = bytearray()
            while not decoder.eof:
                block = source.read(65536)
                if not block:
                    raise ValueError("Truncated private APK control stream")
                control.extend(decoder.decompress(block, MAX_CONTROL + 1 - len(control)))
                if len(control) > MAX_CONTROL or decoder.unconsumed_tail:
                    raise ValueError("Private APK control data exceeds limit")
            source.seek(source.tell() - len(decoder.unused_data))
            if source.tell() == path.stat().st_size:
                raise ValueError("Private APK has no payload stream")
            yield bytes(control), source, "r:"
    else:
        raise ValueError("Unknown private package format")


def inspect_package(path, manager, row):
    with _parts(path, manager) as (control, payload, control_mode):
        with tarfile.open(fileobj=io.BytesIO(control), mode=control_mode) as archive:
            entries = tar_members(archive)
            expected = "control" if manager == "opkg" else ".PKGINFO"
            allowed = ({"control", "conffiles", *SCRIPTS} if manager == "opkg"
                       else {".PKGINFO", *("." + name for name in APK_SCRIPTS)})
            if not set(entries) <= allowed or expected not in entries:
                raise ValueError("Unexpected private package control files")
            for member in entries.values():
                if not member.isfile() or member.size > 1024 * 1024:
                    raise ValueError("Unsafe private package control entry")
            text = archive.extractfile(entries[expected]).read().decode("utf-8")
            fields = {}
            for line in text.splitlines():
                key, sep, value = line.partition(":" if manager == "opkg" else " = ")
                if sep:
                    if key in {"Package", "Version", "pkgname", "pkgver"} and key in fields:
                        raise ValueError("Duplicate private package identity")
                    fields[key] = value.strip()
            name_key, version_key = ("Package", "Version") if manager == "opkg" else ("pkgname", "pkgver")
            if fields.get(name_key) != row["name"] or fields.get(version_key) != row["version"]:
                raise ValueError("Private package identity does not match the backup manifest")
        if manager == "apk":
            position, digest = payload.tell(), hashlib.sha256()
            for block in iter(lambda: payload.read(1024 * 1024), b""):
                digest.update(block)
            if fields.get("datahash") != digest.hexdigest():
                raise ValueError("Private APK payload checksum mismatch")
            payload.seek(position)
        with tarfile.open(fileobj=payload, mode="r:gz") as archive:
            entries = tar_members(archive, payload=True)
            if any(member.islnk() for member in entries.values()):
                raise ValueError("Hard links are not permitted in private restore packages")
            return sum(member.size for member in entries.values())

