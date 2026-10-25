"""Shared identity confirmation and restore hints use existing Qt components."""
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from PySide6.QtWidgets import QWidget,QScrollArea
from tests.test_standalone import APP
from tests.test_wireless_ui import environment
from tests.test_shared_station_identity import device_with_group, apply_batch
from modules.config_manager import ConfigManager
from modules.dialog_theme import build_base_stylesheet
from modules.ui_scale_manager import UiScaleManager
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import RelaySetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_relay
from modules.main_pages.packet_capture.openwrt.router_policy_dialogs import PolicyReviewDialog,review_text


class SharedStationUiTests(unittest.TestCase):
    def test_shared_preview_and_editor_render_with_recovery_hints(self):
        output=Path("build/shared-station-identity-20261025/previews")
        output.mkdir(parents=True,exist_ok=True)
        d=device_with_group();plan=apply_batch(d)
        text=review_text(plan)
        self.assertIn("wwan",text)
        for entry in plan["identities"]:
            self.assertIn(entry["section"],text)
            self.assertIn(entry["mac"],text)
        for light in (False,True):
            for percent in (100,150):
                with self.subTest(light=light,scale=percent):
                    parent=QWidget();parent.scale_manager=UiScaleManager(percent)
                    self.addCleanup(parent.deleteLater)
                    theme="light.qss" if light else "dark.qss"
                    style=build_base_stylesheet(ConfigManager(),light,theme,parent.scale_manager)
                    parent.setStyleSheet(style)
                    with patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet",return_value=style):
                        preview=PolicyReviewDialog(plan,parent)
                        editor=RelaySetupDialog("Upstream",parent,radio_name="radio7",row=NS(section="station",running="active"))
                    self.addCleanup(preview.deleteLater);self.addCleanup(editor.deleteLater)
                    env=environment();row=dict(d.config["wireless"]["station"],section="station",has_key=True)
                    env.data["rows"].append(row)
                    env.data["networks"].append(dict(d.config["network"]["wwan"],name="wwan"))
                    configure_relay(editor,env)
                    fields=editor.identity_fields
                    fields.mode.setCurrentIndex(fields.mode.findData("restore"))
                    self.assertEqual(fields.values(),{"identity_action":"restore"})
                    self.assertTrue(fields._shared_identity)
                    self.assertIn("MAC",fields.note.text())
                    editor.other_section.set_expanded(True,animate=False)
                    editor.other_tabs.buttons[3].click()
                    editor.show();APP.processEvents()
                    for area in editor.findChildren(QScrollArea):
                        if area.widget() and area.widget().isAncestorOf(fields.note):
                            area.ensureWidgetVisible(fields.note,5,10)
                    APP.processEvents()
                    editor.grab().save(str(output/("shared-editor-"+str(light)+"-"+str(percent)+".png")))
                    editor.reject()
                    preview.show();APP.processEvents()
                    preview.grab().save(str(output/("shared-preview-"+str(light)+"-"+str(percent)+".png")))
                    preview.reject()
