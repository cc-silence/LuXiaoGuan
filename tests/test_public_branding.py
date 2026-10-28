"""Public product names must remain consistent while historical ownership stays compatible."""
from configparser import ConfigParser
import ctypes
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget, QLabel
from tests.test_standalone import APP
from tests.test_app_updates import signed, PUBLIC
from modules.app_update_services.manifest import verify_manifest
from modules.release_identity import DISPLAY_NAME, PRODUCT_NAME, APPLICATION_EXECUTABLE
from modules.platform_services import local_gpo_firewall as gpo, windows_firewall as firewall

ROOT = Path(__file__).resolve().parents[1]


class FirewallBrandMigrationTests(unittest.TestCase):
    def setUp(self):
        self.program = Path('C:/Applications/LuXiaoGuan/LuXiaoGuan.exe')
        self.rule = dict(program=self.program, name=gpo.startup_rule_name(self.program))
        self.old_name = self.rule['name'].replace(PRODUCT_NAME, 'RouterManager', 1)
        self.old_marker = gpo.LEGACY_OWNER_DESCRIPTION_PREFIX + gpo.STARTUP_OWNER
        self.old_value = gpo._rule_value(self.program, self.old_name, gpo.STARTUP_OWNER).replace(
            gpo._owner_description(gpo.STARTUP_OWNER), self.old_marker)

    def test_new_public_labels_keep_stable_owner(self):
        self.assertEqual(gpo.STARTUP_OWNER, 'routermanager-startup')
        self.assertTrue(self.rule['name'].startswith(PRODUCT_NAME + ' Startup'))
        self.assertTrue(firewall.temporary_rule_name(self.program, 'session').startswith(PRODUCT_NAME))
        self.assertIn(PRODUCT_NAME, gpo._owner_description(gpo.STARTUP_OWNER))
        self.assertEqual(APPLICATION_EXECUTABLE, 'LuXiaoGuan.exe')

    def test_old_rule_is_replaced_once_and_unrelated_rules_survive(self):
        other_path = Path('C:/Elsewhere/LuXiaoGuan.exe')
        other = gpo._rule_value(other_path, 'other-launch', gpo.STARTUP_OWNER)
        foreign = gpo._rule_value(self.program, self.old_name, 'foreign-owner')
        deleted, added, removed = gpo._source_update([('old', self.old_value), ('other', other),
                                                     ('foreign', foreign)], [self.rule], gpo.STARTUP_OWNER, 'ensure')
        self.assertEqual(deleted, ['old'])
        self.assertEqual(len(added), 1)
        fields = gpo._fields(added[0][1])
        self.assertEqual(fields['Name'], self.rule['name'])
        self.assertEqual(fields['Desc'], gpo._owner_description(gpo.STARTUP_OWNER))
        self.assertEqual(removed, [dict(name=self.old_name, program=self.program)])
        deleted, added_again, _ = gpo._source_update(added, [self.rule], gpo.STARTUP_OWNER, 'ensure')
        self.assertEqual((deleted, added_again), ([], []))

    def test_explicit_removal_recognizes_old_ownership(self):
        deleted, added, _ = gpo._source_update([('old', self.old_value)], [self.rule], gpo.STARTUP_OWNER, 'remove')
        self.assertEqual(deleted, ['old'])
        self.assertEqual(added, [])

    def test_old_effective_rule_cannot_be_mistaken_for_successful_removal(self):
        rule = dict(name=self.old_name, program=self.program)
        rows = [dict(name=self.old_name, program=str(self.program), description=self.old_marker)]
        with patch.object(gpo, '_active_rules', return_value=rows):
            self.assertFalse(gpo._active_matches([rule], gpo.STARTUP_OWNER, absent=True))

    def test_legacy_source_and_receipts_cannot_hide_needed_migration(self):
        with patch.object(Path, 'read_bytes', return_value=self.old_value.encode('utf-16le')):
            self.assertEqual(gpo._source_matches(gpo.STARTUP_OWNER)[0]['fields']['Name'], self.old_name)
        fields = gpo._fields(self.old_value)
        with patch.object(gpo, '_source_matches', return_value=[dict(fields=fields)]), \
                patch.object(gpo, '_applied_fields', return_value=[fields]):
            self.assertEqual(gpo._verification_entries([self.rule], gpo.STARTUP_OWNER), [])

    def test_temporary_cleanup_recognizes_both_groups_and_exact_program_scope(self):
        with patch.object(gpo, '_powershell_json', return_value=[]) as execute:
            gpo._cleanup_legacy_local_rules([self.rule])
        script = execute.call_args.args[0]
        self.assertIn(firewall.RULE_GROUP, script)
        self.assertIn(firewall.LEGACY_RULE_GROUP, script)
        self.assertIn('$paths -icontains $rule.ApplicationName', script)
        with patch.object(firewall, 'is_windows', return_value=True), \
                patch.object(firewall, '_run_elevated_powershell', return_value=(True, 0)) as execute:
            firewall.remove_temporary_rules(['exact-session-rule'])
        script = execute.call_args.args[0]
        self.assertIn('exact-session-rule', script)
        self.assertIn(firewall.RULE_GROUP, script)
        self.assertIn(firewall.LEGACY_RULE_GROUP, script)


