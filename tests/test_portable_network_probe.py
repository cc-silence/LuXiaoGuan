"""Portable-report behavior without network or installed application state."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from scripts.slow_network_probe.runner import Runner
from scripts.slow_network_probe.presentation import ChineseRunner


class PortableProbeTests(unittest.TestCase):
    def test_local_connection_denied_has_safe_error_code(self):
        from scripts.slow_network_probe.worker import run_request
        with tempfile.TemporaryDirectory() as directory:
            options = dict(url="https://example.test", directory=directory,
                profile=dict(connect=1, idle=1), mode="metadata", limit=64,
                proxy="", expected=None, headers={})
            with patch("requests.Session") as session:
                session.return_value.__enter__.return_value.get.side_effect = PermissionError(13, "sensitive")
                result = run_request(options)
            self.assertEqual(result["reason"], "connection_denied")
            self.assertIn({"errno": 13}, result["system_error_codes"])
            self.assertNotIn("sensitive", json.dumps(result))

    def test_invalid_snapshot_is_reported_not_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"app_manifest.json").write_bytes(b"invalid")
            runner = Runner(root/"report.json", threading.Event(), seed_directory=root)
            def reject(_):
                raise InvalidSignature()
            self.assertIsNone(runner.seed("app_manifest", reject))
            record = runner.report["records"][-1]
            self.assertEqual(record["outcome"], "failed")
            self.assertEqual(record["validation_error_type"], "InvalidSignature")

    def test_snapshot_does_not_become_online_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"app_manifest.json").write_bytes(b"fixture")
            runner = Runner(root/"report.json", threading.Event(), seed_directory=root)
            self.assertEqual(runner.seed("app_manifest", lambda _: {"signed": True}), {"signed": True})
            record = runner.report["records"][-1]
            self.assertEqual(record["outcome"], "snapshot_loaded")
            self.assertNotIn("route", record)

    def test_share_zip_contains_chinese_summary_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"report.json"
            runner = ChineseRunner(output, threading.Event())
            runner.report["records"].append(dict(target="common_archive", profile="long_wait",
                outcome="failed", reason="connection_timeout", received_bytes=0, elapsed_seconds=3))
            archive = runner.export()
            with zipfile.ZipFile(archive) as package:
                self.assertIsNone(package.testzip())
                self.assertEqual(set(package.namelist()), {"report.json", "report.txt"})
                self.assertEqual(len(json.loads(package.read("report.json"))["records"]), 1)
                text = package.read("report.txt").decode("utf-8-sig")
                self.assertIn("\u5efa\u7acb\u8fde\u63a5\u8d85\u65f6", text)
                self.assertIn("\u4e0b\u8f7d\u5e38\u7528\u4f9d\u8d56\u5305", text)


if __name__ == "__main__":
    unittest.main()
