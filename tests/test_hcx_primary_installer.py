"""Regression coverage for preserving installed hcx shared libraries."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modules.capture_services.openwrt.hcx_primary_installer import install_primary, primary_command


class HcxPrimaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = MagicMock()
        self.calls = []
        self.versions = "libpcap1=1.10.4-1\nlibopenssl3=3.0.9-1\n"
        self.plan = "(1/1) Upgrading hcxdumptool (6.2.4-r1 -> 6.3.4-r1)"
        self.fail_install = False
        self.wrong_hash = False
        self.cancel = Event()
        self.size_patch = patch("modules.capture_services.openwrt.package_space.ipk_installed_size", return_value=200000)
        self.size_patch.start()
        self.addCleanup(self.size_patch.stop)
        self.manager.command_runner.run.side_effect = self.run_command
        self.manager.command_runner.ssh_client.connected = True

    def run_command(self, name, command, **kwargs):
        self.calls.append((name, command))
        outputs = {"hcx_library_probe": self.versions,
                   "hcx_primary_plan": self.plan,
                   "hcx_uploaded_hash": ("0" * 64 if self.wrong_hash else self.digest) + "  package",
                   "verify_hcx": "hcxdumptool 6.3.4"}
        return SimpleNamespace(exit_code=1 if name == "install_modern_hcx" and self.fail_install else 0,
                               stdout=outputs.get(name, ""), stderr="failure")

    def install(self, pm="opkg"):
        branch = "24.10" if pm == "opkg" else "25.12"
        bundle = self.root / "tools/hc_offline_pack" / branch / "mipsel_24kc"
        (bundle / "pkgs").mkdir(parents=True)
        filename = "hcxdumptool_6.3.4-r1_mipsel_24kc.ipk" if pm == "opkg" else "hcxdumptool-6.3.4-r1.apk"
        content = b"fixture-package"
        self.digest = hashlib.sha256(content).hexdigest()
        (bundle / "pkgs" / filename).write_bytes(content)
        (bundle / "manifest.json").write_text(json.dumps({
            "branch": branch, "arch": "mipsel_24kc", "mode": "ipk" if pm == "opkg" else "apk",
            "packages": [{"name": "hcxdumptool", "file": filename, "version": "6.3.4-r1", "sha256": self.digest}]
        }), encoding="utf-8")
        device = SimpleNamespace(package_manager=pm, package_architecture="mipsel_24kc")
        with patch("modules.config_manager.BASE_DIR", self.root):
            return install_primary(self.manager, device, True, self.cancel, MagicMock(), None, None)

    def test_opkg_only_uploads_primary_and_does_not_force_libraries(self):
        self.assertTrue(self.install())
        puts = self.manager.command_runner.ssh_client.open_sftp.return_value.put.call_args_list
        self.assertEqual(len(puts), 1)
        command = dict(self.calls)["install_modern_hcx"]
        self.assertIn("--nodeps", command)
        self.assertNotIn("libopenssl", command)
        self.assertNotIn("libpcap", command)
        self.assertNotIn("*", command)
        self.assertNotIn("--force-overwrite", command)
        self.manager._install_online.assert_not_called()

    def test_apk_pins_existing_library_versions(self):
        self.assertTrue(self.install("apk"))
        command = dict(self.calls)["install_modern_hcx"]
        self.assertIn("libpcap1=1.10.4-1", command)
        self.assertIn("libopenssl3=3.0.9-1", command)
        self.assertIn("--no-network", command)

    def test_apk_rejects_library_change_in_plan(self):
        self.plan = "(1/1) Upgrading libopenssl3 (3.0.9-r1 -> 3.0.22-r1)"
        with self.assertRaisesRegex(RuntimeError, "another package"):
            self.install("apk")
        self.assertNotIn("install_modern_hcx", dict(self.calls))
        self.assertIn("hcx_cleanup", dict(self.calls))

    def test_failure_is_not_masked_by_cleanup(self):
        self.fail_install = True
        with self.assertRaisesRegex(RuntimeError, "install_modern_hcx"):
            self.install()
        self.assertIn("hcx_cleanup", dict(self.calls))
        self.assertNotIn("verify_hcx", dict(self.calls))

    def test_upload_hash_failure_prevents_install(self):
        self.wrong_hash = True
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            self.install()
        self.assertNotIn("install_modern_hcx", dict(self.calls))
        self.assertIn("hcx_cleanup", dict(self.calls))

    def test_cancellation_prevents_remote_mutation(self):
        self.cancel.set()
        with self.assertRaises(Exception):
            self.install()
        self.assertEqual(self.calls, [])

    def test_missing_libraries_require_regular_installation(self):
        self.versions = "libpcap1=1.10.4-1\n"
        with patch("modules.capture_services.openwrt.installation_sources.install_with_sources") as route:
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                self.install()
        route.assert_called_once()
        self.assertNotIn("install_modern_hcx", dict(self.calls))

    def test_incomplete_versions_cannot_build_install(self):
        with self.assertRaises(ValueError):
            primary_command("opkg", "/tmp/hcx.ipk", {})


if __name__ == "__main__":
    unittest.main()
