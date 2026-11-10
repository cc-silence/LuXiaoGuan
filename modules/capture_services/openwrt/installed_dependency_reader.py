"""Read-only router inventory for same-device installed-dependency snapshots."""
import base64
import hashlib
import io
import json
import re
import tarfile
from .command_runner import require_command_completion
from .installed_dependency_catalog import parse_opkg, parse_apk, file_path
from .dependency_snapshot_archive import tar_members, SCRIPTS, APK_SCRIPTS

DATABASES = {"opkg": "/usr/lib/opkg/status", "apk": "/lib/apk/db/installed"}


class SnapshotCommandFailure(RuntimeError):
    """Preserve whether a remote nonzero exit was actually acknowledged."""
    def __init__(self, message, result):
        super().__init__(message)
        self.exit_confirmed = result.exit_code >= 0


class InstalledDependencyReader:
    def __init__(self, runner, transfer, guard):
        self.runner, self.transfer, self.guard = runner, transfer, guard

    def run(self, action, command, timeout=30):
        self.guard.check()
        result = self.runner.run("dependency_snapshot_" + action, command,
                                 timeout=min(timeout, max(0.1, self.guard.deadline - __import__("time").monotonic())),
                                 cancel_event=self.guard.cancel, output_limit=2 * 1024 * 1024)
        require_command_completion(result)
        if result.exit_code:
            from .package_manager import package_error_reason
            reason = package_error_reason(result.stdout + "\n" + result.stderr)
            if reason == "\u8bf7\u68c0\u67e5\u8054\u7f51\u3001\u7a7a\u95f4\u4e0e\u5305\u7ba1\u7406\u5668\u72b6\u6001":
                reason = "\u8def\u7531\u5668\u8fd4\u56de\u9519\u8bef\uff0c\u672a\u786e\u8ba4\u5b8c\u6210"
            if action == "original_extract":
                # Extraction output contains package paths and native errors,
                # never login credentials. Bound the displayed diagnostic.
                native = "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())[:2000]
                reason += "\n\u539f\u5305\u89e3\u5305\u5931\u8d25\uff0c\u9519\u8bef\u7801 " + str(result.exit_code)
                if native:
                    reason += "\n" + native
            raise SnapshotCommandFailure("\u4f9d\u8d56\u64cd\u4f5c\u5931\u8d25\uff08" + action + "\uff09\uff1a" + reason, result)
        self.guard.check()
        return result.stdout.strip()

    def inventory(self):
        manager = self.run("manager", "test \"$(id -u)\" = 0 || exit 41; "
                           "a=0; b=0; command -v opkg >/dev/null 2>&1 && a=1; "
                           "command -v apk >/dev/null 2>&1 && b=1; "
                           "case $a$b in 10) echo opkg;; 01) echo apk;; *) exit 42;; esac")
        if manager not in DATABASES:
            raise ValueError("Cannot identify the native package manager")
        database = DATABASES[manager]
        text = self.transfer.read("cat " + database).decode("utf-8")
        # opkg install also configures unrelated unpacked packages. Refuse a
        # dirty database before planning any private restore transaction.
        packages = parse_opkg(text, require_clean=True) if manager == "opkg" else parse_apk(text)
        builtin_text = self.transfer.read("cat /rom" + database).decode("utf-8")
        builtin = (parse_opkg if manager == "opkg" else parse_apk)(builtin_text)
        if not packages or not builtin:
            raise ValueError("Firmware package inventory is unavailable")
        board = json.loads(self.run("board", "ubus call system board"))
        release = board.get("release", {})
        if not board.get("board_name") or not release.get("revision") or not release.get("target"):
            raise ValueError("Incomplete router firmware identity")
        immutable = self.run("firmware", "sha256sum /rom/etc/openwrt_release /rom/bin/busybox /rom" + database)
        hashes = [line.split()[0] for line in immutable.splitlines()]
        if len(hashes) != 3 or any(not re.fullmatch("[a-f0-9]{64}", value) for value in hashes):
            raise ValueError("Cannot verify immutable firmware identity")
        raw_addresses = self.run("hardware",
            "for f in /sys/class/ieee80211/phy*/macaddress /sys/class/net/eth*/address "
            "/sys/class/net/en*/address; do test ! -r \"$f\" || cat \"$f\"; done")
        addresses = sorted({hashlib.sha256(value.lower().encode("ascii")).hexdigest()
                            for value in raw_addresses.splitlines()
                            if re.fullmatch(r"(?:[a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}", value)
                            and value.lower() not in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}})
        if not addresses:
            raise ValueError("No stable hardware address; same-device restore cannot be verified")
        identity = dict(board=board["board_name"], model=board.get("model", ""),
                        release=release, kernel=self.run("kernel", "uname -r"),
                        firmware=hashes, hardware=addresses)
        return dict(manager=manager, packages=packages, builtin=builtin, identity=identity,
                    database_hash=hashlib.sha256(text.encode("utf-8")).hexdigest())

    def enrich(self, inventory, selected):
        if not selected:
            return selected
        for pkg in selected:
            if pkg.extended_attributes:
                raise ValueError("Cannot preserve package extended attributes: " + pkg.name
                                 + " (" + ", ".join(pkg.extended_attributes[:5]) + ")")
        if inventory["manager"] == "opkg":
            self._opkg_metadata(selected)
        else:
            self._apk_scripts(selected)
        return selected

    def configuration_ownership(self, inventory, packages):
        if inventory["manager"] != "opkg" or not packages:
            return
        from .snapshot_configuration import config_path
        blob = self.transfer.read("tar -czf - -C /usr/lib/opkg/info .", limit=16 * 1024 * 1024)
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            entries = tar_members(archive, size_limit=32 * 1024 * 1024)
            for pkg in packages:
                item = entries.get(pkg.name + ".list")
                if item is None or not item.isfile() or item.size > 8 * 1024 * 1024:
                    raise ValueError("Missing package configuration ownership: " + pkg.name)
                text = archive.extractfile(item).read().decode("utf-8")
                paths = (file_path(line.strip()) for line in text.splitlines()
                         if line.strip() not in {"", "/", "/."})
                pkg.files = tuple(path for path in paths if config_path(path))

    def _opkg_metadata(self, selected):
        blob = self.transfer.read("tar -czf - -C /usr/lib/opkg/info .", limit=16 * 1024 * 1024)
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            entries = tar_members(archive, size_limit=32 * 1024 * 1024)
            if sum(member.size for member in entries.values()) > 32 * 1024 * 1024:
                raise ValueError("Installed metadata exceeds memory limit")
            for pkg in selected:
                control = entries.get(pkg.name + ".control")
                if control is None or not control.isfile() or control.size > 1024 * 1024:
                    raise ValueError("Missing installed package control metadata: " + pkg.name)
                text = archive.extractfile(control).read().decode("utf-8")
                # The status database omits fields needed by native post-install hooks,
                # notably Require-User. Recover the retained original control record.
                records = parse_opkg(text.rstrip() + "\nStatus: install ok installed\n")
                original = records.get(pkg.name)
                if len(records) != 1 or original is None or original.version != pkg.version:
                    raise ValueError("Installed control identity mismatch: " + pkg.name)
                pkg.metadata = dict(original.metadata, **pkg.metadata)
                member = entries.get(pkg.name + ".list")
                if member is None or not member.isfile() or member.size > 8 * 1024 * 1024:
                    raise ValueError("Missing package file ownership: " + pkg.name)
                value = archive.extractfile(member).read().decode("utf-8")
                pkg.files = tuple(dict.fromkeys(file_path(line.strip()) for line in value.splitlines()
                                               if line.strip() not in {"", "/", "/."}))
                for suffix in (*SCRIPTS, "conffiles"):
                    member = entries.get(pkg.name + "." + suffix)
                    if member is None:
                        continue
                    if not member.isfile() or member.size > 1024 * 1024:
                        raise ValueError("Unsupported installed package script")
                    pkg.scripts[suffix] = archive.extractfile(member).read()

    def _apk_scripts(self, selected):
        by_prefix = {pkg.name + "-" + pkg.version: pkg for pkg in selected}
        by_digest = {}
        for pkg in selected:
            value = pkg.metadata.get("C", "")
            digest = self._apk_digest(value)
            if not digest:
                raise ValueError("Missing APK installed package identity: " + pkg.name)
            by_digest[digest] = pkg
        variant = self.run("scripts_probe",
            "if test -f /lib/apk/db/scripts.tar; then echo scripts.tar; "
            "elif test -f /lib/apk/db/scripts.tar.gz; then echo scripts.tar.gz; else echo none; fi")
        if variant not in {"scripts.tar", "scripts.tar.gz", "none"}:
            raise ValueError("Unknown APK script database")
        if variant != "none":
            blob = self.transfer.read("cat /lib/apk/db/" + variant)
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
                entries = tar_members(archive, size_limit=32 * 1024 * 1024)
                if sum(member.size for member in entries.values()) > 32 * 1024 * 1024:
                    raise ValueError("Installed scripts exceed memory limit")
                for name, member in entries.items():
                    parts = name.rsplit(".", 2)
                    if len(parts) != 3 or parts[0] not in by_prefix:
                        continue
                    pkg = by_prefix[parts[0]]
                    if (parts[2] not in APK_SCRIPTS or not member.isfile()
                            or member.size > 1024 * 1024):
                        raise ValueError("Unsupported APK installed script")
                    if self._apk_digest(parts[1]) != self._apk_digest(pkg.metadata["C"]):
                        continue
                    if parts[2] in pkg.scripts:
                        raise ValueError("Duplicate APK installed script")
                    pkg.scripts[parts[2]] = archive.extractfile(member).read()
        triggers = self.transfer.read(
            "if test -r /lib/apk/db/triggers; then cat /lib/apk/db/triggers; fi").decode("utf-8")
        for line in triggers.splitlines():
            fields = line.split()
            if not fields:
                continue
            pkg = by_digest.get(self._apk_digest(fields[0]))
            if pkg:
                pkg.triggers = tuple(fields[1:])
                if "trigger" not in pkg.scripts:
                    raise ValueError("APK trigger script is missing")

    @staticmethod
    def _apk_digest(value):
        try:
            if value.startswith(("Q1", "Q2")):
                return base64.b64decode(value[2:], validate=True).hex()
            if value.startswith(("X1", "X2")):
                value = value[2:]
            return value.lower() if re.fullmatch(r"[a-fA-F0-9]{40,64}", value) else ""
        except ValueError:
            return ""


def verify_identity(saved, current):
    keys = {"board", "model", "release", "kernel", "firmware", "hardware"}
    if set(saved) != keys or set(current) != keys:
        raise ValueError("Incomplete saved router identity")
    if any(saved[key] != current[key] for key in keys - {"hardware"}):
        raise ValueError("\u56fa\u4ef6\u4e0e\u5907\u4efd\u4e0d\u4e00\u81f4\uff0c\u672a\u6267\u884c\u6062\u590d\u3002")
    if (not isinstance(saved["hardware"], list) or not saved["hardware"]
            or not set(saved["hardware"]) & set(current["hardware"])):
        raise ValueError("\u65e0\u6cd5\u786e\u8ba4\u662f\u540c\u4e00\u53f0\u8def\u7531\u5668\uff0c\u672a\u6267\u884c\u6062\u590d\u3002")

