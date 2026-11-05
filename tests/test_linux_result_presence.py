"""Result references and metadata never turn uncertainty into lost capture history."""
import base64
import io
import json
from pathlib import PurePosixPath, Path
import stat
import subprocess
import sys
import tempfile
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch, mock_open
from modules.platform_services import result_paths as paths
from modules.platform_services import linux_file_presence as native
from modules.platform_services import file_presence as presence


class ResultPathTests(unittest.TestCase):
    def setUp(self):
        self.mode = patch.object(paths, "is_linux", return_value=True)
        self.mode.start(); self.addCleanup(self.mode.stop)
        self.roots = {"{APP_DIR}": "/opt/Router", "{USER_HOME}": "/home/test", "{USER_DOWNLOADS}": "/home/test/Downloads"}

    def test_posix_tokens_absolute_base_case_and_round_trip(self):
        resolve = lambda value, **kw: paths.resolve_result_path(value, self.roots, **kw)
        self.assertEqual(resolve("{USER_DOWNLOADS}/Results/Capture.cap"), "/home/test/Downloads/Results/Capture.cap")
        self.assertEqual(resolve("capture.cap", base="/home/test/Downloads"), "/home/test/Downloads/capture.cap")
        self.assertNotEqual(resolve("/data/A.cap"), resolve("/data/a.cap"))
        portable = paths.portable_result_path("/home/test/Downloads/Results/Capture.cap", self.roots)
        self.assertEqual(portable, "{USER_DOWNLOADS}/Results/Capture.cap")
        self.assertEqual(resolve(portable), "/home/test/Downloads/Results/Capture.cap")

    def test_foreign_ambiguous_and_traversal_references_stay_unknown(self):
        for value in (r"C:\Results\old.cap", r"{APP_DIR}\old.cap", "/a/../b.cap", "/a/./b.cap", "//server/share/a.cap", "old.cap", "{OTHER}/a.cap", "{APP_DIR}//a.cap", "/"):
            with self.subTest(value=value): self.assertEqual(paths.resolve_result_path(value, self.roots), "")
        self.assertEqual(paths.resolve_result_path("/data/a: b.cap", self.roots), "/data/a: b.cap")

    def test_windows_drive_unc_and_token_contract_remains(self):
        roots = {"{APP_DIR}": r"C:\Router", "{USER_HOME}": r"C:\Users\test"}
        with patch.object(paths, "is_linux", return_value=False):
            self.assertEqual(paths.resolve_result_path("{APP_DIR}/results/A.cap", roots), r"C:\Router\results\A.cap")
            self.assertEqual(paths.resolve_result_path("A.cap", roots, base=r"D:\Results"), r"D:\Results\A.cap")
            self.assertEqual(paths.resolve_result_path(r"\\server\share\A.cap", roots), r"\\server\share\A.cap")
            self.assertEqual(paths.portable_result_path(r"C:\Router\results\A.cap", roots), r"{APP_DIR}\results\A.cap")
            self.assertEqual(paths.resolve_result_path(r"C:\Router\..\A.cap", roots), "")


class FakeFilesystem:
    O_RDONLY=0; O_DIRECTORY=1; O_NOFOLLOW=2; O_CLOEXEC=4; O_NONBLOCK=8
    def __init__(self):
        self.nodes = {"/": NS(st_mode=stat.S_IFDIR, st_dev=1, st_ino=1),
                      "/data": NS(st_mode=stat.S_IFDIR, st_dev=1, st_ino=2),
                      "/data/a.cap": NS(st_mode=stat.S_IFREG, st_dev=1, st_ino=3)}
        self.fds = {}; self.closed = []; self.stat_calls = {}; self.rename = False
    def open(self, name, flags, *, dir_fd=None):
        path = name if dir_fd is None else str(PurePosixPath(self.fds[dir_fd]) / name)
        if path not in self.nodes: raise FileNotFoundError()
        fd = len(self.fds) + 10; self.fds[fd] = path; return fd
    def stat(self, name, *, dir_fd, follow_symlinks):
        assert not follow_symlinks
        path = str(PurePosixPath(self.fds[dir_fd]) / name)
        self.stat_calls[path] = self.stat_calls.get(path, 0) + 1
        if path not in self.nodes: raise FileNotFoundError()
        if self.rename and path == "/data" and self.stat_calls[path] > 1:
            return NS(st_mode=stat.S_IFDIR, st_dev=1, st_ino=999)
        return self.nodes[path]
    def fstat(self, fd): return self.nodes[self.fds[fd]]
    def close(self, fd): self.closed.append(fd)


