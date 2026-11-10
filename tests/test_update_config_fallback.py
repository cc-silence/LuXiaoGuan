"""Missing update assets must not prevent secure update discovery."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from modules.app_update_services.config import default_update_config, load_update_config
from modules.app_update_services.checker import UpdateChecker
from modules.network_policy import NetworkChannel


class UpdateConfigFallbackTests(unittest.TestCase):
    def test_missing_directory_uses_platform_defaults_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"missing/config.json"
            for platform in ("win-x64", "linux-x64"):
                self.assertEqual(load_update_config(path, platform), default_update_config(platform))
            self.assertFalse(path.parent.exists())

    def test_existing_choices_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"config.json"
            value = dict(default_update_config("win-x64"), enabled=False, network_mode="direct")
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(load_update_config(path, "win-x64"), value)

    def test_present_invalid_or_oversized_config_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"config.json"
            for data in (b"{", b"[]", b"x"*4097, b'{"network_mode":"unknown"}', b"\xff"):
                with self.subTest(data=data[:30]):
                    path.write_bytes(data)
                    with self.assertRaises((ValueError, UnicodeError)):
                        load_update_config(path, "win-x64")

    def test_missing_config_reaches_discovery_for_both_platforms(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"missing/config.json"
            for platform in ("win-x64", "linux-x64"):
                with self.subTest(platform=platform), patch("modules.platform_services.app_update_access.read_code", return_value=""), patch("modules.app_update_services.checker.require_package_platform", return_value=platform), patch("modules.app_update_services.sources.manifest_routes", return_value=[NetworkChannel("direct", "direct")]):
                    checker = UpdateChecker("1.0.48_alpha", path)
                    errors = []
                    checker.failed.connect(lambda *args: errors.append(args))
                    try:
                        with patch.object(checker, "_next_channel") as dispatch:
                            self.assertTrue(checker.check(manual=True))
                            dispatch.assert_called_once()
                        self.assertEqual(errors, [])
                        self.assertEqual(checker._url, default_update_config(platform)["manifest_url"])
                        self.assertEqual(checker.test_code, "")
                    finally:
                        checker.cancel()
                        checker.deleteLater()
                        app.processEvents()
