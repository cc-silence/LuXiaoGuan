"""Same-device installed-package backup and native offline restore, worker-only."""
import hashlib
import json
import re
import tempfile
import tarfile
from pathlib import Path
from threading import Event
from uuid import uuid4
from .command_builder import quote_argument as q
from .dependency_snapshot_transfer import SnapshotGuard, SnapshotTransfer
from .installed_dependency_reader import InstalledDependencyReader, SnapshotCommandFailure, verify_identity
from .installed_dependency_catalog import dependency_roots, dependency_closure, allowed_payload, restore_selection, _dep_name
from .dependency_snapshot_archive import (MAX_TOTAL, digest_file, rebuild_package, save_snapshot, load_snapshot)
from .dependency_snapshot_package_check import inspect_package
from .dependency_snapshot_plan import install_command, verify_restore_plan
from .dependency_snapshot_permissions import directory_metadata_command, parse_directory_metadata
from .snapshot_original_packages import SnapshotOriginalPackages, SkippableOriginalRecoveryError
from .snapshot_configuration import capture_configurations, restore_configurations, affected_configurations, stage_configurations
from modules.platform_services.local_file_publish import publish_new_file
from modules.capture_services.capture_logging import LOGGER


def _skip_impact(name):
    try:
        from .common_tool_dependencies import TOOLS
        from .other_tool_dependencies import OTHER_TOOLS
        for tool in (*TOOLS, *OTHER_TOOLS):
            if name in tool.package_names:
                label = tool.title
                if tool.key == 'luci_base_zh':
                    return label, '以后用这份备份恢复时，路由器自带网页可能有些地方变回英文，或者中文显示不完整。路小管自己的界面不受影响。'
                if tool.key == 'luci_commands_zh':
                    return label, '以后用这份备份恢复时，路由器网页里的“自定义命令”页面可能有些地方变回英文。路小管自己的界面不受影响。'
                if tool.key in {'luci_commands', 'other_terminal', 'other_files', 'other_uhttpd', 'other_upnp', 'other_dnscrypt', 'other_dnscrypt_ui', 'other_zerotier', 'other_zerotier_ui', 'other_mounts'}:
                    return label, '以后用这份备份恢复时，路由器上这个功能可能需要重新安装，恢复后才能正常使用。'
                if tool.key in {'iw', 'wpa_cli', 'iwinfo', 'curl', 'wireless_rpc', 'signal', 'bash'}:
                    return label, '以后用这份备份恢复时，路小管里依赖这个工具的相关功能可能不能正常使用，需要重新安装这个依赖。'
                return label, '以后用这份备份恢复时，这个依赖可能需要重新安装。'
    except Exception:
        pass
    capture_names = {
        'hcxdumptool': ('抓包工具 hcxdumptool', '以后用这份备份恢复时，抓握手包/PMKID 相关功能可能不能正常使用，需要重新安装。'),
        'hcxtools': ('抓包转换工具 hcxtools', '以后用这份备份恢复时，抓包结果转换相关功能可能不能正常使用，需要重新安装。'),
        'aircrack-ng': ('抓包工具 aircrack-ng', '以后用这份备份恢复时，握手包抓取相关功能可能不能正常使用，需要重新安装。'),
        'reaver': ('WPS 测试工具 reaver', '以后用这份备份恢复时，WPS 相关功能可能不能正常使用，需要重新安装。'),
        'pixiewps': ('WPS 测试工具 pixiewps', '以后用这份备份恢复时，WPS 相关功能可能不能正常使用，需要重新安装。'),
    }
    if name in capture_names:
        return capture_names[name]
    return name, '以后用这份备份恢复时，这个依赖可能需要重新安装，相关功能可能暂时不能用。'


