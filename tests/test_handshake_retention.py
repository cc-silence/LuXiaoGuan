"""Offline capture retention and cross-batch delivery contracts."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
from modules.capture_services.openwrt.handshake_capture import HandshakeCaptureResult
from modules.capture_services.openwrt.models import AccessPointInfo
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend, HandshakeTargetResult
from modules.capture_services.handshake_processing import DeferredHandshake
from modules.config_services.capture_settings_manager import CaptureSettingsManager


class RetentionTests(unittest.TestCase):
    target = AccessPointInfo("02:11:22:33:44:55", "test", channel=6)

    def test_unusable_results_are_not_saved_even_with_legacy_setting(self):
        for verdict in ("none", "partial", "unknown"):
            with self.subTest(verdict=verdict), TemporaryDirectory() as folder:
                source = Path(folder) / "input.cap"
                source.write_bytes(b"test")
                result = HandshakeCaptureResult(str(source), self.target.bssid, "test", verdict=verdict)
                entry, retry = OpenWrtCaptureBackend._save_handshake_entry(
                    NS(), result, {"handshake_save_usable_only": False}, Path(folder) / "out",
                    self.target, (), Mock())
                self.assertTrue(retry)
                self.assertFalse(entry.result.cap_path)
                self.assertFalse((Path(folder) / "out").exists())

    def test_timeout_still_retries_without_saving(self):
        result = HandshakeCaptureResult("input.cap", self.target.bssid, "test", verdict="none", deauth_timed_out=True)
        entry, retry = OpenWrtCaptureBackend._save_handshake_entry(
            NS(), result, {"handshake_save_usable_only": False}, Path("unused"), self.target, (), Mock())
        self.assertTrue(retry)
        self.assertEqual(entry.error_code, "deauth_timeout")
        self.assertFalse(entry.result.cap_path)

    def test_legacy_setting_is_removed(self):
        with TemporaryDirectory() as folder:
            manager = CaptureSettingsManager(Path(folder) / "settings.json")
            data = manager.load()
            data["openwrt"]["handshake_save_usable_only"] = False
            manager.save(data)
            self.assertNotIn("handshake_save_usable_only", manager.load()["openwrt"])

    def test_failed_result_publishes_while_next_group_is_still_capturing(self):
        second_started, published = Event(), Event()
        targets = (self.target, AccessPointInfo("02:11:22:33:44:66", "second", channel=11))
        backend = NS(ssh_client=NS(connected=True), _ensure_remote_tmp=Mock(),
            reconnect_cleanup=Mock(), workspace_manager=Mock(), _finish_capture_task=Mock())
        messages = []
        def capture(*args, **kwargs):
            target = args[4]
            entry = HandshakeTargetResult(target, HandshakeCaptureResult("", target.bssid, target.ssid, verdict="none"))
            if target.bssid == targets[0].bssid:
                def finish():
                    if not second_started.wait(3): raise RuntimeError("Next capture did not start")
                    return ((entry, True),)
                return DeferredHandshake(finish)
            second_started.set()
            if not published.wait(3): raise RuntimeError("Previous failure remained blocked by capture")
            return entry, True
        backend._capture_single_handshake = capture
        def ready(entry):
            if entry.target.bssid == targets[0].bssid: published.set()
        with TemporaryDirectory() as folder:
            batch = OpenWrtCaptureBackend.capture_handshakes_passive(backend, "offline",
                {"resolved_output_dir":folder, "handshake_rounds":1}, targets, Event(),
                target_callback=ready, message_callback=lambda text,level: messages.append(text))
        self.assertTrue(published.is_set())
        self.assertFalse(batch.note)
        self.assertEqual(len(batch.results),2)
        self.assertEqual(sum(text.startswith("test：") for text in messages),1)
