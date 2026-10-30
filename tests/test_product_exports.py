"""The independent application brands new exports and preserves explicit legacy paths."""
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from modules.log_services import build_export_filename, export_logs
from modules.error_logs import ErrorFileHandler
from modules.config_services.capture_settings_manager import CaptureSettingsManager
from modules.release_identity import PACKAGE_DIRECTORY


class ProductExportTests(unittest.TestCase):
    def test_export_name_readme_and_redaction(self):
        with patch('modules.log_services.getpass.getuser',return_value='CC'):
            name=build_export_filename("router/login")
        self.assertTrue(name.startswith("CC_LuXiaoGuan_router_login_"))
        with tempfile.TemporaryDirectory() as folder:
            destination=Path(folder)/name
            export_logs(destination,"router/login",[("password=do-not-export","error"),("diagnostic fixture","info")],app_version="1.0.2_alpha")
            with zipfile.ZipFile(destination) as archive:
                readme=archive.read("README.txt").decode("utf-8")
                self.assertTrue(readme.startswith("LuXiaoGuan 1.0.2_alpha\n"))
                self.assertNotIn("WinBox",readme)
                self.assertNotIn("do-not-export",archive.read("information.txt").decode("utf-8"))

    def test_error_log_header_uses_current_product(self):
        store=type("Store",(),{"version":"1.0.2_alpha"})()
        handler=ErrorFileHandler(store)
        try:
            text=handler.format(logging.LogRecord("fixture",logging.ERROR,"",0,"failure",(),None))
            self.assertIn("LuXiaoGuan 1.0.2_alpha",text)
            self.assertNotIn("WinBox",text)
        finally:handler.close()


class LegacyOutputPathTests(unittest.TestCase):
    def test_new_defaults_and_existing_legacy_capture_paths(self):
        from modules.config_services.capture_settings_manager import CAPTURE_OUTPUT_DIR_NAME
        from modules.config_manager import DownloadConfigManager, WINBOX_DOWNLOAD_DIR_NAME
        self.assertEqual(CAPTURE_OUTPUT_DIR_NAME,"\u8def\u5c0f\u7ba1\u6293\u5305")
        self.assertEqual(CaptureSettingsManager.default_output_dir().name,CAPTURE_OUTPUT_DIR_NAME)
        self.assertEqual(CaptureSettingsManager.normalized_output_dir("D:/old/winbox\u6293\u5305").name,"winbox\u6293\u5305")
        self.assertEqual(WINBOX_DOWNLOAD_DIR_NAME,"\u8def\u5c0f\u7ba1\u4e0b\u8f7d")
        self.assertEqual(DownloadConfigManager().normalized_download_dir("D:/old/winbox\u4e0b\u8f7d").name,"winbox\u4e0b\u8f7d")


class ShortcutAssetTests(unittest.TestCase):
    def test_seed_has_router_icon_and_no_first_launch_record(self):
        import os
        from unittest.mock import patch
        from scripts.release_layout import seed_assets
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);user=root/"user";user.mkdir()
            (user/".desktop-shortcut-prompted").write_text("1",encoding="utf-8")
            with patch.dict(os.environ,{"ROUTERMANAGER_USER_CONFIG_DIR":str(user)}):
                seed_assets(root/"assets")
            self.assertTrue((root/"assets/images/icons/app-router.ico").is_file())
            self.assertFalse(list((root/"assets").rglob(".desktop-shortcut-prompted")))
            self.assertFalse(list((root/"assets").rglob(".first_launch_done")))
