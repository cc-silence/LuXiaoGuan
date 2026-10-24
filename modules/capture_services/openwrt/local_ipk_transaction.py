"""No-feed opkg configuration and closed local transaction validation."""
import re


def prepare_config(directory):
    if directory not in ("/tmp/winbox-dependency-pkgs", "/tmp/winbox-signal-pkgs"):
        raise ValueError("Invalid offline transaction directory")
    return """set -eu
umask 077
mkdir -p /tmp/winbox-dependency-pkgs/no-feeds
{
 awk '$1 == "dest" || ($1 == "option" && $2 == "overlay_root") { print }' /etc/opkg.conf
 opkg print-architecture
 printf 'lists_dir ext /tmp/winbox-dependency-pkgs/no-feeds\n'
} > /tmp/winbox-dependency-pkgs/local.conf
""".replace("/tmp/winbox-dependency-pkgs", directory)


def local_command(manager, paths, simulate=False, *, directory="/tmp/winbox-dependency-pkgs"):
    from pathlib import PurePosixPath
    paths = tuple(paths)
    if directory not in ("/tmp/winbox-dependency-pkgs", "/tmp/winbox-signal-pkgs"):
        raise ValueError("Invalid offline transaction directory")
    if any(str(PurePosixPath(path).parent) != directory for path in paths):
        raise ValueError("Local package directory differs from transaction")
    command = manager.local_install(paths, simulate=simulate)
    return ("OPKG_CONF_DIR=" + directory + "/no-feeds "
            + command.replace("opkg ", "opkg -f " + directory + "/local.conf ", 1))



def verify_local_plan(plan, expected):
    from .package_space import planned_packages
    planned = planned_packages("opkg", plan)
    if (any(expected.get(name) != version for name, version in planned.items())
            or re.search(r"(?im)^\s*Downloading\s", plan)):
        raise ValueError("\u79bb\u7ebf\u9884\u6f14\u5305\u6216\u7248\u672c\u8d85\u51fa\u5df2\u6821\u9a8c\u6e05\u5355\uff0c\u672a\u5b89\u88c5\u3002")
