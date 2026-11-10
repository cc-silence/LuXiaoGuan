"""Installed dependency graph and file ownership, independent of Qt."""
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*")
MAX_PACKAGES = 512
MAX_FILES = 100000


@dataclass
class InstalledPackage:
    name: str
    version: str
    metadata: dict
    files: tuple = ()
    scripts: dict = field(default_factory=dict)
    triggers: tuple = ()
    directories: tuple = ()
    extended_attributes: tuple = ()


def dependency_roots():
    from .common_tool_dependencies import TOOLS
    from .other_tool_dependencies import OTHER_TOOLS
    from .dependency_groups import CAPTURE_PACKAGES
    result = {"openssh-sftp-server", "tmux"}
    result.update(name for tool in (*TOOLS, *OTHER_TOOLS) for name in tool.package_names)
    result.update(name for names in CAPTURE_PACKAGES.values() for name in names)
    return frozenset(result)


def file_path(value):
    value = str(value).removeprefix("/")
    path = PurePosixPath(value)
    if (not value or value.startswith("/") or chr(92) in value or path.as_posix() != value
            or any(p in {"", ".", ".."} for p in value.split("/"))
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Invalid installed file path")
    return value


def allowed_payload(value):
    path = file_path(value)
    forbidden = ("etc/config/network", "etc/config/wireless", "etc/config/firewall",
                 "etc/config/dhcp", "etc/config/system", "etc/passwd", "etc/shadow",
                 "etc/group", "etc/gshadow", "etc/dropbear", "etc/ssh", "etc/opkg",
                 "etc/apk", "usr/lib/opkg", "lib/opkg", "lib/apk/db", "rom", "overlay",
                 "proc", "sys", "dev", "tmp", "run", "root", "home", "var")
    return not any(path == p or path.startswith(p + "/") for p in forbidden)


def checked_package(name, version, metadata, files=()):
    if (not NAME.fullmatch(name) or not version or len(version) > 200
            or any(c.isspace() or ord(c) < 32 for c in version)):
        raise ValueError("Invalid installed package identity")
    return InstalledPackage(name, version, metadata, tuple(dict.fromkeys(files)))


def parse_opkg(text, file_lists=None, *, require_clean=False):
    result = {}
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        if not paragraph.strip():
            continue
        fields, last = {}, None
        for line in paragraph.splitlines():
            if line[:1].isspace() and last:
                fields[last] += "\n" + line
                continue
            key, sep, value = line.partition(":")
            if not sep or key in fields:
                raise ValueError("Invalid opkg database record")
            fields[key], last = value.strip(), key
        status = fields.get("Status", "").split()
        if require_clean and (len(status) != 3 or "reinstreq" in status
                              or status[-1] not in {"installed", "not-installed", "config-files"}):
            raise ValueError("Pending package operation must be resolved first: " + fields.get("Package", "?"))
        if status[-1:] != ["installed"]:
            continue
        pkg = checked_package(fields.get("Package", ""), fields.get("Version", ""), fields)
        if file_lists is not None:
            if pkg.name not in file_lists:
                raise ValueError("Missing installed file list: " + pkg.name)
            pkg.files = tuple(dict.fromkeys(file_path(p.strip()) for p in
                              file_lists[pkg.name].splitlines() if p.strip() not in {"", "/", "/."}))
        if pkg.name in result:
            raise ValueError("Duplicate installed package")
        result[pkg.name] = pkg
    if len(result) > 10000:
        raise ValueError("Installed database too large")
    return result


def parse_apk(text):
    result = {}
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        if not paragraph.strip():
            continue
        fields, files, directory, directories, attributes = {}, [], None, [], []
        for line in paragraph.splitlines():
            if len(line) < 2 or line[1] != ":":
                raise ValueError("Unsupported APK installed database format")
            key, value = line[0], line[2:]
            if key == "F":
                directory = file_path(value) if value else ""
                if directory:
                    directories.append(directory)
            elif key == "R":
                if directory is None or "/" in value:
                    raise ValueError("Invalid APK file ownership")
                files.append(file_path((directory + "/" if directory else "") + value))
            elif key in {"M", "a"}:
                # APK stores an optional xattr digest after uid:gid:mode. The
                # digest cannot reconstruct the actual ACL/capability values.
                parts = value.split(":", 3)
                if len(parts) == 4 and parts[3]:
                    if directory is None or (key == "a" and not files):
                        raise ValueError("Invalid APK extended attribute ownership")
                    attributes.append(files[-1] if key == "a" else directory or "/")
            elif key != "Z":
                if key in fields:
                    raise ValueError("Duplicate APK metadata field")
                fields[key] = value
        pkg = checked_package(fields.get("P", ""), fields.get("V", ""), fields, files)
        pkg.directories = tuple(dict.fromkeys(directories))
        pkg.extended_attributes = tuple(dict.fromkeys(attributes))
        if pkg.name in result:
            raise ValueError("Duplicate installed package")
        result[pkg.name] = pkg
    if len(result) > 10000:
        raise ValueError("Installed database too large")
    return result


def _dep_name(value, manager):
    value = value.strip()
    pattern = (r"([A-Za-z0-9][A-Za-z0-9+_.:/-]*)(?:[<>=~].+)?" if manager == "apk"
               else r"([A-Za-z0-9][A-Za-z0-9+_.-]*)(?:\s*\([^()]+\))?")
    match = re.fullmatch(pattern, value)
    if not match:
        raise ValueError("Unsupported dependency expression: " + value)
    return match[1]


def require_package_healthy(pkg):
    if (pkg.metadata.get("f", "").strip("S")
            or "reinstreq" in pkg.metadata.get("Status", "").split()):
        raise ValueError("Installed dependency is broken: " + pkg.name)


def dependency_closure(packages, roots, manager):
    if manager not in {"opkg", "apk"}:
        raise ValueError("Unknown package manager")
    providers = {}
    for pkg in packages.values():
        value = pkg.metadata.get("Provides" if manager == "opkg" else "p", "")
        for token in value.split(",") if manager == "opkg" else value.split():
            if token.strip():
                providers.setdefault(_dep_name(token, manager), set()).add(pkg.name)
    selected, pending = set(), sorted(set(roots) & packages.keys())
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        require_package_healthy(packages[name])
        if len(selected) > MAX_PACKAGES:
            raise ValueError("Dependency backup exceeds package limit")
        value = packages[name].metadata.get("Depends" if manager == "opkg" else "D", "")
        if manager == "opkg" and packages[name].metadata.get("Pre-Depends"):
            value += (", " if value else "") + packages[name].metadata["Pre-Depends"]
        for expression in value.split(",") if manager == "opkg" else value.split():
            if not expression.strip() or expression.strip().startswith("!"):
                continue
            candidates = set()
            for option in expression.split("|"):
                dep = _dep_name(option, manager)
                candidates.update({dep} if dep in packages else providers.get(dep, ()))
            if not candidates:
                raise ValueError("Installed dependency is missing: " + expression)
            pending.extend(candidates - selected)
    return tuple(packages[name] for name in sorted(selected))


def restore_selection(rows, installed):
    missing, skipped = [], []
    for row in rows:
        current = installed.get(row["name"])
        if current is not None:
            require_package_healthy(current)
            # Keep the existing package; the native solver validates version constraints.
            skipped.append(row["name"])
        elif row.get("builtin"):
            raise ValueError("Firmware dependency is missing: " + row["name"])
        else:
            missing.append(row["name"])
    return tuple(missing), tuple(skipped)

