"""Regression checks for official legacy architecture evidence."""
import gzip
import hashlib
import json
import unittest
from unittest.mock import Mock

from .firmware_metadata import firmware_metadata


class FirmwareMetadataTests(unittest.TestCase):
    def legacy_http(self, target="x86/64", architecture="x86_64"):
        config = b"CONFIG_TARGET_x86=y\nCONFIG_TARGET_x86_64=y\n"
        packages = ("Package: base-files\nVersion: 1\nArchitecture: " + architecture +
            "\nFilename: base.ipk\nSize: 1\nSHA256sum: " + "a" * 64 + "\n\n").encode()
        responses = {"config.buildinfo": config, "packages/Packages.manifest": packages}
        responses["sha256sums"] = ("b" * 64 + " *openwrt-19.07.10-x86-64-rootfs.img\n" + "".join(hashlib.sha256(raw).hexdigest() + " *" + name + "\n"
            for name, raw in responses.items())).encode()
        def fetch(url):
            for name in ("profiles.json", "config.buildinfo", "sha256sums", "packages/Packages.manifest"):
                if url.endswith("/" + name):
                    if name not in responses:
                        raise FileNotFoundError(url)
                    return responses[name]
            raise AssertionError(url)
        return Mock(official=Mock(side_effect=fetch)), responses

    def test_current_profiles_use_explicit_target_and_architecture(self):
        http = Mock(official=Mock(return_value=json.dumps(dict(target="x86/64", arch_packages="x86_64")).encode()))
        data, evidence = firmware_metadata(http, "24.10.8", "x86/64")
        self.assertEqual(data["arch_packages"], "x86_64")
        self.assertEqual(len(evidence), 1)
        http.official.assert_called_once()

    def test_legacy_architecture_comes_from_verified_base_files(self):
        http, _ = self.legacy_http()
        data, evidence = firmware_metadata(http, "19.07.10", "x86/64")
        self.assertEqual(data["arch_packages"], "x86_64")
        self.assertTrue(data["legacy_metadata"])
        self.assertNotIn("profiles", data)
        self.assertEqual(len(evidence), 2)

    def test_tampered_build_evidence_is_rejected(self):
        http, responses = self.legacy_http()
        responses["config.buildinfo"] += b"modified"
        with self.assertRaisesRegex(ValueError, "checksum"):
            firmware_metadata(http, "19.07.10", "x86/64")

    def test_target_mismatch_is_rejected(self):
        http, _ = self.legacy_http()
        with self.assertRaisesRegex(ValueError, "target differs"):
            firmware_metadata(http, "19.07.10", "ramips/mt7621")

    def test_architecture_independent_base_files_is_rejected(self):
        http, _ = self.legacy_http(architecture="all")
        with self.assertRaisesRegex(ValueError, "device-specific"):
            firmware_metadata(http, "19.07.10", "x86/64")

    def test_old_single_subtarget_requires_matching_default_sdk(self):
        http, responses = self.legacy_http()
        config = b"CONFIG_TARGET_bcm53xx=y\nCONFIG_TARGET_DEVICE_bcm53xx_DEVICE_router=y\n"
        responses["config.buildinfo"] = config
        responses["sha256sums"] = (hashlib.sha256(config).hexdigest() + " *config.buildinfo\n" +
            hashlib.sha256(responses["packages/Packages.manifest"]).hexdigest() + " *packages/Packages.manifest\n" +
            "c" * 64 + " *openwrt-sdk-19.07.10-bcm53xx_gcc-7.5.0.tar.xz\n").encode()
        data, _ = firmware_metadata(http, "19.07.10", "bcm53xx/generic")
        self.assertEqual(data["target"], "bcm53xx/generic")
        with self.assertRaisesRegex(ValueError, "image target differs"):
            firmware_metadata(http, "19.07.10", "bcm53xx/other")

    def test_network_error_does_not_trigger_legacy_fallback(self):
        http = Mock(official=Mock(side_effect=RuntimeError("network unavailable")))
        with self.assertRaises(RuntimeError):
            firmware_metadata(http, "19.07.10", "x86/64")
        http.official.assert_called_once()
