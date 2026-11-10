"""Native offline install commands and strict additive-only plan validation."""
import re
from .command_builder import quote_argument as q
from .installed_dependency_catalog import MAX_PACKAGES


def install_command(manager, root, paths, *, simulate=False):
    if not paths or len(paths) > MAX_PACKAGES:
        raise ValueError("Invalid dependency restore transaction")
    if any(not value.startswith(root + "/") or "/" in value[len(root)+1:] for value in paths):
        raise ValueError("Package outside private restore workspace")
    arguments = " ".join(map(q, paths))
    if manager == "apk":
        return ("apk --no-network --allow-untrusted --repositories-file /dev/null add "
                + ("--simulate " if simulate else "") + arguments)
    if manager != "opkg":
        raise ValueError("Unknown package manager")
    return ("OPKG_CONF_DIR=" + q(root + "/empty") + " opkg --conf " + q(root + "/opkg.conf")
            + (" --noaction" if simulate else "") + " install " + arguments)


def verify_restore_plan(manager, text, wanted):
    found = {}
    for line in text.splitlines():
        if manager == "apk":
            match = re.fullmatch(r"\s*\(\s*\d+/\s*\d+\)\s+Installing\s+"
                                 r"([A-Za-z0-9][A-Za-z0-9+_.-]*)\s+\(([^)]+)\)\s*", line)
            suspicious = re.match(r"\s*\(\s*\d+/\s*\d+\)", line) or re.search(
                r"\b(?:Installing|Removing|Upgrading|Downgrading|Reinstalling|Replacing|Purging|Updating pinning)\b", line)
        else:
            configuring = re.fullmatch(r"Configuring ([A-Za-z0-9][A-Za-z0-9+_.-]*)\.", line.strip())
            if configuring:
                if configuring[1] not in wanted:
                    raise ValueError("Restore plan would configure an unrelated package: " + configuring[1])
                continue
            match = re.match(r"^Installing ([A-Za-z0-9][A-Za-z0-9+_.-]*) \(([^)]+)\)(?: to \S+)?[. ]*$", line)
            suspicious = re.match(r"^(?:Installing|Removing|Upgrading|Downgrading|Reinstalling|Replacing)\b", line)
        if match:
            name, version = match.groups()
            if name not in wanted or wanted[name] != version or (name in found and found[name] != version):
                raise ValueError("Restore plan would change an unapproved package: " + name)
            found[name] = version
        elif suspicious:
            raise ValueError("Restore plan is not strictly additive")
    if found != wanted:
        raise ValueError("Native installer did not confirm the complete restore plan")
    return found

