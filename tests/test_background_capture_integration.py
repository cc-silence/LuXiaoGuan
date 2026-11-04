"""Behavioral regression for authorized background logs and serialized commands."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner, RemoteCommandResult
from modules.capture_services.openwrt.auto_capture_boot_manager import AutoCaptureBootManager

ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
SHELL = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("sh")


class SerializedCommandsTests(unittest.TestCase):
    def runner(self):
        ssh = Mock(connected=True)
        runner = OpenWrtCommandRunner(ssh)
        runner._run = Mock(return_value=RemoteCommandResult("fixture", 0, "ok"))
        return runner

    def test_queue_cancellation_never_submits_command_or_cancels_holder(self):
        runner = self.runner()
        cancel = threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with runner._command_lock:
                pending = pool.submit(runner.run, "queued", "write", 2, cancel)
                cancel.set()
                result = pending.result(timeout=1)
                self.assertTrue(result.cancelled)
                self.assertEqual(result.failure_stage, "command_queue")
                runner._run.assert_not_called()
        self.assertEqual(runner.run("next", "read", 1).exit_code, 0)

    def test_queue_timeout_is_bounded_and_does_not_submit_command(self):
        runner = self.runner()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with runner._command_lock:
                started = time.monotonic()
                result = pool.submit(runner.run, "queued", "write", .1).result(timeout=1)
                self.assertTrue(result.timed_out)
                self.assertEqual(result.failure_stage, "command_queue")
                self.assertLess(time.monotonic() - started, 1)
                runner._run.assert_not_called()
        self.assertEqual(runner.run("next", "read", 1).exit_code, 0)

    def test_exception_releases_lock_for_another_thread(self):
        runner = self.runner()
        runner._run.side_effect = [ValueError("fixture failure"), RemoteCommandResult("next", 0)]
        with self.assertRaises(ValueError):
            runner.run("bad", "read", 1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(pool.submit(runner.run, "next", "read", 1).result(timeout=2).exit_code, 0)


@unittest.skipUnless(SHELL, "POSIX shell unavailable")
class BackgroundLogsTests(unittest.TestCase):
    def test_byte_caps_keep_start_end_and_active_append_handle(self):
        source = (ROOT / "tools/scripts/hcx_autocap.sh").read_text(encoding="utf-8")
        functions = source[source.index("cap_log_file() {"):]
        end = functions.index("\n}\n", functions.index("cap_runlogs() {")) + 3
        functions = functions[:end]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runlog, session, stdout = [root / name for name in ("run.log", "session.log", "stdout.log")]
            for path in (runlog, session, stdout):
                path.write_bytes(b"START" + b"x" * 600000 + b"END")
            functions = functions.replace("/tmp/.hcx_autocap.stdout.log", stdout.as_posix())
            setup = "RUNLOGS=" + shlex.quote(runlog.as_posix()) + "; SESSION_LOG=" + shlex.quote(session.as_posix()) + "; MAX_RUNLOG_KB=256;\n"
            code = setup + functions + '\nexec 3>>"$RUNLOGS"; cap_runlogs; printf AFTER >&3; exec 3>&-;'
            result = subprocess.run([SHELL, "-c", code], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            for path, limit in ((runlog, 256 * 1024), (session, 256 * 1024), (stdout, 65536)):
                data = path.read_bytes()
                self.assertLessEqual(len(data), limit)
                self.assertTrue(data.startswith(b"START"))
                self.assertIn(b"END", data)
                self.assertNotIn(b"\0", data)
            self.assertTrue(runlog.read_bytes().endswith(b"AFTER"))

    def test_repeated_start_does_not_erase_running_console_log(self):
        runner = Mock()
        runner.run.return_value = RemoteCommandResult("start", 0)
        manager = AutoCaptureBootManager(runner, Mock())
        manager.start_script()
        command = runner.run.call_args.args[1]
        self.assertIn('</dev/null >> "$OUT_LOG"', command)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "hcx_autocap.sh").write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            logfile = root / ".hcx_autocap.stdout.log"
            logfile.write_bytes(b"existing diagnostic evidence")
            command = command.replace("/root/", root.as_posix() + "/").replace("/tmp/", root.as_posix() + "/")
            setup = 'printf "%s" $$ > ' + shlex.quote((root / ".hcx_autocap.lock").as_posix()) + "; "
            result = subprocess.run([SHELL, "-c", setup + command], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(logfile.read_bytes(), b"existing diagnostic evidence")
            self.assertIn(b"existing diagnostic evidence", result.stdout)

    def test_recent_log_read_is_byte_and_line_bounded(self):
        runner = Mock()
        runner.run.return_value = RemoteCommandResult("log", 0)
        AutoCaptureBootManager(runner, Mock()).read_recent_session_log(100000)
        command = runner.run.call_args.args[1]
        self.assertIn("tail -c 65536", command)
        self.assertIn("tail -n 200", command)
        self.assertNotIn("100000", command)


class BackgroundLogAdmissionTests(unittest.TestCase):
    def check_snapshot(self, replace_connection):
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
        entered, release = threading.Event(), threading.Event()
        def read(**kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("fixture did not release")
            return "latest frame"
        owner = NS(ssh=NS(_client=object(), connected=True), _disposed=False,
                   _boot_probe_lock=threading.Lock(), _is_polling_boot_log=False,
                   boot_manager=NS(read_recent_session_log=read), bootLogUpdated=Mock())
        workers = []
        real_thread = threading.Thread
        def tracked_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            workers.append(thread)
            return thread
        with patch("modules.capture_services.openwrt.auto_capture_service.threading.Thread", side_effect=tracked_thread):
            self.assertTrue(AutoCaptureService.poll_boot_log(owner))
            try:
                self.assertTrue(entered.wait(1))
                for _ in range(5):
                    self.assertFalse(AutoCaptureService.poll_boot_log(owner))
                if replace_connection:
                    owner.ssh._client = object()
            finally:
                release.set()
                for thread in workers:
                    thread.join(2)
        self.assertEqual(len(workers), 1)
        self.assertFalse(owner._is_polling_boot_log)
        if replace_connection:
            owner.bootLogUpdated.emit.assert_not_called()
        else:
            owner.bootLogUpdated.emit.assert_called_once_with("latest frame")

    def test_repeated_reads_share_one_worker(self):
        self.check_snapshot(False)

    def test_old_connection_result_is_not_published_to_new_login(self):
        self.check_snapshot(True)