class InstalledDependencyBackup:
    def __init__(self, runner, ssh, cancel, progress):
        self.guard = SnapshotGuard(ssh, cancel)
        self.transfer = SnapshotTransfer(self.guard, progress)
        self.reader = InstalledDependencyReader(runner, self.transfer, self.guard)
        self.progress = progress

    def backup(self, destination):
        destination = Path(destination)
        self.progress(dict(stage="\u6b63\u5728\u76d8\u70b9\u8def\u7531\u5668\u5df2\u5b89\u88c5\u4f9d\u8d56"))
        before = self.reader.inventory()
        roots = sorted(dependency_roots() & before["packages"].keys())
        selected = dependency_closure(before["packages"], roots, before["manager"])
        if not selected:
            raise ValueError("\u8def\u7531\u5668\u672a\u5b89\u88c5\u53ef\u5907\u4efd\u7684\u8def\u5c0f\u7ba1\u4f9d\u8d56\u3002")
        skipped_packages = []
        remote = self._workspace()
        try:
            while True:
                addons = [pkg for pkg in selected if pkg.name not in before["builtin"]
                          or pkg.version != before["builtin"][pkg.name].version]
                self.reader.enrich(before, addons)
                builtin = [pkg for pkg in selected if pkg not in addons]
                self.reader.configuration_ownership(before, builtin)
                from .snapshot_configuration import config_path
                paths = {path for pkg in builtin for path in pkg.files if config_path(path)}
                for pkg in addons:
                    for path in (*pkg.files, *pkg.directories):
                        # The root filesystem is never captured as a directory tree.
                        if not allowed_payload(path):
                            # Runtime directories are not package payload; a protected file is an error.
                            kind = self.reader.run("protected_type", "if test -d " + q("/" + path)
                                                   + " && test ! -L " + q("/" + path) + "; then echo directory; else echo file; fi")
                            if kind != "directory":
                                raise ValueError("Package owns a protected path; backup stopped: " + path)
                            continue
                        paths.add(path)
                    # Verified runtime directories are not installable package payload.
                    pkg.files = tuple(path for path in pkg.files if allowed_payload(path))
                    pkg.directories = tuple(path for path in pkg.directories if allowed_payload(path))
                try:
                    with tempfile.TemporaryDirectory(prefix="luxiaoguan-dependencies-", dir=destination.parent) as directory:
                        local = Path(directory)
                        payload = local / "payload.tgz"
                        if paths:
                            with SnapshotOriginalPackages(self, before, addons, remote, local) as originals:
                                directories = self._capture(remote, sorted(paths), payload, local,
                                                            recover=originals.recover)
                                provenance = dict(originals.provenance)
                        else:
                            directories = {}
                            provenance = {}
                            with tarfile.open(payload, "w:gz"):
                                pass
                        settings = capture_configurations(payload, selected, local, self.guard.check)
                        rows = []
                        for position, pkg in enumerate(selected):
                            self.guard.check()
                            self.progress(dict(stage="\u6b63\u5728\u5907\u4efd\u4f9d\u8d56\uff1a" + pkg.name,
                                               completed=position, total=len(selected)))
                            is_builtin = pkg not in addons
                            row = dict(name=pkg.name, version=pkg.version, metadata=pkg.metadata, builtin=is_builtin)
                            if pkg.name in settings:
                                row["configuration"] = settings[pkg.name]
                            if pkg.name in provenance:
                                row["original_source"] = provenance[pkg.name]
                            if not is_builtin:
                                output = local / (pkg.name + "." + before["manager"])
                                row["installed_size"] = rebuild_package(pkg, before["manager"], payload, output, self.guard.check,
                                                                          directory_metadata=directories)
                                inspect_package(output, before["manager"], row)
                                row.update(file="packages/" + pkg.name + (".ipk" if before["manager"] == "opkg" else ".apk"),
                                           size=output.stat().st_size, sha256=digest_file(output, self.guard.check))
                            rows.append(row)
                        after = self.reader.inventory()
                        self._unchanged(before, after)
                        manifest = dict(format="luxiaoguan-installed-dependencies", schema=2 if settings else 1, manager=before["manager"],
                                        identity=before["identity"], roots=roots, packages=rows)
                        archive = local / "dependencies.zip"
                        save_snapshot(archive, manifest, local, self.guard.check)
                        self.guard.check()
                        publish_new_file(archive, destination)
                        return dict(action="backup", path=str(destination), count=len(rows), stored=len(addons),
                                    skipped_packages=skipped_packages)
                except SkippableOriginalRecoveryError as exc:
                    names = {item["name"] for item in exc.packages}
                    removed = [pkg for pkg in selected if pkg.name in names]
                    if not removed:
                        raise
                    for pkg in removed:
                        self.progress(dict(stage="\u5df2\u8df3\u8fc7\u65e0\u6cd5\u5907\u4efd\u7684\u4f9d\u8d56\uff1a" + pkg.name))
                    for item in exc.packages:
                        if item["name"] not in {p["name"] for p in skipped_packages}:
                            label, impact = _skip_impact(item["name"])
                            skipped_packages.append(dict(name=item["name"], version=item["version"],
                                                         reason=item["reason"], label=label, impact=impact))
                    excluded = {item["name"] for item in skipped_packages}
                    selected = [pkg for pkg in selected if pkg.name not in excluded]
                    roots = [r for r in roots if r not in excluded]
                    # Also drop packages whose required dependencies are no longer satisfiable.
                    providers = {}
                    for pkg in before["packages"].values():
                        value = pkg.metadata.get("Provides" if before["manager"] == "opkg" else "p", "")
                        for token in value.split(",") if before["manager"] == "opkg" else value.split():
                            if token.strip():
                                providers.setdefault(_dep_name(token, before["manager"]), set()).add(pkg.name)
                    changed = True
                    while changed:
                        changed = False
                        selected_names = {pkg.name for pkg in selected}
                        keep = []
                        for pkg in selected:
                            value = pkg.metadata.get("Depends" if before["manager"] == "opkg" else "D", "")
                            if before["manager"] == "opkg" and pkg.metadata.get("Pre-Depends"):
                                value += (", " if value else "") + pkg.metadata["Pre-Depends"]
                            ok = True
                            for expression in value.split(",") if before["manager"] == "opkg" else value.split():
                                if not expression.strip() or expression.strip().startswith("!"):
                                    continue
                                candidates = set()
                                for option in expression.split("|"):
                                    dep = _dep_name(option, before["manager"])
                                    candidates.update({dep} if dep in before["packages"] else providers.get(dep, ()))
                                if candidates and not (candidates & selected_names):
                                    ok = False
                                    break
                            if ok:
                                keep.append(pkg)
                            else:
                                changed = True
                                if pkg.name not in excluded:
                                    label, _ = _skip_impact(pkg.name)
                                    skipped_packages.append(dict(
                                        name=pkg.name, version=pkg.version,
                                        reason='它需要的另一个依赖没法安全备份，所以这个依赖也不能单独备份。',
                                        label=label,
                                        impact='它需要的另一个依赖没法安全备份，所以这个依赖也一起跳过了。以后用这份备份恢复时，可能需要重新安装。'))
                                    excluded.add(pkg.name)
                                    self.progress(dict(stage="\u5df2\u8df3\u8fc7\u65e0\u6cd5\u5907\u4efd\u7684\u4f9d\u8d56\uff1a" + pkg.name))
                        selected = keep
                        roots = [r for r in roots if r not in excluded]
                    try:
                        selected = dependency_closure(before["packages"], set(roots), before["manager"])
                        selected = [pkg for pkg in selected if pkg.name not in excluded]
                    except ValueError as err:
                        if "Installed dependency is missing:" not in str(err):
                            raise
                    if not selected:
                        labels = '、'.join((item.get('label') or item['name']) for item in skipped_packages)
                        raise ValueError('这些依赖都没法安全备份，所以这次没有生成备份文件：' + labels + '。')
                    continue
        finally:
            self._cleanup(remote)

    def preview(self, source):
        self.progress(dict(stage="\u6b63\u5728\u6821\u9a8c\u5907\u4efd\u548c\u8def\u7531\u5668"))
        source_digest = digest_file(source, self.guard.check)
        with tempfile.TemporaryDirectory(prefix="luxiaoguan-restore-") as directory:
            manifest = load_snapshot(source, Path(directory), self.guard.check)
            self._inspect_packages(manifest, Path(directory))
        inventory = self.reader.inventory()
        self._compatible(manifest, inventory)
        missing, skipped = restore_selection(manifest["packages"], inventory["packages"])
        if digest_file(source, self.guard.check) != source_digest:
            raise ValueError("\u5907\u4efd\u6587\u4ef6\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9\u3002")
        return dict(action="preview", source=str(source), sha256=source_digest,
                    database_hash=inventory["database_hash"], identity=inventory["identity"],
                    manager=inventory["manager"], missing=missing, skipped=skipped,
                    count=len(manifest["packages"]))

    def restore(self, review):
        # Hash again before and after unpacking, preventing a replaced selection from being installed.
        if digest_file(review["source"], self.guard.check) != review["sha256"]:
            raise ValueError("\u5907\u4efd\u6587\u4ef6\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9\u3002")
        with tempfile.TemporaryDirectory(prefix="luxiaoguan-restore-") as directory:
            local = Path(directory)
            manifest = load_snapshot(review["source"], local, self.guard.check)
            sizes = self._inspect_packages(manifest, local)
            if digest_file(review["source"], self.guard.check) != review["sha256"]:
                raise ValueError("Backup changed during verification")
            before = self.reader.inventory()
            self._compatible(manifest, before)
            if before["database_hash"] != review["database_hash"] or before["manager"] != review["manager"]:
                raise ValueError("\u4f9d\u8d56\u72b6\u6001\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u9884\u89c8\u3002")
            missing, skipped = restore_selection(manifest["packages"], before["packages"])
            if not missing:
                return dict(action="restore", installed=(), skipped=skipped, complete=True)
            configuration_rows = affected_configurations(manifest["packages"], missing,
                                                         before["packages"], before["manager"])
            remote = self._workspace()
            committed = False
            install_pending = False
            try:
                selected = [row for row in manifest["packages"] if row["name"] in missing]
                temporary = sum(row["size"] for row in selected) + sum(row.get("configuration", {}).get("size", 0) for row in manifest["packages"]) + 2 * 1024 * 1024
                installed = sum(sizes[name] for name in missing)
                self._check_space("/tmp", temporary + installed)
                self._check_space("/", int(installed * 1.1) + len(missing) * 65536 + 1024 * 1024)
                paths = []
                for row in selected:
                    name = row["name"]
                    path = remote + "/" + name + (".ipk" if before["manager"] == "opkg" else ".apk")
                    self.transfer.upload(local / (name + "." + before["manager"]), path)
                    self._verify_remote(path, row["size"], row["sha256"])
                    paths.append(path)
                stage_configurations(self, configuration_rows, local, remote)
                if before["manager"] == "opkg":
                    self._opkg_config(remote, local)
                wanted = {row["name"]:row["version"] for row in selected}
                plan = self.reader.run("restore_plan", install_command(before["manager"], remote, paths, simulate=True), 180)
                verify_restore_plan(before["manager"], plan, wanted)
                self._unchanged(before, self.reader.inventory())
                self.guard.check()
                self.progress(dict(stage="\u6b63\u5728\u79bb\u7ebf\u6062\u590d\u4f9d\u8d56", uncancellable=True))
                # Do not interrupt a package-manager write. Application shutdown still waits for its worker.
                committed = True
                original = self.guard.cancel
                self.guard.cancel = Event()
                try:
                    install_pending = True
                    try:
                        self.reader.run("restore_install", install_command(before["manager"], remote, paths), 300)
                    except SnapshotCommandFailure as error:
                        install_pending = not error.exit_confirmed
                        raise
                    install_pending = False
                    after = self.reader.inventory()
                    self._verify_restored(before, after, manifest)
                    restore_configurations(self, configuration_rows, local, remote)
                finally:
                    self.guard.cancel = original
                return dict(action="restore", installed=missing, skipped=skipped, complete=True)
            except Exception as exc:
                if committed:
                    retained = ("\u5b89\u88c5\u8fdb\u7a0b\u662f\u5426\u7ed3\u675f\u5c1a\u672a\u786e\u8ba4\uff0c\u5df2\u4fdd\u7559\u4e34\u65f6\u4f9d\u8d56\u6587\u4ef6\uff0c"
                                "\u907f\u514d\u5f71\u54cd\u4ecd\u5728\u8fdb\u884c\u7684\u5b89\u88c5\uff1b\u8bf7\u52ff\u7acb\u5373\u91cd\u590d\u5b89\u88c5\u3002 "
                                if install_pending else "")
                    raise RuntimeError("\u6062\u590d\u5df2\u5f00\u59cb\uff0c\u4f46\u672a\u786e\u8ba4\u5168\u90e8\u5b8c\u6210\u3002"
                                       "\u8bf7\u91cd\u65b0\u767b\u5f55\u68c0\u67e5\uff1b\u82e5\u6709\u5b89\u88c5\u5f02\u5e38\uff0c\u8bf7\u5148\u4fee\u590d\u540e\u518d\u6062\u590d\u3002 " + retained + str(exc)) from exc
                raise
            finally:
                if install_pending:
                    LOGGER.warning("Dependency restore exit unconfirmed; retaining private workspace %s", remote)
                else:
                    self._cleanup(remote)

    @staticmethod
    def _verify_restored(before, after, manifest):
        # APK can retain an installed version while marking its files or hooks
        # broken. A version match alone is not a completed restoration.
        restore_selection(manifest["packages"], after["packages"])
        expected = {row["name"]:(before["packages"][row["name"]].version
                    if row["name"] in before["packages"] else row["version"])
                    for row in manifest["packages"]}
        wrong = [name for name, version in expected.items()
                 if name not in after["packages"] or after["packages"][name].version != version]
        changed = [name for name,pkg in before["packages"].items()
                   if name not in after["packages"] or after["packages"][name].version != pkg.version]
        if wrong or changed:
            raise ValueError("Post-install verification failed: " + ", ".join(sorted(set(wrong + changed))))

    def _capture(self, remote, paths, payload, local, *, recover=None):
        lists = local / "paths"
        lists.write_text("".join("/" + path + "\n" for path in paths), encoding="utf-8", newline="\n")
        self.transfer.upload(lists, remote + "/paths")
        self.progress(dict(stage="\u6b63\u5728\u4ece\u8def\u7531\u5668\u6536\u96c6\u5df2\u5b89\u88c5\u6587\u4ef6"))
        # No recursive directory inclusion, even when a package owns /usr or /etc.
        script = ("set -e; : > " + q(remote + "/files") + "; total=0; "
                  "while IFS= read -r p; do "
                  "if test -L \"$p\"; then printf '%s\\n' \"${p#/}\" >> " + q(remote + "/files") + "; "
                  "elif test -f \"$p\"; then n=$(wc -c < \"$p\"); total=$((total+n)); "
                  "printf '%s\\n' \"${p#/}\" >> " + q(remote + "/files") + "; "
                  "elif test -d \"$p\"; then :; "
                  "else printf 'missing\\t%s\\n' \"$p\"; fi; "
                  "test \"$total\" -le " + str(MAX_TOTAL) + " || exit 47; done < " + q(remote + "/paths")
                  + "; printf '%s\\n' \"$total\"")
        result = self.reader.run("files", script, 120)
        lines = result.splitlines()
        missing = [line.partition("\t")[2] for line in lines if line.startswith("missing\t")]
        recovered = None
        if missing:
            if recover is None:
                raise ValueError("Missing package payload; backup not created: " + ", ".join(missing))
            if any(path.removeprefix("/") not in paths for path in missing):
                raise ValueError("Unexpected missing package path")
            recovered = recover(missing)
        total = int(lines[-1]) if lines and not lines[-1].startswith("missing\t") else 0
        self._check_space("/tmp", total + 2 * 1024 * 1024)
        self.reader.run("capture", "ulimit -f 1048576 || exit 48; tar -czf " + q(remote + "/payload.tgz")
                        + " -C / -T " + q(remote + "/files"), 180)
        size = int(self.reader.run("archive_size", "wc -c < " + q(remote + "/payload.tgz")))
        digest = self.reader.run("archive_hash", "sha256sum " + q(remote + "/payload.tgz")).split()[0]
        self.transfer.download(remote + "/payload.tgz", payload, size, digest)
        if recovered is not None:
            self._merge_originals(payload, recovered, missing)
        parents = set(paths)
        for path in paths:
            parts = path.split("/")
            parents.update("/".join(parts[:i]) for i in range(1, len(parts)))
        directory_list = local / "directories"
        directory_list.write_text("".join("/" + path + "\n" for path in sorted(parents)), encoding="utf-8", newline="\n")
        self.transfer.upload(directory_list, remote + "/directories")
        output = self.reader.run("directory_metadata", directory_metadata_command(remote + "/directories"), 60)
        return parse_directory_metadata(output, parents)

    def _merge_originals(self, payload, recovered, missing):
        from .dependency_snapshot_archive import tar_members
        expected = {path.removeprefix("/") for path in missing}
        merged = payload.with_suffix(".merged")
        with tarfile.open(payload, "r:gz") as current, tarfile.open(recovered, "r:gz") as original:
            current_entries = tar_members(current, payload=True)
            original_entries = tar_members(original, payload=True)
            if set(original_entries) != expected or expected & current_entries.keys():
                raise ValueError("Recovered original file set mismatch")
            total = sum(m.size for m in (*current_entries.values(), *original_entries.values()))
            if total > MAX_TOTAL:
                raise ValueError("Dependency payload exceeds size limit")
            with tarfile.open(merged, "w:gz", format=tarfile.PAX_FORMAT) as target:
                for source, entries in ((current, current_entries), (original, original_entries)):
                    for member in entries.values():
                        self.guard.check()
                        if member.isfile() or member.islnk():
                            with source.extractfile(member) as stream:
                                target.addfile(member, stream)
                        else:
                            target.addfile(member)
        merged.replace(payload)

    def _opkg_config(self, remote, local):
        architectures = self.reader.run("architectures", "opkg print-architecture").splitlines()
        if not architectures or any(not re.fullmatch(r"arch [A-Za-z0-9_.-]+ [0-9]+", line) for line in architectures):
            raise ValueError("Cannot determine native package architectures")
        config = local / "opkg.conf"
        config.write_text("dest root /\nlists_dir ext " + remote + "/empty\n" +
                          "\n".join(architectures) + "\n", encoding="utf-8", newline="\n")
        self.reader.run("empty_sources", "mkdir " + q(remote + "/empty"))
        self.transfer.upload(config, remote + "/opkg.conf")

    def _verify_remote(self, path, size, digest):
        result = self.reader.run("uploaded_hash", "wc -c < " + q(path) + "; sha256sum " + q(path))
        values = result.split()
        if len(values) < 2 or values[0] != str(size) or values[1] != digest:
            raise ValueError("Uploaded dependency package checksum mismatch")

    def _check_space(self, path, size):
        output = self.reader.run("space", "df -Pk " + q(path))
        lines = output.splitlines()
        if len(lines) < 2:
            raise ValueError("Cannot read router free space")
        fields = lines[-1].split()
        if len(fields) < 4 or not fields[3].isdigit() or int(fields[3]) * 1024 < size:
            raise ValueError("\u8def\u7531\u5668\u7a7a\u95f4\u4e0d\u8db3\uff0c\u672a\u5f00\u59cb\u5b89\u88c5\u3002")

    def _workspace(self):
        remote = "/tmp/luxiaoguan-dependencies-" + uuid4().hex
        self.reader.run("workspace", "umask 077; mkdir " + q(remote))
        return remote

    def _cleanup(self, remote):
        if not re.fullmatch(r"/tmp/luxiaoguan-dependencies-[a-f0-9]{32}", remote):
            raise ValueError("Invalid private dependency workspace")
        try:
            original = self.guard.cancel
            deadline = self.guard.deadline
            self.guard.cancel = Event()
            self.guard.deadline = __import__("time").monotonic() + 15
            try:
                self.guard.check()
                self.reader.run("cleanup", "test ! -L " + q(remote) + " && rm -rf " + q(remote), 15)
            finally:
                self.guard.cancel = original
                self.guard.deadline = deadline
        except Exception:
            LOGGER.warning("Dependency snapshot temporary files not confirmed removed; reboot clears /tmp")

    @staticmethod
    def _compatible(manifest, inventory):
        if manifest["manager"] != inventory["manager"]:
            raise ValueError("Package manager differs from the dependency backup")
        verify_identity(manifest["identity"], inventory["identity"])

    @staticmethod
    def _unchanged(before, after):
        verify_identity(before["identity"], after["identity"])
        if before["manager"] != after["manager"] or before["database_hash"] != after["database_hash"]:
            raise ValueError("Installed package inventory changed; please retry")

    def _inspect_packages(self, manifest, local):
        sizes = {}
        for row in manifest["packages"]:
            self.guard.check()
            if not row["builtin"]:
                sizes[row["name"]] = inspect_package(local / (row["name"] + "." + manifest["manager"]),
                                                     manifest["manager"], row)
        return sizes

