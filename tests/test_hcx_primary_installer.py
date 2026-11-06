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

    def test_valid_local_bundle_never_fetches_primary_online(self):
        with patch("modules.capture_services.openwrt.hcx_online.fetch_bytes",
                   side_effect=AssertionError("Local designated body must take priority")):
            self.assertTrue(self.install())

    def test_no_bundle_online_body_reuses_installer_and_preserves_system_libraries(self):
        from modules.capture_services.openwrt.hcx_online import primary_row
        from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
        for pm in ("opkg", "apk"):
            with self.subTest(pm=pm):
                self.calls.clear()
                self.manager.reset_mock()
                device = SimpleNamespace(version="25.12.6" if pm == "apk" else "23.05.6",
                                         kernel_version="6.12.1" if pm == "apk" else "5.15.189",
                                         package_manager=pm, package_architecture="mipsel_24kc")
                row = primary_row(device)
                self.digest = row["sha256"]
                branch = "25.12" if pm == "apk" else "24.10"
                path = Path(__file__).resolve().parents[1] / "tools/hc_offline_pack" / branch / device.package_architecture / "pkgs" / row["filename"]
                raw = path.read_bytes()
                repo = CommonBundleRepository(self.root / (pm + "-missing"), kind="capture")
                with patch("modules.capture_services.openwrt.common_bundle_repository.CommonBundleRepository", return_value=repo), \
                     patch("modules.capture_services.openwrt.hcx_online.fetch_bytes", return_value=raw) as fetch:
                    self.assertTrue(install_primary(self.manager, device, True, self.cancel,
                                                   MagicMock(), None, None))
                fetch.assert_called_once()
                uploader = self.manager._package_uploader.return_value
                uploader.assert_called_once()
                self.assertFalse(Path(uploader.call_args.args[0]).exists())
                self.assertIn("install_modern_hcx", dict(self.calls))
                self.assertIn("verify_hcx", dict(self.calls))
                self.manager._install_online.assert_not_called()
                if pm == "opkg":
                    self.assertNotIn("libopenssl", dict(self.calls)["install_modern_hcx"])
                else:
                    self.assertIn("libopenssl3=3.0.9-1", dict(self.calls)["install_modern_hcx"])

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

    def test_apk_installed_libraries_use_actual_shell_probe(self):
        from tests.test_hcx_library_probe import run_probe
        original = self.run_command
        def run(name, command, **kwargs):
            result = original(name, command, **kwargs)
            if name == "hcx_library_probe":
                result.stdout = run_probe("apk", command=command)
            return result
        self.manager.command_runner.run.side_effect = run
        self.assertTrue(self.install("apk"))
        self.manager._install_online.assert_not_called()
        self.manager._install_offline.assert_not_called()
        self.assertIn("libpcap1=1.10.6-r1", dict(self.calls)["install_modern_hcx"])
        self.assertIn("libopenssl3=3.5.9-r1", dict(self.calls)["install_modern_hcx"])

    def test_apk_missing_library_is_recognized_after_regular_install(self):
        from tests.test_hcx_library_probe import run_probe
        original = self.run_command
        present = {"pcap": False}
        def run(name, command, **kwargs):
            result = original(name, command, **kwargs)
            if name == "hcx_library_probe":
                result.stdout = run_probe("apk", command=command, pcap=present["pcap"])
            return result
        self.manager.command_runner.run.side_effect = run
        def install_missing(*args):
            present["pcap"] = True
        with patch("modules.capture_services.openwrt.installation_sources.install_with_sources",
                   side_effect=install_missing) as route:
            self.assertTrue(self.install("apk"))
        route.assert_called_once()
        self.assertIn("libopenssl3=3.5.9-r1", dict(self.calls)["install_modern_hcx"])

    def test_real_query_failure_stops_before_any_library_install_or_upload(self):
        from tests.test_hcx_library_probe import probe_result
        for pm in ("opkg", "apk"):
            with self.subTest(manager=pm):
                self.calls.clear()
                self.manager.reset_mock()
                original = self.run_command
                def run(name, command, **kwargs):
                    result = original(name, command, **kwargs)
                    if name == "hcx_library_probe":
                        actual = probe_result(pm, command=command, query_error=True)
                        result.exit_code, result.stdout, result.stderr = actual.returncode, actual.stdout, actual.stderr
                    return result
                self.manager.command_runner.run.side_effect = run
                with self.assertRaisesRegex(RuntimeError, "database read failed"):
                    self.install(pm)
                self.manager._install_online.assert_not_called()
                self.manager._install_offline.assert_not_called()
                self.manager._package_uploader.assert_not_called()
                self.assertNotIn("hcx_prepare", dict(self.calls))

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
            with self.assertRaisesRegex(RuntimeError, "libopenssl3"):
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

    def test_additional_architectures_reuse_library_preserving_installer(self):
        from contextlib import contextmanager
        for pm in ("opkg", "apk"):
            for arch in ("x86_64", "arm_cortex-a7_neon-vfpv4"):
                with self.subTest(pm=pm, arch=arch):
                    self.calls.clear()
                    self.manager.reset_mock()
                    path = self.root / ("hcx.apk" if pm == "apk" else "hcx.ipk")
                    path.write_bytes(b"body")
                    self.digest = hashlib.sha256(b"body").hexdigest()
                    row = dict(name="hcxdumptool",version="6.3.4-r1",architecture=arch,sha256=self.digest)
                    @contextmanager
                    def body(*_):
                        yield path, row
                    device = SimpleNamespace(version="23.05.0-rc2",kernel_version="5.15.118",
                                             package_manager=pm,package_architecture=arch)
                    with patch("modules.capture_services.openwrt.hcx_bundle.primary_package",body):
                        self.assertTrue(install_primary(self.manager,device,True,self.cancel,MagicMock(),None,None))
                    self.assertIn("install_modern_hcx",dict(self.calls))
                    self.manager._install_online.assert_not_called()
                    self.manager._install_offline.assert_not_called()
                    if pm == "apk":
                        self.assertIn("libopenssl3=3.0.9-1",dict(self.calls)["install_modern_hcx"])
                    else:
                        self.assertNotIn("libopenssl",dict(self.calls)["install_modern_hcx"])

    def test_both_formats_stop_on_plan_timeout_or_cancel_after_upload(self):
        for pm in ("opkg", "apk"):
            for fault in ("plan_timeout", "cancel_after_upload"):
                with self.subTest(pm=pm, fault=fault):
                    self.calls.clear()
                    self.cancel.clear()
                    original = self.run_command
                    def run(name, command, **kwargs):
                        result = original(name, command, **kwargs)
                        if fault == "plan_timeout" and name == "hcx_primary_plan":
                            result.timed_out = True
                        if fault == "cancel_after_upload" and name == "hcx_uploaded_hash":
                            self.cancel.set()
                        return result
                    self.manager.command_runner.run.side_effect = run
                    with self.assertRaises((TimeoutError, RuntimeError)):
                        self.install(pm)
                    self.assertNotIn("install_modern_hcx", dict(self.calls))
                    self.assertIn("hcx_cleanup", dict(self.calls))

    def test_wrong_version_after_commit_is_never_reported_as_success(self):
        for pm in ("opkg", "apk"):
            with self.subTest(pm=pm):
                self.calls.clear()
                original = self.run_command
                def run(name, command, **kwargs):
                    result = original(name, command, **kwargs)
                    if name == "verify_hcx":
                        result.stdout = "hcxdumptool 6.3.5"
                    return result
                self.manager.command_runner.run.side_effect = run
                with self.assertRaisesRegex(RuntimeError, "version verification failed"):
                    self.install(pm)
                self.assertIn("install_modern_hcx", dict(self.calls))
                self.assertIn("hcx_cleanup", dict(self.calls))

    def test_cleanup_failure_after_verified_install_keeps_success_and_reports_warning(self):
        for pm in ("opkg", "apk"):
            with self.subTest(pm=pm):
                self.calls.clear()
                original = self.run_command
                def run(name, command, **kwargs):
                    if name == "hcx_cleanup":
                        raise OSError("cleanup connection lost")
                    return original(name, command, **kwargs)
                self.manager.command_runner.run.side_effect = run
                with patch("modules.capture_services.openwrt.offline_cleanup.report_cleanup_warning") as warn:
                    self.assertTrue(self.install(pm))
                warn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
