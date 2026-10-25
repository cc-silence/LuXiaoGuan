"""Settings keep shortcut actions immediate, lazy, themed and runtime-owned."""
from pathlib import Path
from unittest.mock import patch
import unittest
from PySide6.QtWidgets import QScrollArea
from tests import test_standalone as standalone
from tests.test_desktop_shortcut import wait_until
APP=standalone.APP


class ShortcutSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window=standalone.StandaloneTests.window
        cls.runtime=standalone.StandaloneTests.runtime

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def test_open_is_lazy_and_action_does_not_wait_for_save(self):
        presenter=self.runtime.desktop_shortcuts
        with patch.object(presenter.controller,"inspect") as inspect,patch.object(presenter,"add_manually",return_value=True) as add:
            self.window.openSettingsWindow()
            settings=self.window.settings_window
            inspect.assert_not_called()
            self.assertIs(self.window.navigation.stack.currentWidget(), settings)
            self.assertFalse(settings.isWindow())
            settings.desktop_shortcut_section.button.click()
            add.assert_called_once()
            settings.reject()
            self.assertIsNone(self.window.settings_window)

    def test_startup_waits_for_firewall_and_shortcut_completion(self):
        presenter=self.runtime.desktop_shortcuts
        with patch.object(presenter,"start_first_launch") as start,patch.object(self.runtime.app_updates,"window_ready") as update:
            self.runtime._firewall_finished(False,"fixture")
            start.assert_not_called();update.assert_not_called()
            self.runtime._firewall_finished(True,"")
            start.assert_called_once();update.assert_not_called()
            presenter.startupFinished.emit();APP.processEvents()
            update.assert_called_once()

    def test_themes_and_scales_render(self):
        for theme in ("dark.qss","light.qss"):
            self.window.apply_appearance(100,"default",theme)
            self.window.openSettingsWindow()
            settings=self.window.settings_window
            for scale in (100,125,150,175):
                settings.ui_scale_combo.setCurrentIndex(settings.ui_scale_combo.findData(scale))
                APP.processEvents()
                section=settings.desktop_shortcut_section
                page=settings.pages.widget(0)
                for scroll in [page,*page.findChildren(QScrollArea)]:
                    scroll.ensureWidgetVisible(section.button)
                APP.processEvents()
                button=section.button
                self.assertTrue(button.isVisible())
                self.assertTrue(page.viewport().rect().intersects(button.rect().translated(button.mapTo(page.viewport(),button.rect().topLeft()))))
                self.assertGreaterEqual(button.width(),button.fontMetrics().horizontalAdvance(button.text()))
                preview=Path("build")/("shortcut-settings-"+theme+"-"+str(scale)+".png")
                self.assertTrue(self.window.grab().save(str(preview)))
            settings.reject()
        self.window.apply_appearance(100,"default","dark.qss")

    def test_zz_runtime_exit_cancels_and_waits_for_shortcut(self):
        presenter=self.runtime.desktop_shortcuts
        with patch("modules.desktop_shell.desktop_shortcut_controller.shell_arguments",
                   return_value=["-NoProfile","-NonInteractive","-WindowStyle","Hidden","-Command","Start-Sleep -Seconds 60"]):
            presenter.controller.inspect()
        self.assertTrue(presenter.active)
        self.assertTrue(any("shortcut" in value for value in self.runtime.idle_blockers()))
        self.runtime.exit_application()
        wait_until(lambda:self.runtime._ready_to_close,12)
        self.assertFalse(presenter.active)
