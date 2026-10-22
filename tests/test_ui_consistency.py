"""Behavior regressions for shared logs, tooltips and list styling."""
import html
import re
import unittest
from unittest.mock import patch, Mock
from pathlib import Path
from tests import test_standalone as standalone
APP = standalone.APP
from PySide6.QtCore import Qt, QEvent, QPoint
from PySide6.QtGui import QHelpEvent, QFont
from PySide6.QtWidgets import QToolTip, QWidget, QTableWidget, QTableWidgetItem
from modules.config_manager import ConfigManager
from modules.common_widgets.tooltip_policy import format_tooltip_text, install_tooltip_policy
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.ui_scale_manager import UiScaleManager


class SharedUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window = standalone.StandaloneTests.window

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def test_log_zoom_and_document_font_stay_equal_across_pages(self):
        page = self.window.packet_capture_page
        for panel in page.info_panels:
            panel.set_font_delta(0)
        with patch.object(ConfigManager, "save") as save:
            page.signal_info_panel.zoom_in_btn.click()
            save.assert_called_once()
        for theme in ("dark.qss", "light.qss"):
            self.window.apply_appearance(100, "default", theme)
            for panel in page.info_panels:
                self.assertEqual(panel._info_font_delta, 1)
                self.assertEqual(panel.info_box.font().pointSize(), 12)
                self.assertEqual(panel.info_box.font(), panel.info_box.document().defaultFont())
                self.assertEqual(panel.info_box.font().family(), UiScaleManager()._resolved_font())

    def test_rounds_combo_matches_other_capture_inputs(self):
        tabs = self.window.packet_capture_page.openwrt_tab
        tabs.tabs.setCurrentIndex(3)
        box = tabs.settings_page.handshake_rounds_mode
        self.assertEqual(box.objectName(), "captureComboBox")
        for theme, expected in (("dark.qss", "#1d2126"), ("light.qss", "#e9eff6")):
            self.window.apply_appearance(100, "default", theme)
            APP.processEvents()
            image = box.grab().toImage()
            self.assertEqual(image.pixelColor(box.width() // 2, box.height() // 2).name(), expected)

    def test_file_header_matches_capture_header_pixels(self):
        page = self.window.packet_capture_page
        capture = page.openwrt_tab.capture_page.access_points
        files = page.openwrt_tab.files_page
        for theme in ("dark.qss", "light.qss"):
            self.window.apply_appearance(100, "default", theme)
            page.openwrt_tab.tabs.setCurrentIndex(2)
            APP.processEvents()
            expected = capture.horizontalHeader().grab().toImage().pixelColor(3, 15)
            page.openwrt_tab.tabs.setCurrentIndex(4)
            APP.processEvents()
            for stack, tree, icons in files._views.values():
                self.assertEqual(tree.header().grab().toImage().pixelColor(3, 15), expected)


class TooltipTests(unittest.TestCase):
    def test_wrap_preserves_chinese_emoji_paths_and_markup(self):
        for text in ("\u8def\u7531\u5668" * 60, "C:/" + "long_path_" * 80,
                     "\U0001f680" * 80, "<b>& data\nnext line"):
            rendered = format_tooltip_text(text, QFont("Microsoft YaHei", 12), 240)
            body = re.search(r"<div[^>]*>(.*?)</div>", rendered).group(1)
            lines = body.split("<br>")
            self.assertGreater(len(lines), 1)
            self.assertEqual(html.unescape("".join(lines)), text.replace("\n", ""))
            self.assertNotIn("<b>", body)

    def test_global_policy_wraps_widget_and_table_item_tooltips(self):
        policy = install_tooltip_policy(APP)
        self.assertIs(policy, install_tooltip_policy(APP))
        widget = QWidget()
        widget.setToolTip("word " * 100)
        widget.show()
        APP.processEvents()
        event = QHelpEvent(QEvent.ToolTip, QPoint(2, 2), widget.mapToGlobal(QPoint(2, 2)))
        with patch.object(QToolTip, "showText") as show:
            APP.sendEvent(widget, event)
            self.assertIn("<br>", show.call_args.args[1])
        table = QTableWidget(1, 1)
        item = QTableWidgetItem("sample")
        item.setToolTip("item " * 100)
        table.setItem(0, 0, item)
        table.show()
        APP.processEvents()
        point = table.visualItemRect(item).center()
        event = QHelpEvent(QEvent.ToolTip, point, table.viewport().mapToGlobal(point))
        with patch.object(QToolTip, "showText") as show:
            APP.sendEvent(table.viewport(), event)
            self.assertIn("item", show.call_args.args[1])
            self.assertIn("<br>", show.call_args.args[1])
        table.close(); widget.close()

    def test_title_drag_delegates_to_system_movement(self):
        dialog = FramelessDialog("test")
        event = Mock()
        event.button.return_value = Qt.LeftButton
        handle = Mock()
        handle.startSystemMove.return_value = True
        with patch.object(dialog, "windowHandle", return_value=handle):
            dialog._title_mouse_press(event)
        handle.startSystemMove.assert_called_once_with()
        event.accept.assert_called_once_with()
        self.assertIsNone(dialog.drag_pos)
        dialog.close()