class MetadataPolicyTests(unittest.TestCase):
    def probe(self, path, fs=None, local=None):
        fs = fs or FakeFilesystem()
        with patch.object(native, "os", fs), patch.object(native, "is_local_fd", side_effect=local or (lambda *_: True)):
            result = native.probe_path(path, {})
        self.assertEqual(set(fs.closed), set(fs.fds))
        return result

    def test_regular_present_and_confirmed_missing(self):
        self.assertEqual(self.probe("/data/a.cap"), "present")
        self.assertEqual(self.probe("/data/missing.cap"), "missing")
        self.assertEqual(self.probe("/missing/child.cap"), "missing")

    def test_links_directories_special_and_foreign_mounts_stay_unknown(self):
        for mode in (stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO):
            fs = FakeFilesystem(); fs.nodes["/data/a.cap"].st_mode = mode
            self.assertEqual(self.probe("/data/a.cap", fs), "unknown")
        fs = FakeFilesystem(); fs.nodes["/data/a.cap"].st_dev = 2
        self.assertEqual(self.probe("/data/a.cap", fs), "unknown")
        self.assertEqual(self.probe("/data/missing.cap", local=lambda *_: False), "unknown")

    def test_changed_directory_never_confirms_missing(self):
        fs = FakeFilesystem(); fs.rename = True
        self.assertEqual(self.probe("/data/missing.cap", fs), "unknown")

    def test_permissions_failures_release_descriptors(self):
        fs = FakeFilesystem()
        def deny(*args, **kwargs): raise PermissionError()
        fs.stat = deny
        self.assertEqual(self.probe("/data/missing.cap", fs), "unknown")

    def test_unsafe_paths_do_not_touch_filesystem(self):
        for path in ("relative.cap", "/a/../b", "/a//b", "/", "//server/a", "/a/./b"):
            fs = FakeFilesystem()
            self.assertEqual(self.probe(path, fs), "unknown")
            self.assertFalse(fs.fds)

    def test_mount_identity_requires_known_local_filesystem(self):
        with patch("builtins.open", mock_open(read_data=b"pos: 0\nmnt_id: 20\n")):
            self.assertTrue(native.is_local_fd(1, {20: "ext4"}))
            for filesystem in ("nfs", "cifs", "fuse.sshfs", "overlay", "unknown"):
                self.assertFalse(native.is_local_fd(1, {20: filesystem}))


class HelperBoundaryTests(unittest.TestCase):
    def test_valid_worker_is_read_only_and_returns_only_states(self):
        payload = base64.b64encode(json.dumps(["/data/a.cap"]).encode()).decode()
        output = io.StringIO()
        with patch.object(native.sys, "platform", "linux"), patch.object(native, "mount_types", return_value={}), patch.object(native, "probe_path", return_value="present"):
            self.assertEqual(native.run_worker(["app", "--file-presence-worker", payload], output), 0)
        self.assertEqual(json.loads(output.getvalue()), ["present"])
        self.assertNotIn("/data", output.getvalue())

    def test_invalid_payload_stops_before_metadata_access(self):
        for payload in ("bad", base64.b64encode(b"[]").decode(), base64.b64encode(json.dumps(["a"] * 65).encode()).decode()):
            with patch.object(native.sys, "platform", "linux"), patch.object(native, "mount_types") as read:
                self.assertEqual(native.run_worker(["app", "--file-presence-worker", payload], io.StringIO()), 2)
                read.assert_not_called()

    def test_timeout_cancel_and_invalid_output_preserve_unknown(self):
        for failure in (TimeoutError(), InterruptedError(), ValueError()):
            with patch.object(presence.sys, "platform", "linux"), patch.object(presence, "capture_process", side_effect=failure):
                result = presence.check_local_files(["/data/a.cap"], Event())
                self.assertEqual(result["/data/a.cap"].state, "unknown")
        with patch.object(presence.sys, "platform", "linux"), patch.object(presence, "capture_process", return_value=subprocess.CompletedProcess([], 0, b'["bad"]')):
            self.assertEqual(presence.check_local_files(["/data/a.cap"], Event())["/data/a.cap"].state, "unknown")

    def test_linux_dispatch_is_batched_and_never_uses_powershell(self):
        seen = []
        def capture(command, *args, **kwargs):
            self.assertIn("--file-presence-worker", command)
            batch = json.loads(base64.b64decode(command[-1])); seen.append(len(batch))
            return subprocess.CompletedProcess(command, 0, json.dumps(["present"] * len(batch)).encode())
        values = ["/data/" + str(index) + ".cap" for index in range(130)]
        with patch.object(presence.sys, "platform", "linux"), patch.object(presence, "capture_process", side_effect=capture):
            result = presence.check_local_files(values, Event())
        self.assertEqual(len(result), 130)
        self.assertEqual(seen, [64, 64, 2])
        self.assertTrue(all(row.state == "present" for row in result.values()))


