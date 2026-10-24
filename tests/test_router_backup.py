"""Offline maintenance tests use real archives and a deterministic device command adapter."""
import io
import json
import shutil
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.backup_archive import inspect_archive, parent_guard, file_digest
from modules.capture_services.openwrt.router_backup import RouterBackupManager
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


def archive_bytes(entries=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, value in (entries or [("etc/config/network", b"original-network"), ("etc/shadow", b"private-secret")]):
            member = tarfile.TarInfo(name)
            if isinstance(value, tuple):
                member.type = tarfile.SYMTYPE
                member.linkname = value[0]
                archive.addfile(member)
            else:
                member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
    return stream.getvalue()


class Device:
    def __init__(self, root, version="25.12"):
        self.root = Path(root)
        self.version = version
        self.reset = "/sbin/factoryreset" if version.startswith("25") else "/sbin/jffs2reset"
        self.token = "a" * 64
        self.archive = archive_bytes(([
            ("etc/uci-defaults/10_disable_services", b"exit 0"),
            ("etc/config/network", b"native-apk-config")] if version.startswith("25") else None))
        self.commands = []
        self.remote = {}
        self.corrupt_download = False
        self.apply_error = False
        self.backup_error = False
        self.listing_mismatch = False
        self.manager = SimpleNamespace(runner=self, transfer_file=self.transfer,
            ssh=SimpleNamespace(connected=True,_client=object(),host_fingerprint="pinned"))
    def run(self, name, command, timeout, cancel_event=None):
        self.commands.append((name, command))
        action = name.removeprefix("maintenance_")
        text = ""
        code = 0
        if action == "probe":text = self.reset + "\n" + self.token + " /etc/config/network"
        elif action == "board":text = json.dumps(dict(model="Router A", board_name="vendor,router", release=dict(distribution="OpenWrt", version=self.version)))
        elif action == "backup":
            if self.backup_error:code = 1
            else:
                import re
                path = re.search(r"sysupgrade -b (\S+)", command)[1].strip("'")
                self.remote[path] = self.archive
        elif action == "digest":
            import hashlib, shlex
            text = hashlib.sha256(self.remote[shlex.split(command)[1]]).hexdigest() + " archive"
        elif action == "cleanup":
            self.remote.clear()
        elif action == "restore_check":
            path = next(p for p in self.remote if p.endswith("/restore.tar.gz"))
            with tarfile.open(fileobj=io.BytesIO(self.remote[path]),mode="r:gz") as archive:
                text="\n".join(m.name for m in archive)
            if self.listing_mismatch:text += "\netc/unexpected"
        elif action == "space":text = "999999"
        elif action == "apply" and self.apply_error:raise OSError("Transport ended")
        return RemoteCommandResult(name, code, text)
    def transfer(self, upload, local, remote, cancel, progress):
        if cancel.is_set():raise InterruptedError("cancelled")
        if upload:self.remote[remote] = Path(local).read_bytes()
        else:Path(local).write_bytes(b"corrupt" if self.corrupt_download else self.remote[remote])


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.tar.gz"
        self.event = threading.Event()
    def test_old_and_new_native_relative_names_and_safe_service_links(self):
        for prefix in ("", "./"):
            self.path.write_bytes(archive_bytes([(prefix+"etc/config/network", b"x"),
                (prefix+"etc/rc.d/S10network", ("../init.d/network",))]))
            info = inspect_archive(self.path,self.event)
            self.assertEqual(info["entries"],2)
            self.assertIn("/etc/config/network",parent_guard(info["records"]))
    def test_unsafe_paths_types_links_and_duplicate_members_are_rejected(self):
        cases = [[("../etc/shadow",b"x")], [("/etc/config/network",b"x")],
                 [("etc/config/network",b"x"),("etc/config/network",b"y")],
                 [("etc/config/network",b"x"),("etc/escape",("../../tmp",))],
                 [("etc/config/network",b"x"),("etc/alias",("config",)),("etc/alias/file",b"x")],
                 [("etc/config/network",b"x"),("usr/bin/tool",b"x")]]
        for entries in cases:
            self.path.write_bytes(archive_bytes(entries))
            with self.subTest(entries=entries),self.assertRaises(ValueError):
                inspect_archive(self.path,self.event)
    def test_hidden_members_after_tar_padding_are_also_checked(self):
        import gzip
        valid=gzip.decompress(archive_bytes())
        unsafe=gzip.decompress(archive_bytes([("../outside",b"unsafe")]))
        self.path.write_bytes(gzip.compress(valid+unsafe))
        with self.assertRaises(ValueError):
            inspect_archive(self.path,self.event)

    def test_special_files_and_hardlinks_are_rejected_without_extraction(self):
        for kind in (tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.LNKTYPE):
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode="w:gz") as archive:
                config=tarfile.TarInfo("etc/config/network");config.size=1
                archive.addfile(config,io.BytesIO(b"x"))
                member=tarfile.TarInfo("etc/extra");member.type=kind
                member.linkname="etc/config/network"
                archive.addfile(member)
            self.path.write_bytes(stream.getvalue())
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                inspect_archive(self.path,self.event)
        self.assertEqual(list(Path(self.temp.name).iterdir()),[self.path])

    def test_archive_entry_and_digest_limits_are_enforced(self):
        self.path.write_bytes(archive_bytes())
        with patch("modules.capture_services.openwrt.backup_archive.MAX_ENTRIES",1):
            with self.assertRaises(ValueError):inspect_archive(self.path,self.event)
        with patch("modules.capture_services.openwrt.backup_archive.MAX_ARCHIVE",1):
            with self.assertRaises(ValueError):file_digest(self.path,self.event)

    def test_corrupt_oversized_and_cancelled_inspection(self):
        self.path.write_bytes(b"invalid")
        with self.assertRaises(ValueError):inspect_archive(self.path,self.event)
        self.path.write_bytes(archive_bytes())
        with patch("modules.capture_services.openwrt.backup_archive.MAX_EXPANDED",100):
            with self.assertRaises(ValueError):inspect_archive(self.path,self.event)
        self.event.set()
        with self.assertRaises(InterruptedError):inspect_archive(self.path,self.event)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.tar.gz"
        self.source.write_bytes(archive_bytes())
        self.event = threading.Event()
        self.device = Device(self.root)
        self.manager = RouterBackupManager(self.device.manager)
        self.destination = self.root / "backup.tar.gz"
    def test_native_backup_and_verified_download_for_all_official_series(self):
        for version in ("19.07","21.02","22.03","23.05","24.10","25.12"):
            device = Device(self.root,version)
            destination = self.root / (version+".tar.gz")
            result = RouterBackupManager(device.manager).export(destination,None,self.event,Mock())
            self.assertEqual(destination.read_bytes(),device.archive)
            self.assertEqual(file_digest(destination,self.event),result["sha256"])
            self.assertFalse(device.remote)
            self.assertTrue(any("/sbin/sysupgrade -b " in c for n,c in device.commands))
            self.assertFalse(any(n=="maintenance_apply" for n,c in device.commands))
    def test_corrupt_download_preserves_existing_local_backup(self):
        self.destination.write_bytes(b"keep-old")
        self.device.corrupt_download = True
        from modules.capture_services.openwrt.router_management import RouterManagement
        expected = RouterManagement.identity(self.destination.stat())
        with self.assertRaises(ValueError):
            self.manager.export(self.destination,expected,self.event,Mock())
        self.assertEqual(self.destination.read_bytes(),b"keep-old")
        self.assertFalse(self.device.remote)
    def test_restore_saves_local_backup_before_native_restore_and_reboot(self):
        review = self.manager.preview("restore",self.source,self.event)
        result = self.manager.execute(review,self.destination,None,self.event,Mock())
        self.assertEqual(self.destination.read_bytes(),self.device.archive)
        names = [n for n,c in self.device.commands]
        self.assertLess(names.index("maintenance_backup"),names.index("maintenance_apply"))
        apply = next(c for n,c in self.device.commands if n=="maintenance_apply")
        self.assertIn("/sbin/sysupgrade -r ",apply)
        self.assertNotIn(" -F ",apply)
        self.assertIn("maintenance_reboot",names)
        self.assertEqual(result["outcome"],"reconnect")
    def test_backup_failure_never_starts_restore(self):
        review = self.manager.preview("restore",self.source,self.event)
        self.device.backup_error=True
        with self.assertRaises(RuntimeError):
            self.manager.execute(review,self.destination,None,self.event,Mock())
        self.assertFalse(any(n=="maintenance_apply" for n,c in self.device.commands))
    def test_changed_source_and_device_are_rejected(self):
        review = self.manager.preview("restore",self.source,self.event)
        self.source.write_bytes(archive_bytes([("etc/config/network",b"changed")]))
        with self.assertRaises(RuntimeError):
            self.manager.execute(review,self.destination,None,self.event,Mock())
        self.assertFalse(self.destination.exists())
        self.device.token = "b"*64
        with self.assertRaises(RuntimeError):
            self.manager.execute(review,self.destination,None,self.event,Mock())
    def test_reset_is_capability_bound_and_cannot_inject_command(self):
        review = self.manager.preview("reset","",self.event)
        review["reset_command"] = "rm -rf /"
        with self.assertRaises(RuntimeError):
            self.manager.execute(review,self.destination,None,self.event,Mock())
        self.assertFalse(self.destination.exists())
        self.device.reset = "none"
        with self.assertRaises(RuntimeError):self.manager.preview("reset","",self.event)
    def test_reset_uses_detected_command_and_reports_uncertain_transport(self):
        review = self.manager.preview("reset","",self.event)
        self.device.apply_error = True
        result = self.manager.execute(review,self.destination,None,self.event,Mock())
        self.assertEqual(result["outcome"],"uncertain")
        self.assertTrue(self.destination.is_file())
        self.assertFalse(any(n=="maintenance_reboot" for n,c in self.device.commands))


    def test_connection_switch_after_preview_cannot_run_on_new_router(self):
        review=self.manager.preview("reset","",self.event)
        self.device.manager.ssh._client=object()
        before=len(self.device.commands)
        with self.assertRaises(RuntimeError):
            self.manager.execute(review,self.destination,None,self.event,Mock())
        self.assertEqual(len(self.device.commands),before)
        self.assertFalse(self.destination.exists())


    def test_native_tar_manifest_mismatch_blocks_restore_before_writes(self):
        review=self.manager.preview("restore",self.source,self.event)
        self.device.listing_mismatch=True
        with self.assertRaises(RuntimeError):
            self.manager.execute(review,self.destination,None,self.event,Mock())
        self.assertTrue(self.destination.is_file())
        self.assertFalse(any(n=="maintenance_apply" for n,c in self.device.commands))

    def test_windows_backup_publish_does_not_require_hardlinks(self):
        import os
        if os.name!="nt":self.skipTest("Windows no-replace rename")
        with patch("os.link",side_effect=OSError("filesystem has no hardlinks")) as link:
            self.manager.export(self.destination,None,self.event,Mock())
        self.assertTrue(self.destination.is_file())
        link.assert_not_called()

    def test_publish_never_replaces_unapproved_existing_destination(self):
        self.destination.write_bytes(b"preserve")
        source=self.root/"temporary"
        source.write_bytes(b"new")
        with self.assertRaises(FileExistsError):
            self.manager._publish(source,self.destination,None,self.event)
        self.assertEqual(self.destination.read_bytes(),b"preserve")