class UpdateBrandTests(unittest.TestCase):
    def test_update_dialog_uses_public_name_in_both_themes_and_renders(self):
        from modules.desktop_shell.app_update_presenter import AppUpdatePresenter
        from modules.dialog_theme import build_base_stylesheet
        from modules.config_manager import ConfigManager
        runtime = QObject()
        runtime.main_window = QWidget()
        runtime.main_window.show()
        runtime._exiting = False
        runtime.config = ConfigParser()
        with patch('modules.platform_services.app_update_access.read_code', return_value=''):
            presenter = AppUpdatePresenter('2.0.8_alpha', runtime)
        try:
            for theme in ('dark.qss', 'light.qss'):
                presenter._release = verify_manifest(signed(), PUBLIC)
                style = build_base_stylesheet(ConfigManager(), theme_name=theme)
                with patch('modules.common_widgets.common_dialogs.build_current_base_stylesheet', return_value=style):
                    presenter._present_if_visible(force=True)
                APP.processEvents()
                dialog = presenter.dialog
                texts = [label.text() for label in dialog.findChildren(QLabel)]
                joined = '\n'.join(texts)
                self.assertIn(DISPLAY_NAME, joined)
                self.assertNotIn('RouterManager', joined)
                self.assertNotIn('WinBox', joined)
                output = ROOT / 'build/review/public-branding'
                output.mkdir(parents=True, exist_ok=True)
                self.assertTrue(dialog.grab().save(str(output / (theme + '.png'))))
                dialog.reject()
                APP.processEvents()
        finally:
            presenter.stop()
            runtime.main_window.close()
            runtime.main_window.deleteLater()
            runtime.deleteLater()
            APP.processEvents()

    def test_python_native_window_title_and_hint_use_public_name(self):
        from modules.platform_services.native_update_window import NativeUpdateWindow
        from modules.platform_services.app_update_native import MESSAGES, smoke_test
        self.assertEqual(smoke_test(), 0)
        window = NativeUpdateWindow(visible=False)
        try:
            user = window.user
            user.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
            user.GetWindowTextW.restype = ctypes.c_int
            for handle in (window.hwnd, window.hint):
                buffer = ctypes.create_unicode_buffer(1024)
                user.GetWindowTextW(handle, buffer, 1024)
                self.assertIn(DISPLAY_NAME, buffer.value)
                self.assertNotIn('WinBox', buffer.value)
                self.assertNotIn('RouterManager', buffer.value)
            for key in ('waiting', 'restarting'):
                self.assertIn(DISPLAY_NAME, MESSAGES[key])
        finally:
            window.dispose()

    def test_old_and_new_manifest_errors_keep_the_same_diagnostic_code(self):
        from modules.app_update_services.failure_codes import failure_code
        for message in ('Unsupported RouterManager update manifest', 'Unsupported LuXiaoGuan update manifest'):
            self.assertEqual(failure_code(ValueError(message)), 'invalid_product_or_platform')

    def test_native_helper_has_public_product_metadata(self):
        import pefile
        from scripts.build_native_updater import inputs
        self.assertEqual(inputs()['display_name'], DISPLAY_NAME)
        with pefile.PE(str(ROOT / 'bin/update_launcher/RouterManagerUpdater.exe')) as binary:
            entries = [row.entries for group in binary.FileInfo for item in group
                       if hasattr(item, 'StringTable') for row in item.StringTable]
        self.assertTrue(any(row.get(b'ProductName') == PRODUCT_NAME.encode('utf-8') for row in entries))
        self.assertTrue(any(row.get(b'FileDescription') == (PRODUCT_NAME + ' Update').encode('utf-8') for row in entries))
