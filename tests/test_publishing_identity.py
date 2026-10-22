"""Publisher credentials and Cloudflare commands stay isolated from legacy defaults."""
import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from modules.app_update_services.manifest import canonical
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
from scripts.publishing import cloudflare, signing


class PublishingIdentityTests(unittest.TestCase):
    def test_commands_use_isolated_device_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            entry = Path(folder)/"node_modules/wrangler/bin/wrangler.js"
            entry.parent.mkdir(parents=True); entry.write_text("", encoding="utf-8")
            with patch.object(cloudflare, "INFRA", Path(folder)):
                command = cloudflare.command("deploy")
                self.assertEqual(command[-2:], ["--profile", cloudflare.PROFILE])
                query = cloudflare.command("whoami", "--json")
                self.assertEqual(query[-2:], ["--cwd", str(Path(folder))])
                self.assertNotIn("--profile", query)
                command = cloudflare.command("login", "--device", "--browser=false")
                self.assertNotIn("--profile", command)
                with self.assertRaises(ValueError):
                    cloudflare.command("auth", "create", "default")

    def test_old_environment_credentials_cannot_override_account(self):
        values = dict(CLOUDFLARE_API_TOKEN="old-secret", CF_API_TOKEN="old-secret",
            CLOUDFLARE_ACCOUNT_ID="old-account", AWS_SECRET_ACCESS_KEY="old-secret")
        with patch.dict(os.environ, values), patch("modules.network_policy.allowed_channels", return_value=[]):
            env = cloudflare.environment()
        for name in values:
            self.assertNotIn(name, env)
        self.assertEqual(env["CLOUDFLARE_AUTH_USE_KEYRING"], "true")
        self.assertEqual(env["XDG_CONFIG_HOME"], str(cloudflare.AUTH_CONFIG))

    def test_legacy_home_config_cannot_bypass_private_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/".wrangler").mkdir()
            with patch("pathlib.Path.home", return_value=Path(folder)):
                with self.assertRaises(ValueError):
                    cloudflare.environment()

    def test_signing_key_lookup_uses_only_routermanager_target(self):
        with patch.object(signing, "read_generic_password", return_value="") as read:
            with self.assertRaises(ValueError):signing.signing_key()
        read.assert_called_once_with("RouterManager:Publisher:Ed25519:v1")

    def test_legacy_offline_product_is_rejected_even_with_a_valid_signature(self):
        key = Ed25519PrivateKey.generate()
        public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
            serialization.PublicFormat.Raw)).decode()
        payload = dict(schema=1, product="WinBox", purpose="openwrt-common-offline")
        raw = canonical(dict(payload=payload, signature=base64.b64encode(key.sign(canonical(payload))).decode()))
        with self.assertRaises(ValueError):verify_bundle_manifest(raw, public_key=public)
