"""Directory routing, persistent settings and read-only-installation regressions."""
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from modules.platform_services import application_paths as paths
from modules.config_services.config_store import ConfigPair

ROOT = Path(__file__).resolve().parents[1]


class ApplicationPathsTests(unittest.TestCase):
    def test_linux_defaults_and_valid_xdg_overrides(self):
        self.assertEqual(paths.linux_user_directory('config', {}, '/home/test'),
                         PurePosixPath('/home/test/.config/LuXiaoGuan'))
        for kind, variable, default in (
                ('config', 'XDG_CONFIG_HOME', '.config'),
                ('state', 'XDG_STATE_HOME', '.local/state'),
                ('data', 'XDG_DATA_HOME', '.local/share')):
            with self.subTest(kind=kind):
                self.assertEqual(paths.linux_user_directory(kind, {variable: '/custom'}, '/home/test'),
                                 PurePosixPath('/custom/LuXiaoGuan'))
                for value in ('', 'relative', '~/not-expanded', 'C:/not-posix-absolute'):
                    self.assertEqual(paths.linux_user_directory(kind, {variable: value}, '/home/test'),
                                     PurePosixPath('/home/test') / default / 'LuXiaoGuan')

    def test_windows_locations_stay_compatible(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(paths, 'is_linux', return_value=False), \
                patch.dict(os.environ, {'LOCALAPPDATA': folder}, clear=True):
            root = Path(folder)
            app = root / 'application'
            self.assertEqual(paths.user_config_dir(), root / 'LuXiaoGuan/config')
            self.assertEqual(paths.configuration_dir(app), app / 'config')
            self.assertEqual(paths.user_state_dir(), root / 'LuXiaoGuan')
            self.assertEqual(paths.work_data_dir(app), app / 'RouterManagerData')
            self.assertEqual(paths.error_log_dir(app), app / 'RouterManagerData/error-logs')

    def test_automatic_capture_fallback_never_writes_linux_install_tree(self):
        from types import SimpleNamespace
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / 'user-data'
            with patch.object(paths, 'is_linux', return_value=True), patch.object(paths, 'linux_user_directory', return_value=data / 'RouterManager'):
                owner = SimpleNamespace(settings={})
                output = AutoCaptureService.get_output_dir(SimpleNamespace(owner=owner))
                self.assertEqual(output, data / 'RouterManager/captures')
                self.assertTrue(output.is_dir())
                custom = Path(folder) / 'custom'
                custom.mkdir()
                owner.settings['output_dir'] = str(custom)
                self.assertEqual(AutoCaptureService.get_output_dir(SimpleNamespace(owner=owner)), custom)
            with patch.object(paths, 'is_linux', return_value=False):
                self.assertEqual(paths.automatic_capture_directory(Path(folder)), Path(folder) / 'dist/captures')

    def test_explicit_user_config_override_and_relative_linux_rejection(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(paths, 'is_linux', return_value=True):
            with patch.dict(os.environ, {'ROUTERMANAGER_USER_CONFIG_DIR': folder}):
                self.assertEqual(paths.user_config_dir(), Path(folder))
            with patch.dict(os.environ, {'ROUTERMANAGER_USER_CONFIG_DIR': 'relative'}):
                with self.assertRaises(ValueError):
                    paths.user_config_dir()

    def test_resolution_does_not_create_directories(self):
        with patch.object(paths.Path, 'mkdir', side_effect=AssertionError('unexpected filesystem write')):
            paths.linux_user_directory('data', {}, '/home/test')
            with patch.object(paths, 'is_linux', return_value=False):
                paths.configuration_dir(Path('application'))

    def test_portable_tokens_allow_legacy_separators_without_changing_literal_names(self):
        with patch.object(paths, 'is_linux', return_value=True):
            for value in ('{APP_DIR}/bin/test.txt', r'{APP_DIR}\bin\test.txt'):
                self.assertEqual(paths.expand_portable_tokens(value, [('{APP_DIR}', '/opt/app')]),
                                 '/opt/app/bin/test.txt')
            literal = r'/home/test/literal\name'
            self.assertEqual(paths.expand_portable_tokens(literal, [('{APP_DIR}', '/opt/app')]), literal)
            self.assertEqual(paths.join_portable_token('{APP_DIR}', PurePosixPath('bin/file')),
                             '{APP_DIR}/bin/file')

    def test_new_linux_directories_request_private_permissions_without_chmod(self):
        original = Path.mkdir
        observed = []
        def create(path, *args, **kwargs):
            observed.append((path, kwargs.get('mode')))
            return original(path, *args, **kwargs)
        with tempfile.TemporaryDirectory() as folder, patch.object(paths, 'is_linux', return_value=True):
            target = Path(folder) / 'new-config' / 'RouterManager' / 'capture'
            with patch.object(Path, 'mkdir', create):
                paths.ensure_directory(target)
            self.assertTrue(target.is_dir())
            self.assertEqual(len(observed), 3)
            self.assertTrue(all(mode == 0o700 for _, mode in observed))
            with patch.object(Path, 'mkdir', side_effect=AssertionError('existing directory recreated')):
                paths.ensure_directory(target)


class ConfigStorageTests(unittest.TestCase):
    def test_identical_primary_and_mirror_do_not_repeat_disk_io(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'settings.json'
            pair = ConfigPair(target, target)
            with patch.object(pair, '_write_file', wraps=pair._write_file) as write:
                pair.write_json({'theme': 'dark'})
                self.assertEqual(write.call_count, 1)
            with patch.object(pair, '_read_single_json', wraps=pair._read_single_json) as read:
                self.assertEqual(pair.read_newest_json(), {'theme': 'dark'})
                self.assertEqual(read.call_count, 1)
            with patch.object(pair, '_write_file', side_effect=PermissionError('read-only')):
                with self.assertRaises(PermissionError):
                    pair.write_json({'theme': 'light'})

    def test_windows_distinct_mirror_keeps_recovery_behavior(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pair = ConfigPair(root / 'user/settings.json', root / 'app/settings.json')
            pair.write_json({'value': 7})
            pair.primary.unlink()
            self.assertEqual(pair.read_newest_json(), {'value': 7})
            with patch.object(pair, '_write_file', side_effect=[None, PermissionError('mirror read-only')]):
                pair.write_json({'value': 8})

    def test_linux_settings_work_and_logs_are_user_owned_across_process_restarts(self):
        script = r'''
import json, os, sys
from pathlib import Path
from modules.platform_services import application_paths as paths
root = Path(sys.argv[1])
paths.is_linux = lambda: True
paths.linux_user_directory = lambda kind: root / kind
os.environ.pop('ROUTERMANAGER_USER_CONFIG_DIR', None)
from modules import config_manager as config
from modules.config_services.capture_settings_manager import CaptureSettingsManager
from modules.error_logs import default_error_log_dir, ErrorLogStore
from modules.download_services.download_mode_prompt_preferences import DownloadModePromptPreferences
assert config.CONFIG_DIR == root / 'config'
assert config.USER_RUNTIME_DIR == root / 'state'
assert config.WORK_DATA_DIR == root / 'data'
assert default_error_log_dir() == root / 'state/error-logs'
# Model an immutable installation: all application writes must stay in the user root.
original_open, original_mkdir = Path.open, Path.mkdir
def user_open(path, mode='r', *args, **kwargs):
    if any(flag in mode for flag in ('w', 'a', 'x', '+')):
        assert path.absolute().is_relative_to(root), ('write outside user root', path)
    return original_open(path, mode, *args, **kwargs)
def user_mkdir(path, *args, **kwargs):
    assert path.absolute().is_relative_to(root), ('mkdir outside user root', path)
    return original_mkdir(path, *args, **kwargs)
Path.open, Path.mkdir = user_open, user_mkdir
manager = config.ConfigManager()
if sys.argv[2] == 'write':
    settings = manager.load()
    settings.set('ui', 'scale', '125')
    manager.save(settings)
    assert DownloadModePromptPreferences().mark_skip_prompt('offline-example')
    capture = CaptureSettingsManager()
    data = capture.load()
    data['openwrt']['host'] = '192.0.2.15'
    data['openwrt']['access_point_column_widths'] = [211, 81]
    capture.save(data)
    config.ensure_work_data_dirs()
    from types import SimpleNamespace
    from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
    output = AutoCaptureService.get_output_dir(SimpleNamespace(owner=SimpleNamespace(settings={})))
    assert output == root / 'data/captures' and output.is_dir()
    logs = ErrorLogStore()
    assert logs.directory == root / 'state/error-logs'
else:
    assert manager.has_saved_ui_scale()
    assert DownloadModePromptPreferences().should_skip_prompt('offline-example')
    assert manager.load().get('ui', 'scale') == '125'
    data = CaptureSettingsManager().load()
    assert data['openwrt']['host'] == '192.0.2.15'
    assert data['openwrt']['access_point_column_widths'] == [211, 81]
    assert (root / 'data/capture-work').is_dir()
print(json.dumps({'ok': True}))
'''
        with tempfile.TemporaryDirectory() as folder:
            for action in ('write', 'read'):
                result = subprocess.run([sys.executable, '-c', script, folder, action],
                                        cwd=ROOT, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)['ok'])


if __name__ == '__main__':
    unittest.main()
