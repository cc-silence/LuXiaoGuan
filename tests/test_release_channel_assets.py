"""Packages generate public defaults even when source user config is absent."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import release_layout
from scripts.update_channel_assets import default_update_config
from modules.app_update_services.channel import GATEWAY


class ReleaseChannelAssetsTests(unittest.TestCase):
    def test_windows_assets_do_not_require_source_user_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"source";root.mkdir()
            target=Path(tmp)/"assets"
            with patch.object(release_layout,"ROOT",root):
                release_layout.seed_assets(target)
            self.assertFalse((root/"config/app_update.json").exists())
            self.assertEqual(json.loads((target/"config/app_update.json").read_text(encoding="utf-8")),default_update_config("win-x64"))

    def test_platform_defaults_keep_signed_channel_routes(self):
        for platform,suffix in (("win-x64","/manifest"),("linux-x64","/manifest?platform=linux-x64")):
            value=default_update_config(platform)
            self.assertTrue(value["enabled"])
            self.assertTrue(value["github_mirror"])
            self.assertEqual(value["manifest_url"],GATEWAY+suffix)
        with self.assertRaises(ValueError):default_update_config("unsupported")
