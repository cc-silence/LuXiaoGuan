"""Measure actual glyph centers in shared dialog title controls."""
from pathlib import Path
import unittest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtWidgets import QLabel
from tests.test_standalone import APP
from modules import resources_rc
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.config_manager import ConfigManager
from modules.dialog_theme import build_base_stylesheet
from modules.ui_scale_manager import UiScaleManager


class TitleAlignmentTests(unittest.TestCase):
    def test_normal_pressed_maximized_glyphs_are_centered_in_all_scales(self):
        output = Path("build/scan-feedback-154731/title-alignment")
        output.mkdir(parents=True, exist_ok=True)
        old_font = APP.font()
        self.addCleanup(lambda: APP.setFont(old_font))
        for light in (False, True):
            for percent in (100, 125, 150, 175):
                with self.subTest(light=light, scale=percent):
                    window = FramelessDialog("Title alignment")
                    scale = window.scale_manager = UiScaleManager(percent)
                    window.setProperty("settingsLightTheme", light)
                    scale.apply_application_font(APP)
                    scale.register_layout_tree(window)
                    scale.apply_bound_metrics(window)
                    window.setStyleSheet(build_base_stylesheet(ConfigManager(output/"unused.ini"), light_theme=light, scale_manager=scale))
                    window.content_layout.addWidget(QLabel("Dialog content", window))
                    window.resize(scale.px(600), scale.px(300))
                    window.show()
                    APP.processEvents()
                    try:
                        controls = window.window_controls
                        buttons = (controls.minimize_button, controls.maximize_button, window.close_btn)
                        for maximized in (False, True):
                            if maximized:
                                controls.toggle_maximized()
                                APP.processEvents()
                            for button in buttons:
                                button.setAttribute(Qt.WA_UnderMouse, False)
                                APP.sendEvent(button, QEvent(QEvent.Leave))
                                for pressed in (False, True):
                                    button.setDown(pressed)
                                    image = button.grab().toImage()
                                    points = []
                                    for x in range(image.width()):
                                        for y in range(image.height()):
                                            color = image.pixelColor(x, y)
                                            glyph = max(color.red(),color.green(),color.blue()) < 190 if light else min(color.red(),color.green(),color.blue()) > 100
                                            if color.alpha() > 10 and glyph:
                                                points.append((x, y))
                                    self.assertTrue(points, (light, percent, maximized, pressed, button.toolTip()))
                                    center_x = (min(x for x,y in points) + max(x for x,y in points))/2
                                    center_y = (min(y for x,y in points) + max(y for x,y in points))/2
                                    self.assertLessEqual(abs(center_x-(image.width()-1)/2), 1.0)
                                    self.assertLessEqual(abs(center_y-(image.height()-1)/2), 1.0)
                                button.setDown(False)
                            if not maximized:
                                window.grab(QRect(0,0,window.width(),scale.px(72))).save(str(output/(("light" if light else "dark")+f"-{percent}.png")))
                    finally:
                        window.close()
                        window.deleteLater()
                        APP.processEvents()
