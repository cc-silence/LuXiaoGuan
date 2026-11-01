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
                               stdout=outputs.get(name, ""), stderr="failure", timed_out=False, cancelled=False, command_name=name)

    def install(self, pm="opkg", sftp_available=True):
        from tests.hcx_bundle_fixture import hcx_fixture, prepare_hcx
        f = hcx_fixture(self.root, apk=pm == "apk")
        repo = prepare_hcx(f, self.root / "repository")
        variant = next(v for v in f.catalog["hcx_variants"]
                       if v["branch"] == ("25.12" if pm == "apk" else "24.10")
                       and v["architecture"] == f.device.package_architecture)
        self.digest = next(r["sha256"] for r in variant["packages"] if r["name"] == "hcxdumptool")
        with patch("modules.capture_services.openwrt.common_bundle_repository.CommonBundleRepository", return_value=repo):
            return install_primary(self.manager, f.device, sftp_available, self.cancel, MagicMock(), None, None)

    def test_opkg_only_uploads_primary_and_does_not_force_libraries(self):
        self.assertTrue(self.install())
        puts = self.manager._package_uploader.return_value.call_args_list
        self.assertEqual(len(puts), 1)
        command = dict(self.calls)["install_modern_hcx"]
        self.assertIn("--nodeps", command)
        self.assertNotIn("libopenssl", command)
        self.assertNotIn("libpcap", command)
        self.assertNotIn("*", command)
        self.assertNotIn("--force-overwrite", command)
        self.manager._install_online.assert_not_called()

    def test_missing_sftp_uses_shared_package_uploader(self):
        self.install(sftp_available=False)
        self.manager._package_uploader.assert_called_once_with(False, self.cancel, unittest.mock.ANY)
        upload = self.manager._package_uploader.return_value
        upload.assert_called_once()
        self.assertTrue(upload.call_args.args[1].endswith('.ipk'))

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

    def test_missing_library_install_cannot_hide_change_to_existing_library(self):
        self.versions = "libpcap1=1.10.4-1\n"
        def install_missing(*args):
            self.versions = "libpcap1=1.10.5-r2\nlibopenssl3=3.0.9-1\n"
        with patch("modules.capture_services.openwrt.installation_sources.install_with_sources",
                   side_effect=install_missing):
            with self.assertRaisesRegex(RuntimeError, "Existing shared library changed"):
                self.install()
        self.assertNotIn("install_modern_hcx", dict(self.calls))

    def test_cleanup_exception_preserves_original_install_failure(self):
        original = self.run_command
        def run(name, command, **kwargs):
            if name == "hcx_cleanup":
                raise OSError("cleanup disconnected")
            return original(name, command, **kwargs)
        self.manager.command_runner.run.side_effect = run
        self.fail_install = True
        with self.assertRaisesRegex(RuntimeError, "install_modern_hcx"):
            self.install()

    def test_timeout_prevents_upload_and_still_cleans_unique_directory(self):
        original = self.run_command
        def run(name, command, **kwargs):
            result = original(name, command, **kwargs)
            if name == "hcx_prepare":
                result.timed_out = True
            return result
        self.manager.command_runner.run.side_effect = run
        with self.assertRaises(TimeoutError):
            self.install()
        self.manager._package_uploader.assert_not_called()
        self.assertIn("hcx_cleanup", dict(self.calls))

    def test_incomplete_versions_cannot_build_install(self):
        with self.assertRaises(ValueError):
            primary_command("opkg", "/tmp/hcx.ipk", {})


if __name__ == "__main__":
    unittest.main()
