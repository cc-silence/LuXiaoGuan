"""Explicit upgrade and progress lifecycle regressions."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget
from tests.test_feedback_regressions import APP
from modules.capture_services.openwrt.hcx_upgrade import upgrade_hcx
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState
from modules.capture_services.dependency_service import DependencyTask
from modules.main_pages.packet_capture.openwrt.capture_upgrade_progress import CaptureUpgradeProgress


def row(state):
    return DependencyInfo('pmkid', 'PMKID', state, detail='version check')


class HcxUpgradeTests(unittest.TestCase):
    def test_upgradable_is_installed_and_rechecked(self):
        manager = Mock()
        manager.check.side_effect = [(row(DependencyState.UPGRADABLE),), (row(DependencyState.READY),)]
        self.assertEqual(upgrade_hcx(manager, NS(sftp_available=True), Path('.'), None, Mock())[0].state,
                         DependencyState.READY)
        manager._try_install_modern_hcxdumptool.assert_called_once()
        manager.install_all.assert_not_called()

    def test_old_version_after_install_is_failure(self):
        manager = Mock()
        manager.check.return_value = (row(DependencyState.UPGRADABLE),)
        with self.assertRaisesRegex(RuntimeError, 'version check'):
            upgrade_hcx(manager, NS(sftp_available=True), Path('.'), None, Mock())

    def test_unsupported_unknown_or_missing_is_not_upgraded(self):
        for state in (DependencyState.OUTDATED, DependencyState.UNKNOWN, DependencyState.MISSING):
            manager = Mock()
            manager.check.return_value = (row(state),)
            with self.assertRaises(RuntimeError):
                upgrade_hcx(manager, NS(sftp_available=True), Path('.'), None, Mock())
            manager._try_install_modern_hcxdumptool.assert_not_called()

    def test_unavailable_package_cannot_report_success(self):
        manager = Mock()
        manager.check.return_value = (row(DependencyState.UPGRADABLE),)
        manager._try_install_modern_hcxdumptool.return_value = False
        with self.assertRaises(RuntimeError):
            upgrade_hcx(manager, NS(sftp_available=True), Path('.'), None, Mock())


class Service(QObject):
    taskChanged = Signal(object)
    taskFinished = Signal(str, bool, str)

    def __init__(self):
        super().__init__()
        self.calls = 0
        self.cancel = Mock()

    def upgrade_hcx_async(self):
        self.calls += 1
        self.taskChanged.emit(DependencyTask('ours', 'capture', ('pmkid',), 'install', 'Checking prerequisites'))
        return True


class UpgradeProgressTests(unittest.TestCase):
    def test_first_click_shows_progress_repeat_reuses_and_cancel_is_owned(self):
        page = QWidget()
        service = Service()
        progress = CaptureUpgradeProgress(page, service)
        self.assertTrue(progress.start())
        dialog = progress.dialog
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.message.text(), 'Checking prerequisites')
        self.assertTrue(progress.start())
        self.assertEqual(service.calls, 1)
        dialog.request_cancel()
        service.cancel.assert_called_once_with('ours')
        service.taskFinished.emit('other', True, '')
        self.assertIs(progress.dialog, dialog)
        service.taskFinished.emit('ours', False, 'Cancelled')
        self.assertIsNone(progress.dialog)
        self.assertFalse(dialog.running)
        progress.dispose()
        page.deleteLater()

    def test_failure_stays_visible_and_dispose_only_unsubscribes(self):
        page = QWidget()
        service = Service()
        progress = CaptureUpgradeProgress(page, service)
        progress.start()
        dialog = progress.dialog
        service.taskFinished.emit('ours', False, 'Channel closed')
        self.assertFalse(dialog.running)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.message.text(), 'Channel closed')
        progress.dispose()
        service.cancel.assert_not_called()
        page.deleteLater()
