"""Pinned online hcx acquisition and task-local cleanup regressions."""
import hashlib
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.hcx_online import online_primary_package, primary_row
from modules.capture_services.openwrt.signal_manager import SignalError

ROOT = Path(__file__).resolve().parents[1]


def original(device):
    branch = "25.12" if device.package_manager == "apk" else "24.10"
    folder = ROOT / "tools/hc_offline_pack" / branch / device.package_architecture
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    row = next(r for r in manifest["packages"] if r["name"] == "hcxdumptool")
    return row, (folder / "pkgs" / row["file"]).read_bytes()


class HcxOnlineTests(unittest.TestCase):
    def setUp(self):
        self.device = NS(package_manager="opkg", package_architecture="mipsel_24kc")
        self.cancel = Event()

    def test_all_four_online_pins_match_original_approved_bodies(self):
        for pm in ("opkg", "apk"):
            for arch in ("mipsel_24kc", "aarch64_cortex-a53"):
                with self.subTest(pm=pm, arch=arch):
                    device = NS(package_manager=pm, package_architecture=arch)
                    approved, raw = original(device)
                    row = primary_row(device)
                    self.assertEqual((row["filename"], row["size"], row["sha256"]),
                                     (approved["file"], len(raw), hashlib.sha256(raw).hexdigest()))
                    self.assertEqual(row["version"], "6.3.4-r1")
                    with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes", return_value=raw) as fetch:
                        with online_primary_package(device, self.cancel, Mock()) as (path, selected):
                            self.assertEqual(path.read_bytes(), raw)
                            self.assertEqual(path.name, row["filename"])
                            self.assertEqual(selected, row)
                        self.assertFalse(path.exists())
                    fetch.assert_called_once()
                    self.assertEqual(fetch.call_args.kwargs["limit"], len(raw))
                    self.assertEqual(fetch.call_args.args[0], row["url"])

    def test_transport_failure_uses_official_archive_without_changing_version(self):
        _, raw = original(self.device)
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes",
                   side_effect=[RuntimeError("HTTP 404"), RuntimeError("HTTP 404"), raw]) as fetch:
            with online_primary_package(self.device, self.cancel, Mock()) as (_, row):
                self.assertEqual(row["version"], "6.3.4-r1")
        self.assertIn("archive.openwrt.org", fetch.call_args_list[2].args[0])
        self.assertEqual(len(fetch.call_args_list), 3)

    def test_changed_or_truncated_body_never_yields_a_package(self):
        _, raw = original(self.device)
        for changed in (raw[:-1], raw[:-1] + bytes([raw[-1] ^ 1])):
            with self.subTest(size=len(changed)), patch(
                    "modules.capture_services.openwrt.hcx_online.fetch_bytes", return_value=changed) as fetch:
                with self.assertRaises(SignalError):
                    with online_primary_package(self.device, self.cancel, Mock()):
                        self.fail("Corrupt package must never reach installation")
                fetch.assert_called_once()

    def test_cancellation_before_or_during_fetch_never_yields_a_package(self):
        self.cancel.set()
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes") as fetch:
            with self.assertRaises(InterruptedError):
                with online_primary_package(self.device, self.cancel, Mock()):pass
            fetch.assert_not_called()
        self.cancel.clear()
        def stop(*args, **kwargs):
            self.cancel.set()
            raise InterruptedError("cancelled")
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes", side_effect=stop) as fetch:
            with self.assertRaises(InterruptedError):
                with online_primary_package(self.device, self.cancel, Mock()):pass
            fetch.assert_called_once()

    def test_offline_hosts_failing_cannot_install_another_version(self):
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes",
                   side_effect=RuntimeError("unavailable")) as fetch:
            with self.assertRaises(SignalError):
                with online_primary_package(self.device, self.cancel, Mock()):
                    self.fail("No fallback to router feed version is allowed")
            self.assertEqual(fetch.call_count, 4)

    def test_installer_error_cleans_temp_without_retrying_download_or_install(self):
        _, raw = original(self.device)
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes", return_value=raw) as fetch:
            with self.assertRaisesRegex(RuntimeError, "install failed"):
                with online_primary_package(self.device, self.cancel, Mock()) as (path, _):
                    self.assertTrue(path.exists())
                    raise RuntimeError("install failed")
            self.assertFalse(path.exists())
            fetch.assert_called_once()

    def test_unsafe_architecture_never_downloads(self):
        device = NS(package_manager="opkg", package_architecture="../../x86_64")
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes") as fetch:
            with self.assertRaises(SignalError):
                with online_primary_package(device, self.cancel, Mock()):pass
            fetch.assert_not_called()
