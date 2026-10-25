"""Native installed-package listings shared by detection and dependency solving."""
import re
from .dependency_resolution import InstalledPackages

_NAME = r"[A-Za-z0-9][A-Za-z0-9+_.-]*"
_OPKG_ROW = re.compile(r"(" + _NAME + r") - (\S+)(?: -(?: [^\r\n]*)?)?")


def parse_package_listing(text, manager, *, allow_names_only=False):
    """opkg print_pkg optionally appends a description; it is not version text."""
    if manager not in {'opkg', 'apk'}:
        raise ValueError('Unknown package manager')
    versions = {}
    for raw in str(text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _OPKG_ROW.fullmatch(line) if manager == 'opkg' else None
        if match:
            name, version = match.groups()
        elif (manager == 'apk' or allow_names_only) and re.fullmatch(_NAME, line):
            name, version = line, ''
        else:
            raise ValueError('Invalid native installed-package listing')
        if name in versions and versions[name] != version:
            raise ValueError('Conflicting installed-package versions')
        versions[name] = version
    return InstalledPackages(versions)
