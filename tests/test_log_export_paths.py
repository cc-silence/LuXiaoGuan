"""Shared diagnostic directory preferences survive new exporters and missing folders."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from modules.config_services.config_store import ConfigPair
from modules.log_export_paths import choose_log_export_path, export_directory


class ExportPathTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        self.destination=self.root / "selected"
        self.destination.mkdir()
        self.default=self.root / "Downloads"
        self.store=ConfigPair(self.root/"primary.json",self.root/"mirror.json")
        for target, value in (("modules.log_export_paths._store",self.store),
                              ("modules.config_manager.user_downloads_dir",self.default)):
            mocked=patch(target,return_value=value)
            mocked.start();self.addCleanup(mocked.stop)

    def test_each_category_reuses_persisted_directory_and_keeps_own_filename(self):
        picked=str(self.destination/"first.zip")
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName",return_value=(picked,"ZIP")):
            self.assertEqual(choose_log_export_path(None,"Export","signal"),picked)
        for category in ("router-terminal","update","auto-capture","login"):
            with patch("modules.log_services.build_export_filename",return_value=category+".zip"), patch(
                    "PySide6.QtWidgets.QFileDialog.getSaveFileName",return_value=("","")) as picker:
                choose_log_export_path(None,"Export",category)
                self.assertEqual(Path(picker.call_args.args[2]),self.destination/(category+".zip"))
        self.assertEqual(ConfigPair(self.root/"primary.json",self.root/"mirror.json").read_newest_json()["directory"],str(self.destination))

    def test_removed_folder_or_file_path_falls_back(self):
        self.store.write_json(dict(directory=str(self.destination)))
        self.destination.rmdir()
        self.assertEqual(export_directory(),self.default)
        self.destination.write_text("not a directory",encoding="utf-8")
        self.assertEqual(export_directory(),self.default)

    def test_cancel_leaves_last_directory_unchanged(self):
        self.store.write_json(dict(directory=str(self.destination)))
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName",return_value=("","")):
            choose_log_export_path(None,"Export","signal")
        self.assertEqual(export_directory(),self.destination)

    def test_invalid_saved_value_uses_default(self):
        for value in (None,42,"relative-folder"):
            self.store.write_json(dict(directory=value))
            self.assertEqual(export_directory(),self.default)

    def test_preference_write_failure_does_not_prevent_export(self):
        picked=str(self.destination/"first.zip")
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName",return_value=(picked,"ZIP")), patch.object(
                self.store,"write_json",side_effect=OSError("readonly")), self.assertLogs("modules.log_export_paths",level="WARNING"):
            self.assertEqual(choose_log_export_path(None,"Export","signal"),picked)
