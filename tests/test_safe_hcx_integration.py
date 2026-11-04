"""Selective hcx integration: keep proven modern capture and restoration boundaries."""
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.models import OpenWrtRadioInfo

GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
SHELL = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("sh")


class SafeHcxIntegrationTests(unittest.TestCase):
    def session(self, help_text):
        runner = Mock()
        runner.run.return_value = RemoteCommandResult("probe", 0, help_text)
        session = AutoCaptureRadioSession(OpenWrtRadioInfo(name="radio7", interface="phy2", bands=("5G",)), runner, Mock(), Path("."), Mock())
        session._hcx_bin = "/usr/sbin/hcxdumptool"
        session.remote_cap_file = "/tmp/current.pcapng"
        return session, runner

    def test_named_versions_select_existing_legacy_or_modern_commands(self):
        for version, expected in (("6.0.1", "legacy"), ("6.2.4", "legacy"), ("6.3.4", "modern"), ("7.0.0", "modern")):
            with self.subTest(version=version):
                session, _ = self.session("hcxdumptool " + version)
                self.assertEqual(session._probe_dialect(), expected)

    def test_unversioned_options_do_not_use_unrelated_driver_version(self):
        for output, expected in (("driver 1.0\n-w <outfile> : packets", "modern"), ("-o <dump file> : packets", "legacy")):
            session, _ = self.session(output)
            self.assertEqual(session._probe_dialect(), expected)

    def test_empty_help_uses_bounded_version_query(self):
        session, runner = self.session("")
        runner.run.side_effect = [RemoteCommandResult("help", 0, ""), RemoteCommandResult("version", 0, "hcxdumptool 6.2.4")]
        self.assertEqual(session._probe_dialect(), "legacy")
        self.assertEqual(runner.run.call_count, 2)
        self.assertEqual(runner.run.call_args.kwargs["timeout"], 5)

    def test_empty_legacy_channel_list_does_not_emit_invalid_channel_option(self):
        session, _ = self.session("hcxdumptool 6.2.4")
        for channels in ("", ",,", "b,b"):
            with self.subTest(channels=channels):
                commands = session._build_attempt_commands(channels, "/tmp/bpf")
                self.assertTrue(commands)
                for _, command in commands:
                    self.assertIn(" -o ", command)
                    if channels:
                        self.assertNotIn(" -c ", command)
                    elif " -c " in command:
                        value = shlex.split(command)[shlex.split(command).index("-c") + 1]
                        self.assertTrue(value)
                        self.assertFalse(value.startswith("-"))
                    self.assertNotIn(" -w ", command)

    def test_modern_attempts_never_mix_legacy_flags(self):
        session, _ = self.session("hcxdumptool 6.3.4")
        for _, command in session._build_attempt_commands("36b,40b", "/tmp/bpf"):
            self.assertIn(" -w ", command)
            self.assertNotIn(" -o ", command)
            self.assertNotIn("--enable_status", command)

    @unittest.skipUnless(SHELL, "POSIX test shell unavailable")
    def test_background_monitor_creation_uses_actual_phy_and_failure_stays_failure(self):
        source = (Path(__file__).resolve().parents[1] / "tools/scripts/hcx_autocap.sh").read_text(encoding="utf-8")
        function = source.split("create_raw_monitor() {", 1)[1].split("\n}\n", 1)[0]
        function = "create_raw_monitor() {" + function + "\n}\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "phy2").mkdir()
            function = function.replace("/sys/class/ieee80211/", root.as_posix() + "/")
            for fail in (0, 7):
                trace = root / "trace"
                prelude = "PHY_radio7=phy2; CREATED_IFS=''; trace=" + shlex.quote(trace.as_posix()) + "; fail=" + str(fail) + ";\n"
                prelude += "uci() { printf 5g; }; mac_name() { wlan_mac=02:00:00:00:00:01; }; log() { :; }; sleep() { :; };\n"
                prelude += 'iw() { [ "$1" = dev ] && return 0; printf "%s\\n" "$*" > "$trace"; return "$fail"; }; ip() { :; };\n'
                result = subprocess.run([SHELL, "-c", prelude + function + 'create_raw_monitor radio7; printf "%s" "$CREATED_IFS"'], capture_output=True, text=True, encoding="utf-8", timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(trace.read_text().strip(), "phy phy2 interface add hcxmon7 type monitor addr 02:00:00:00:00:01")
                self.assertEqual(result.stdout.strip(), "hcxmon7" if fail == 0 else "")

    def test_download_socket_failure_keeps_error_state_and_removes_partial_file(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        session, _ = self.session("hcxdumptool 6.3.4")
        session.ssh_client.transfer.return_value = nullcontext()
        sftp = session.ssh_client.open_sftp.return_value
        sftp.stat.return_value = SimpleNamespace(st_size=4, st_mtime=1)
        def failed_get(remote, local):
            Path(local).write_bytes(b"part")
            raise OSError("Socket is closed")
        sftp.get.side_effect = failed_get
        with tempfile.TemporaryDirectory() as directory:
            session.output_dir = Path(directory)
            session._cleanup_session(Mock())
            self.assertEqual(session.stats.state, "error")
            self.assertIn("Socket is closed", session.stats.error_message)
            self.assertEqual(list(Path(directory).iterdir()), [])
        sftp.close.assert_called_once()
        session.service.radioStateChanged.emit.assert_called_with("radio7", "error")
        self.assertFalse(any(call.args[1] == "success" for call in session.service.messageGenerated.emit.call_args_list))