class VerifiedHistoryIntegrationTests(unittest.TestCase):
    def test_only_all_missing_evidence_may_clear_capture_completion(self):
        from modules.capture_services import verified_results as verified
        history = {"MISSING": {"artifact_paths": ["/data/a.cap", "/data/a.22000"]},
                   "PRESENT": {"artifact_paths": ["/data/b.cap", "/data/b.22000"]},
                   "UNKNOWN": {"artifact_paths": ["/data/c.cap", "/data/c.22000"]},
                   "FOREIGN": {"artifact_paths": [r"C:\old\capture.cap"]}}
        states = {"/data/a.cap": "missing", "/data/a.22000": "missing",
                  "/data/b.cap": "missing", "/data/b.22000": "present",
                  "/data/c.cap": "missing", "/data/c.22000": "unknown"}
        def check(values, event):
            return {path: presence.FilePresence(states[path]) for path in values}
        with patch.object(paths, "is_linux", return_value=True), patch.object(verified, "check_local_files", side_effect=check):
            report = verified.check_verified_results(history, (), 7, "session", Event())
        self.assertEqual(report.missing_bssids, frozenset({"MISSING"}))
        self.assertEqual(report.targets["PRESENT"], "present")
        self.assertEqual(report.targets["UNKNOWN"], "unknown")
        self.assertEqual(report.targets["FOREIGN"], "unknown")
        self.assertEqual(report.revision, 7)
        self.assertEqual(report.session_id, "session")


class ChangingMountTests(unittest.TestCase):
    def test_mount_table_changes_invalidate_missing_results(self):
        payload = base64.b64encode(b'["/data/a.cap"]').decode()
        output = io.StringIO()
        with patch.object(native.sys, "platform", "linux"), patch.object(native, "mount_types", side_effect=[{1:"ext4"}, {1:"nfs"}]), patch.object(native, "probe_path", return_value="missing"):
            self.assertEqual(native.run_worker(["app", "--file-presence-worker", payload], output), 0)
        self.assertEqual(json.loads(output.getvalue()), ["unknown"])


@unittest.skipUnless(sys.platform.startswith("linux"), "Native Linux descriptors and metadata helper")
class NativeLinuxPresenceTests(unittest.TestCase):
    def test_real_present_missing_and_symlink_through_private_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / "capture.cap"; file.write_bytes(b"unchanged result")
            link = root / "link.cap"; link.symlink_to(file)
            missing = root / "missing.cap"
            result = presence.check_local_files([str(file), str(missing), str(link)], Event())
            # This acceptance case needs a confirmed local Linux filesystem;
            # remote/FUSE/overlay paths must never be interpreted as missing.
            if result[str(file)].state == "unknown":
                self.skipTest("Native acceptance requires a supported local filesystem without linked ancestors")
            self.assertEqual(result[str(file)].state, "present")
            self.assertEqual(result[str(missing)].state, "missing")
            self.assertEqual(result[str(link)].state, "unknown")
            self.assertEqual(file.read_bytes(), b"unchanged result")
