"""秒 Pin 系统 WPS 发送方式的定向回归。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.wps_defaults import wps_sender
from modules.capture_services.openwrt.wps_system_client import (
    WpsSystemClient,
    WpsSystemUnavailable,
)

BSSID = "5C:02:14:97:70:89"


class _Runner:
    """按命令片段返回预设输出，记录调用顺序。"""

    def __init__(self, replies):
        self.replies = replies
        self.commands = []

    def run(self, name, command, timeout, cancel_event=None, **_kwargs):
        self.commands.append(command)
        for marker, result in self.replies:
            if marker in command:
                return result
        return RemoteCommandResult(name, 0, stdout="")


def _result(name="cmd", code=0, stdout=""):
    return RemoteCommandResult(name, code, stdout=stdout)


class WpsSenderSettingTests(unittest.TestCase):
    def test_unknown_sender_falls_back_to_reaver(self):
        self.assertEqual(wps_sender({}), "reaver")
        self.assertEqual(wps_sender({"wps_sender": "SYSTEM"}), "system")
        self.assertEqual(wps_sender({"wps_sender": "bogus"}), "reaver")


class SystemClientReplyTests(unittest.TestCase):
    def test_last_non_empty_line_is_the_reply(self):
        client = WpsSystemClient(None, None)
        self.assertEqual(WpsSystemClient._reply(_result(stdout="Selected interface\nOK\n\n")), "OK")
        self.assertEqual(WpsSystemClient._reply(_result(stdout="FAIL\n")), "FAIL")

    def test_missing_binary_is_reported(self):
        runner = _Runner([("command -v wpa_supplicant",
                           _result(stdout="MISSING-supplicant\n"))])
        client = WpsSystemClient(runner, None)
        reason = client._missing_binary(None)
        self.assertIn("系统 WPS 组件", reason)

    def test_trimmed_firmware_raises_unavailable(self):
        runner = _Runner([
            ("command -v wpa_supplicant", _result(stdout="/usr/sbin/wpa_supplicant\n")),
            ("wpa_cli", _result(stdout="UNKNOWN COMMAND\n")),
        ])
        client = WpsSystemClient(runner, None)
        client.control_dir = "/tmp/x"
        client.interface = "wbsta123456"
        with self.assertRaises(WpsSystemUnavailable) as caught:
            client._require_wps_support(None)
        self.assertIn("精简版", str(caught.exception))

    def test_supported_firmware_accepts_fail_reply(self):
        runner = _Runner([("wpa_cli", _result(stdout="FAIL\n"))])
        client = WpsSystemClient(runner, None)
        client.control_dir = "/tmp/x"
        client.interface = "wbsta123456"
        client._require_wps_support(None)

    def test_new_wps_fail_log_marks_rejected(self):
        runner = _Runner([
            ("wc -c", _result(stdout="100\n")),
            ("grep -F", _result(stdout="")),
            ("tail -c +", _result(stdout="WPS-FAIL msg=8 config_error=15\n")),
            ("wpa_cli", _result(stdout="OK\n")),
        ])
        client = WpsSystemClient(runner, None)
        client.control_dir = "/tmp/x"
        client.config_path = "/tmp/x/wpa-system.conf"
        client.interface = "wbsta123456"
        client.log_path = "/tmp/x/wpa_supplicant.log"
        outcome, pin, psk = client.attempt(BSSID, "12345670", 5)
        self.assertEqual((outcome, pin, psk), ("rejected", "", ""))

    def test_config_omits_update_config(self):
        runner = _Runner([])
        client = WpsSystemClient(runner, None)
        client.control_dir = "/tmp/x/wpa-ctrl"
        client.config_path = "/tmp/x/wpa-system.conf"
        client._write_config(None)
        self.assertTrue(runner.commands)
        self.assertNotIn("update_config", runner.commands[-1])
        self.assertIn("ctrl_interface=", runner.commands[-1])
        self.assertIn("ap_scan=1", runner.commands[-1])

    def test_supplicant_is_started_with_show_keys(self):
        runner = _Runner([
            ("command -v", _result(stdout="")),
            ("info", _result(stdout="")),
            ("mkdir -p", _result(stdout="")),
        ])

        class ProcessManager:
            command = ""

            def start(self, program, command, *_args, **_kwargs):
                self.command = command
                return SimpleNamespace(log_path="/tmp/x/log")

            def is_running(self, *_args, **_kwargs):
                return True

        client = WpsSystemClient(runner, ProcessManager())
        client._missing_binary = lambda _cancel: ""
        client._create_interface = lambda *_args: None
        client._write_config = lambda *_args: None
        client._wait_control = lambda *_args: True
        client._require_wps_support = lambda *_args: None
        client.open(SimpleNamespace(remote_dir="/tmp/x"), "mon", "session", None)
        self.assertIn("-K", client.process_manager.command)
        self.assertIn("-d", client.process_manager.command)

    def test_network_key_hexdump_is_decoded(self):
        line = "wlan0: WPS: Network Key - hexdump(len=10): 31 32 33 34 35 36 37 38 39 30"
        self.assertEqual(WpsSystemClient._parse_network_key(line), "1234567890")
        short = "WPS: Network Key - hexdump(len=8): 70 61 73 73 77 6f 72 64"
        self.assertEqual(WpsSystemClient._parse_network_key(short), "password")
        hex64 = "WPS: Network Key - hexdump(len=64): " + " ".join(
            f"{byte:02x}" for byte in b"a" * 64)
        self.assertEqual(WpsSystemClient._parse_network_key(hex64), "a" * 64)

    def test_credentials_reads_network_key_from_log(self):
        line = "WPS: Network Key - hexdump(len=10): 31 32 33 34 35 36 37 38 39 30\n"
        runner = _Runner([("grep -F", _result(stdout=line))])
        client = WpsSystemClient(runner, None)
        client.log_path = "/tmp/x/wpa_supplicant.log"
        self.assertEqual(client._credentials(100, None), "1234567890")
        self.assertIn("grep -F", runner.commands[-1])

    def test_sanitize_log_tail_removes_key_material(self):
        text = (
            "WPS: Network Key - hexdump(len=10): 31 32 33\n"
            "ctrl_interface=/tmp/x\n"
            "psk=secret\n"
        )
        cleaned = WpsSystemClient._sanitize_log_tail(text)
        self.assertNotIn("Network Key", cleaned)
        self.assertNotIn("psk=", cleaned)
        self.assertIn("ctrl_interface", cleaned)


class _FakeClient:
    unavailable = ""
    kind = ""
    outcome = ("confirmed", "99247454", "secret-pass")

    def __init__(self, *_args):
        pass

    def open(self, *_args):
        if self.unavailable:
            raise WpsSystemUnavailable(self.unavailable, kind=self.kind)

    def attempt(self, _bssid, pin, _seconds, cancel_event=None):
        result = self.outcome
        return (result[0], result[1] or pin, result[2])

    def close(self):
        return []


class QuickSystemSenderTests(unittest.TestCase):
    def _run(self, options):
        from modules.capture_services.openwrt import wps_system_client
        from modules.capture_services.openwrt.wps_quick import QuickWpsCapture

        class ProcessManager:
            def start(self, *_args, **_kwargs):
                raise AssertionError("系统 WPS 模式不应启动 reaver")

            def is_running(self, *_args, **_kwargs):
                return False

        def base_builder(*_args, **_kwargs):
            raise AssertionError("系统 WPS 模式不应组装 reaver 命令")

        capture = QuickWpsCapture(ProcessManager(), base_builder, _Runner([]))
        clock = {"now": 0.0}

        def monotonic():
            return clock["now"]

        original = wps_system_client.WpsSystemClient
        wps_system_client.WpsSystemClient = _FakeClient
        try:
            import modules.capture_services.openwrt.wps_quick as quick
            original_time = quick.time.monotonic
            quick.time.monotonic = monotonic
            try:
                with TemporaryDirectory() as folder:
                    workspace = SimpleNamespace(local_dir=Path(folder), remote_dir="/tmp/owned")
                    target = SimpleNamespace(bssid=BSSID, ssid="Xiaomi_7AD8", channel=40, band="5G")
                    return capture.run(
                        workspace, "ownedmon", "owned", target, options, folder,
                        SimpleNamespace(), 0, 1)
            finally:
                quick.time.monotonic = original_time
        finally:
            wps_system_client.WpsSystemClient = original

    def test_confirmed_pin_returns_credentials(self):
        _FakeClient.unavailable = ""
        _FakeClient.outcome = ("confirmed", "99247454", "secret-pass")
        result = self._run({"wps_sender": "system", "quick_seconds": 30})
        self.assertTrue(result.psk_ok)
        self.assertTrue(result.pin_ok)
        self.assertEqual(result.pin, "99247454")

    def test_unavailable_points_back_to_reaver(self):
        from modules.capture_services.openwrt.wps_result_text import result_message
        _FakeClient.unavailable = "这台路由器的无线组件是精简版，不含 WPS 功能，系统 WPS 用不了。"
        _FakeClient.kind = "basic_wpad"
        try:
            result = self._run({"wps_sender": "system", "quick_seconds": 30})
        finally:
            _FakeClient.unavailable = ""
            _FakeClient.kind = ""
        self.assertFalse(result.psk_ok)
        self.assertIn("改回 reaver", result.note)
        self.assertEqual(result.unavailable_kind, "basic_wpad")
        message, level = result_message(result)
        self.assertEqual(level, "warning")
        self.assertIn("升级完整版 wpad", message)

    def test_system_wps_skips_offline_pixie_by_default(self):
        from modules.capture_services.openwrt import wps_quick

        _FakeClient.unavailable = ""
        _FakeClient.outcome = ("rejected", "", "")
        started = []
        try:
            with patch.object(
                    wps_quick.QuickWpsCapture, "_offline",
                    lambda *args, **kwargs: started.append(True) or ("", "", "", "")):
                result = self._run({"wps_sender": "system", "quick_seconds": 5,
                                    "quick_rounds": 1})
        finally:
            _FakeClient.outcome = ("confirmed", "99247454", "secret-pass")
        self.assertFalse(result.psk_ok)
        self.assertEqual(started, [])
        self.assertNotIn("离线 Pixie", result.note)

    def test_system_wps_offline_pixie_runs_only_when_enabled(self):
        from modules.capture_services.openwrt import wps_quick

        _FakeClient.unavailable = ""
        _FakeClient.outcome = ("rejected", "", "")
        started = []
        try:
            with patch.object(
                    wps_quick.QuickWpsCapture, "_offline",
                    lambda *args, **kwargs: started.append(True) or ("", "", "", "")):
                self._run({"wps_sender": "system", "quick_seconds": 5,
                           "quick_rounds": 1, "system_offline_pixie": True})
        finally:
            _FakeClient.outcome = ("confirmed", "99247454", "secret-pass")
        self.assertEqual(started, [True])

    def test_offline_pixie_psk_is_not_trusted_in_system_mode(self):
        from modules.capture_services.openwrt import wps_quick

        _FakeClient.unavailable = ""
        _FakeClient.outcome = ("rejected", "", "")
        try:
            with patch.object(
                    wps_quick.QuickWpsCapture, "_offline",
                    lambda *args, **kwargs: ("12345670", "reaver-secret", "", "")):
                result = self._run({"wps_sender": "system", "quick_seconds": 5,
                                    "quick_rounds": 1, "system_offline_pixie": True})
        finally:
            _FakeClient.outcome = ("confirmed", "99247454", "secret-pass")
        # Pixie 算出的 PIN 保留待用，但 reaver 顺手拿到的密码不能当作系统 WPS 结果。
        self.assertTrue(result.pin_ok)
        self.assertFalse(result.psk_ok)


if __name__ == "__main__":
    unittest.main()
