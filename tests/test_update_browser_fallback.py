"""Trusted full-package links survive incremental failures and preserve selection."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from configparser import ConfigParser
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from tests.test_app_updates import signed,PUBLIC
from modules.app_update_services.manifest import verify_manifest
from modules.desktop_shell.app_update_presenter import AppUpdatePresenter
from modules.settings_pages.app_update_page import AppUpdatePage

APP=QApplication.instance() or QApplication([])

class BrowserFallbackTests(unittest.TestCase):
    def setUp(self):
        self.runtime=QObject();self.runtime._exiting=False;self.runtime.main_window=None;self.runtime.config=ConfigParser()
        with patch('modules.platform_services.app_update_access.read_code',return_value=''):
            self.presenter=AppUpdatePresenter('2.0.8_alpha',self.runtime)
        self.page=AppUpdatePage(self.presenter)

    def tearDown(self):
        self.presenter.stop();self.page.close();self.page.deleteLater();self.runtime.deleteLater();APP.processEvents()

    def test_link_is_hidden_without_a_verified_release(self):
        self.assertTrue(self.page.package_link_area.isHidden())
        self.assertEqual(self.page.package_link.text(),'')

    def test_failed_download_keeps_verified_link_and_copy_uses_signed_url(self):
        release=verify_manifest(signed(),PUBLIC)
        self.presenter._release=release;self.presenter._transfer_failed('offline fixture')
        self.assertFalse(self.page.package_link_area.isHidden())
        self.assertTrue(self.page.package_link.isReadOnly())
        self.assertFalse(hasattr(self.page,"browser_download_button"))
        self.assertEqual(self.page.package_link.text(),release.url)
        self.page.copy_package_link_button.click()
        self.assertEqual(APP.clipboard().text(),release.url)
        with patch('PySide6.QtGui.QDesktopServices.openUrl',return_value=True) as opened:
            self.presenter.download_release()
        self.assertEqual(opened.call_args.args[0].toString(),release.url)

    def test_progress_refresh_does_not_destroy_manual_link_selection(self):
        self.presenter._release=verify_manifest(signed(),PUBLIC);self.page.refresh()
        self.page.package_link.selectAll();selected=self.page.package_link.selectedText()
        self.page.refresh()
        self.assertEqual(self.page.package_link.selectedText(),selected)
        self.presenter._release=None;self.page.refresh()
        self.assertTrue(self.page.package_link_area.isHidden());self.assertEqual(self.page.package_link.text(),'')

    def test_failed_browser_launch_retains_link_and_reports_error(self):
        self.presenter._release=verify_manifest(signed(),PUBLIC);self.page.refresh()
        with patch('PySide6.QtGui.QDesktopServices.openUrl',return_value=False):
            self.presenter.download_release()
        self.assertEqual(self.presenter.state,'error')
        self.assertEqual(self.page.package_link.text(),self.presenter.release.url)

if __name__=='__main__':unittest.main()
