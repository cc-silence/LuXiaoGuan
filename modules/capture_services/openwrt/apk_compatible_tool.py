"""Extract a verified APK into an exclusive workspace without installing it."""
from dataclasses import dataclass
import hashlib
import re
import stat
import time
from uuid import uuid4
from . import apk_package_feed as feed
from .apk_metadata import read_apk_index
from .command_builder import quote_argument
from .command_runner import require_command_completion
from .compatible_store import check_cancel, optional_stat

LIMIT = 8 * 1024 * 1024

@dataclass(frozen=True)
class ApkToolPackage:
    name: str
    version: str
    filename: str
    sha256: str
    size: int
    url: str
    depends: tuple[str, ...]

def lookup_tool(profile, fetch, cancel):
    indexes = feed.download_adb_files(fetch, profile, cancel_event=cancel, feeds=("packages",))
    rows = [r for r in read_apk_index(indexes["packages"]) if r["name"] == "hcxdumptool"]
    if len(rows) != 1:
        raise ValueError("\u5b98\u65b9 APK \u6e90\u672a\u8fd4\u56de\u552f\u4e00\u7684 PMKID \u5de5\u5177\u3002")
    row = rows[0]
    if row["architecture"] != profile.package_arch or not 0 < row["size"] <= LIMIT:
        raise ValueError("APK \u5de5\u5177\u7684\u67b6\u6784\u6216\u5927\u5c0f\u4e0d\u5339\u914d\u3002")
    filename = feed.package_filename(row["name"], row["version"])
    digest = feed.fetch_sha256sums(fetch, profile, cancel).get(filename, "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("\u5b98\u65b9\u6821\u9a8c\u8868\u7f3a\u5c11 APK \u5de5\u5177\u6458\u8981\u3002")
    operators = {0: "", 1: "=", 2: "<", 3: "<=", 4: ">", 5: ">=", 8: "~", 9: "~"}
    dependencies = []
    for dep in row["depends"]:
        name, version, match = dep["name"], dep["version"], dep["match"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.:/-]*", name):
            raise ValueError("Invalid APK dependency name")
        flags = match & ~16
        if flags not in operators or (version and not re.fullmatch(r"[A-Za-z0-9_.+~:-]+", version)):
            raise ValueError("Unsupported APK dependency constraint")
        if bool(version) != bool(operators[flags]):
            raise ValueError("Incomplete APK dependency constraint")
        dependencies.append(("!" if match & 16 else "") + name + operators[flags] + version)
    base = feed.index_sources(profile, ("packages",))[0][1]
    return ApkToolPackage(row["name"], row["version"], filename, digest,
                          row["size"], base + filename, tuple(dependencies))

def unpack_tool(backend, blob, package, cancel):
    if len(blob) != package.size or hashlib.sha256(blob).hexdigest() != package.sha256:
        raise ValueError("APK \u5de5\u5177\u6821\u9a8c\u5931\u8d25\uff0c\u539f\u5de5\u5177\u672a\u66f4\u6539\u3002")
    ssh, runner = backend.ssh_client, backend.command_runner
    connection = ssh._client
    directory = "/tmp/routermanager-tool-" + uuid4().hex
    archive, payload = directory + "/package.apk", directory + "/payload"
    created, primary = False, None
    deadline = time.monotonic() + 120
    def execute(name, command, timeout, event):
        if ssh._client is not connection:
            raise InterruptedError("Connection changed during APK operation")
        if name != "compatible_apk_cleanup":
            check_cancel(cancel, deadline)
        result = runner.run(name, command, timeout, event)
        require_command_completion(result)
        if result.exit_code:
            raise RuntimeError("APK \u5de5\u5177\u5904\u7406\u672a\u5b8c\u6210\uff08" + name + "\uff09\u3002")
        return result.stdout
    try:
        check_cancel(cancel, deadline)
        for dep in package.depends:
            execute("compatible_dependency_check", "apk --no-network info --exists " + quote_argument(dep), 10, cancel)
        free = execute("compatible_apk_space", "df -Pk /tmp | tail -n 1 | awk '{print $4}'", 10, cancel).strip()
        if not free.isdigit() or int(free) < 2 * LIMIT // 1024 + 8192:
            raise RuntimeError("\u8def\u7531\u5668\u4e34\u65f6\u7a7a\u95f4\u4e0d\u8db3\uff0c\u672a\u89e3\u5305\u5de5\u5177\u3002")
        with ssh.transfer(cancel, timeout=30):
            sftp = ssh.open_sftp(timeout=10, cancel_event=cancel)
            sftp.get_channel().settimeout(10)
            if not stat.S_ISDIR(sftp.lstat("/tmp").st_mode) or sftp.normalize("/tmp") != "/tmp":
                raise ValueError("Unsafe temporary root")
            sftp.mkdir(directory, mode=0o700)
            created = True
            sftp.mkdir(payload, mode=0o700)
            with sftp.open(archive, "wx") as target:
                for offset in range(0, len(blob), 65536):
                    check_cancel(cancel, deadline)
                    target.write(blob[offset:offset + 65536])
                target.flush()
            sftp.chmod(archive, 0o600)
        remote_hash = execute("compatible_apk_hash", "sha256sum " + quote_argument(archive), 10, cancel).split()
        if remote_hash != [package.sha256, archive]:
            raise ValueError("Uploaded APK hash mismatch")
        execute("compatible_apk_verify", "apk --no-network verify " + quote_argument(archive), 15, cancel)
        execute("compatible_apk_extract", "apk --no-network extract --destination " + quote_argument(payload)
                + " " + quote_argument(archive), 30, cancel)
        with ssh.transfer(cancel, timeout=30):
            sftp = ssh.open_sftp(timeout=10, cancel_event=cancel)
            sftp.get_channel().settimeout(10)
            if ssh._client is not connection:
                raise InterruptedError("Connection changed during APK extraction")
            binaries = []
            for relative in ("usr/sbin/hcxdumptool", "usr/bin/hcxdumptool"):
                path = payload + "/" + relative
                info = optional_stat(sftp, path)
                if info is None:
                    continue
                if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= LIMIT
                        or sftp.normalize(path) != path):
                    raise ValueError("Invalid extracted APK binary")
                with sftp.open(path, "rb") as source:
                    binary = source.read(LIMIT + 1)
                check_cancel(cancel, deadline)
                if len(binary) != info.st_size or not binary.startswith(b"\x7fELF"):
                    raise ValueError("Truncated or non-ELF APK tool")
                binaries.append(binary)
            if len(binaries) != 1:
                raise ValueError("APK must contain exactly one tool binary")
            return binaries[0]
    except BaseException as error:
        primary = error
        raise
    finally:
        if created:
            try:
                if ssh._client is not connection or not ssh.connected:
                    raise RuntimeError("Extraction connection lost")
                execute("compatible_apk_cleanup", "rm -rf -- " + quote_argument(directory), 15, None)
            except Exception as cleanup:
                note = "\u4e34\u65f6\u89e3\u5305\u76ee\u5f55\u6e05\u7406\u672a\u786e\u8ba4\uff1a" + directory
                if primary is None:
                    raise RuntimeError(note) from cleanup
                primary.args = (str(primary) + "; " + note,)
