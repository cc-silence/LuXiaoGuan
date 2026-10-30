"""Shipped capture tools remain compatible with the old update path allowlist."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from modules.platform_services.bundled_tools import bundled_tools
from modules.app_update_services.catalog import managed_path
from scripts.release_layout import seed_assets


class BundledToolTests(unittest.TestCase):
    def test_source_and_frozen_paths(self):
        root=Path("application")
        self.assertEqual(bundled_tools(root,frozen=False),root/"tools")
        self.assertEqual(bundled_tools(root,frozen=True),root/"bin/tools")

    def test_release_stages_only_program_resources_not_downloaded_offline_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/"source";target=Path(directory)/"package"
            (root/"tools/scripts").mkdir(parents=True)
            (root/"tools/scripts/hcx_autocap.sh").write_bytes(b"#!/bin/sh\n")
            (root/"offline_packages").mkdir()
            (root/"offline_packages/private-cache").write_bytes(b"not a shipped resource")
            (root/"config").mkdir();(root/"config/app_update.json").write_text("{}",encoding="utf-8")
            with patch("scripts.release_layout.ROOT",root):seed_assets(target)
            script=target/"bin/tools/scripts/hcx_autocap.sh"
            self.assertTrue(script.is_file())
            self.assertEqual(managed_path(script.relative_to(target).as_posix()),"bin/tools/scripts/hcx_autocap.sh")
            self.assertFalse((target/"tools").exists())
            self.assertFalse((target/"offline_packages").exists())
            self.assertEqual(bundled_tools(target,frozen=True)/"scripts/hcx_autocap.sh",script)

    def test_boot_script_uses_the_same_shipped_resource_root(self):
        from modules.capture_services.openwrt.auto_capture_boot_manager import get_local_script_path
        with patch("modules.capture_services.openwrt.auto_capture_boot_manager.BASE_DIR",Path("app")),patch("sys.frozen",True,create=True):
            self.assertEqual(get_local_script_path(),Path("app/bin/tools/scripts/hcx_autocap.sh"))


if __name__=="__main__":unittest.main()
