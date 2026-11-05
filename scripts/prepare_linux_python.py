"""Build the agreed private Python on Debian 12 without changing system Python."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.linux_build_process import run_build

VERSION = "3.10.22"
SOURCE_SHA256 = "c61fe9b1a1a7ec2a0b0388e664187c378a5325a89e3606d220abf5bdfccb2543"
SOURCE_URL = "https://www.python.org/ftp/python/3.10.22/Python-3.10.22.tar.xz"
SOURCE_ROOT = "Python-" + VERSION
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
BUILD_PACKAGES = ["build-essential", "pkg-config", "libssl-dev", "zlib1g-dev",
                  "libbz2-dev", "libreadline-dev", "libsqlite3-dev", "libffi-dev",
                  "liblzma-dev", "libgdbm-dev", "uuid-dev"]
PROBE = r"""
import bz2, ctypes, ensurepip, json, lzma, os, sqlite3, ssl, sys, sysconfig, venv, zlib
assert sys.version_info[:3] == (3,10,22)
assert os.path.realpath(sys.prefix) == os.path.realpath(sys.argv[1])
assert sysconfig.get_config_var('Py_ENABLE_SHARED') == 1
print(json.dumps({'python':sys.version.split()[0], 'openssl':ssl.OPENSSL_VERSION,
                  'shared_library':True, 'prefix':sys.prefix}))
"""


def admit_host():
    if not sys.platform.startswith("linux") or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("Build the private interpreter on Debian 12 x86-64")
    release = platform.freedesktop_os_release()
    if release.get("ID") != "debian" or release.get("VERSION_ID") != "12":
        raise ValueError("This private-runtime build profile requires Debian 12")


def safe_workspace_path(root, path):
    root, path = Path(root).resolve(), Path(path).absolute()
    if not path.resolve().is_relative_to(root / "build"):
        raise ValueError("Runtime preparation must stay inside this project's build directory")
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Runtime preparation does not follow directory links")
    return path


def verified_source(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= MAX_SOURCE_BYTES:
        raise ValueError("Provide the bounded official Python source archive")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != SOURCE_SHA256:
        raise ValueError("Python source differs from the pinned official SHA-256")
    return path


def extract_source(source, destination):
    # Validate every member before the first extracted byte. No tarfile.extractall.
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Refusing to overwrite a source staging directory")
    with tarfile.open(source, "r:xz") as archive:
        members, names, total = [], set(), 0
        for member in archive:
            parts = member.name.split("/")
            if (len(members) >= 30000 or not parts or parts[0] != SOURCE_ROOT
                    or any(part in {"", ".", ".."} for part in parts)
                    or "\\" in member.name or not (member.isfile() or member.isdir())
                    or member.name in names or member.size < 0):
                raise ValueError("Unsafe or excessive Python source member")
            names.add(member.name); total += member.size
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("Python source exceeds the extraction limit")
            members.append(member)
        if SOURCE_ROOT + "/configure" not in names:
            raise ValueError("Python source configure entry is missing")
        destination.mkdir(parents=True)
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as incoming, target.open("xb") as output:
                shutil.copyfileobj(incoming, output, 1024 * 1024)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    return destination / SOURCE_ROOT


def build_private_python(source, *, root=ROOT, configure_timeout=900):
    admit_host()  # Bootstrap may use Debian's system Python; it never replaces it.
    if type(configure_timeout) is not int or not 60 <= configure_timeout <= 1800:
        raise ValueError("Configure timeout must be between 60 and 1800 seconds")
    root = Path(root).resolve()
    source = verified_source(source)
    area = safe_workspace_path(root, root / "build/debian12/python-runtime")
    stage = safe_workspace_path(root, area / uuid.uuid4().hex)
    prefix = stage / "prefix"
    if os.name == "posix" and any(char.isspace() or char in "\"'`$;\\" for char in str(stage)):
        raise ValueError("Use a build workspace without whitespace or shell expansion characters")
    stage.mkdir(parents=True)
    report = dict(schema=1, status="building", version=VERSION, source_sha256=SOURCE_SHA256,
                  source_url=SOURCE_URL, prefix=str(prefix), system_python_changed=False,
                  application_validated=False, configure_timeout_seconds=configure_timeout)
    try:
        staged_source = stage / "source.tar.xz"
        with source.open("rb") as incoming, staged_source.open("xb") as output:
            shutil.copyfileobj(incoming, output, 1024 * 1024)
        verified_source(staged_source)
        source_tree = extract_source(staged_source, stage / "source")
        build = stage / "compile"; build.mkdir()
        env = {key:value for key,value in os.environ.items()
               if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
        # Use Debian tools only; inherited WSL Windows paths are outside this profile.
        env["PATH"] = os.defpath
        env["LDFLAGS"] = "-Wl,-rpath," + str(prefix / "lib")
        # The private build environment has a fixed prefix. Distribution uses the
        # frozen application builder; this prefix is not a relocatable installer.
        run_build([str(source_tree / "configure"), "--prefix=" + str(prefix),
                   "--enable-shared", "--with-ensurepip=install"], build, "configure", configure_timeout, env)
        run_build(["make", "-j2"], build, "compile", 1800, env)
        run_build(["make", "altinstall"], build, "altinstall", 300, env)
        interpreter = prefix / "bin/python3.10"
        run_build([str(interpreter), "-I", "-c", PROBE, str(prefix)], build, "verify", 30, env)
        raw = (build / "verify.log").read_bytes()
        if len(raw) > 65536:
            raise ValueError("Private runtime probe exceeded its limit")
        report.update(status="private-runtime-verified", probe=json.loads(raw), executable=str(interpreter))
        # Publish a new pointer only after every native module check succeeded.
        temporary = area / ("current." + uuid.uuid4().hex + ".part")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                json.dump(report, output, indent=2); output.write("\n")
                output.flush(); os.fsync(output.fileno())
            os.replace(temporary, area / "current.json")
        finally:
            temporary.unlink(missing_ok=True)
        return report
    except BaseException as exc:
        report.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--configure-timeout", type=int, default=900,
                        help="Bounded configuration budget in seconds (60-1800)")
    parser.add_argument("--plan", action="store_true", help="Read-only profile; no download or build")
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps(dict(version=VERSION, source_url=SOURCE_URL, sha256=SOURCE_SHA256,
            build_packages=BUILD_PACKAGES, system_python_changed=False, native_build_validated=False), indent=2))
        return 0
    if args.source is None:
        parser.error("--source is required for a native build")
    print(json.dumps(build_private_python(args.source, configure_timeout=args.configure_timeout), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
