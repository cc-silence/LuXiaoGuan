"""Cache lifetime, path batching and capture settings commit boundaries."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.ui_scale_manager import UiScaleManager
from modules.config_manager import make_portable_path
from modules.config_services.capture_settings_manager import CaptureSettingsManager
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
from modules.main_pages.packet_capture.openwrt.capture_settings_dialog import CaptureSettingsDialog


class StyleCacheTests(unittest.TestCase):
    def setUp(self):
        UiScaleManager._scaled_qss.cache_clear()

    def tearDown(self):
        UiScaleManager._scaled_qss.cache_clear()

    def test_cache_reuses_text_and_tracks_scale_and_font(self):
        text = 'QLabel {font-family: "Segoe UI"; margin: 8px;}'
        a = UiScaleManager(100)
        expected = a.scale_stylesheet(text)
        self.assertEqual(UiScaleManager(100).scale_stylesheet(text), expected)
        self.assertEqual(UiScaleManager._scaled_qss.cache_info().hits, 1)
        self.assertNotEqual(UiScaleManager(150).scale_stylesheet(text), expected)
        self.assertNotEqual(UiScaleManager(100,'Arial').scale_stylesheet(text), expected)

    def test_cache_is_bounded_and_retains_no_manager(self):
        import weakref, gc
        scale = UiScaleManager(100)
        reference = weakref.ref(scale)
        for i in range(24):scale.scale_stylesheet(f'QLabel {{margin: {i}px;}}')
        self.assertEqual(UiScaleManager._scaled_qss.cache_info().currsize, 16)
        del scale;gc.collect()
        self.assertIsNone(reference())


class SettingsCommitTests(unittest.TestCase):
    def setUp(self):
        self.parent = QWidget()
        self.service = Mock()
        self.service.verified_history_snapshot.return_value = {}
        self.service.openwrt_snapshot = OpenWrtSessionSnapshot()
        settings = copy.deepcopy(CaptureSettingsManager.DEFAULTS['openwrt'])
        settings['password_dictionaries'] = []
        settings['password_dictionary'] = ''
        self.dialog = CaptureSettingsDialog(self.service, settings, self.parent)
        self.changed = Mock()
        self.dialog.settingsChanged.connect(self.changed)

    def tearDown(self):
        self.dialog.page.cancel_preview()
        self.dialog.close()
        self.parent.close()
        self.dialog.deleteLater();self.parent.deleteLater();APP.processEvents()

    def test_closing_unchanged_form_does_not_save(self):
        self.dialog.reject()
        self.changed.assert_not_called()

    def test_immediate_edit_is_not_saved_again_on_close(self):
        control = self.dialog.page.active_deauth
        control.setChecked(not control.isChecked())
        self.changed.assert_called_once()
        self.dialog.reject()
        self.changed.assert_called_once()

    def test_unsubmitted_spin_value_is_flushed_on_close(self):
        self.dialog.page.refresh_seconds.setValue(19)
        self.changed.assert_not_called()
        self.dialog.reject()
        self.changed.assert_called_once()
        self.assertEqual(self.dialog.page.collect_settings()['refresh_seconds'],19)

    def test_collecting_existing_dictionary_rows_does_not_resolve_again(self):
        self.dialog.page.password_dictionary.append_paths(['fixture-one.txt'],emit=False)
        with patch('modules.config_services.capture_settings_manager.resolve_portable_path', side_effect=AssertionError('Unexpected path IO')):
            data=self.dialog.page.collect_settings()
        self.assertEqual(len(data['password_dictionaries']),1)


class PathBatchTests(unittest.TestCase):
    def test_one_root_snapshot_preserves_portable_path_results(self):
        from modules.config_manager import portable_path_roots
        with TemporaryDirectory() as folder:
            manager = CaptureSettingsManager(Path(folder)/'settings.json')
            data = copy.deepcopy(manager.DEFAULTS)
            paths=[str(Path(folder)/f'fixture-{i}.txt') for i in range(12)]
            data['openwrt']['password_dictionaries']=paths
            expected=[make_portable_path(p) for p in paths]
            roots=portable_path_roots()
            with patch('modules.config_services.capture_settings_manager.portable_path_roots',return_value=roots) as snapshot, patch('modules.config_manager.portable_path_roots',side_effect=AssertionError('Repeated roots')):
                result=manager.normalize(data)
            snapshot.assert_called_once()
            self.assertEqual(result['openwrt']['password_dictionaries'],expected)
