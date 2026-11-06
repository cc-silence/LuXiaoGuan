"""Narrow action cards and start/pause/resume/stop state transitions."""
from pathlib import Path
import unittest
from PySide6.QtWidgets import QFrame, QVBoxLayout, QGroupBox
from PySide6.QtTest import QSignalSpy
from tests.test_file_selection import APP
from modules.ui_scale_manager import UiScaleManager
from modules.common_widgets.action_scroll_area import ActionScrollArea
from modules.main_pages.packet_capture.openwrt.capture_tool_panel import CaptureToolPanel
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.main_pages.base_page_styles import light_base_page_stylesheet, dark_base_page_stylesheet


class ToolCardTests(unittest.TestCase):
    def test_cards_and_task_actions_at_both_themes_and_scales(self):
        output = Path('build/previews/capture-tool-cards'); output.mkdir(parents=True,exist_ok=True)
        for percent in (100,125):
            for light in (True,False):
                owner=QFrame();owner.scale_manager=UiScaleManager(percent);owner.light_theme=light
                owner.setObjectName('packetCapturePage')
                panel=CaptureToolPanel({},owner)
                scroll=ActionScrollArea(panel,owner)
                QVBoxLayout(owner).addWidget(scroll)
                base=light_base_page_stylesheet('',owner.scale_manager) if light else dark_base_page_stylesheet(owner.scale_manager)
                owner.setStyleSheet(base+CapturePageStyleManager(owner)._capture_stylesheet(light))
                from modules.common_widgets.icon_button import refresh_tool_button_icons
                refresh_tool_button_icons(panel,light)
                owner.resize(scroll.sizeHint().width()+24,owner.scale_manager.px(850));owner.show()
                enabled={key:True for key in ('scan','handshake','password','wps')}
                spy=QSignalSpy(panel.actionRequested)
                try:
                    groups=panel.findChildren(QGroupBox)
                    self.assertEqual(len(groups),5)
                    self.assertIs(panel.password_button.parentWidget(),panel.password_pause_button.parentWidget())
                    self.assertIsNot(panel.password_button.parentWidget(),panel.wps_button.parentWidget())
                    self.assertIs(panel.settings_button.parentWidget(),panel.open_dir_button.parentWidget())
                    for state,pause,expected in (('idle','','password'),('capturing','running','stop'),
                                                 ('capturing','paused','stop'),('stopping','cleaning','stop'),('idle','','password')):
                        busy=state!='idle'
                        panel.set_action_availability(True,busy,enabled,'password_verify' if busy else '',state,pause)
                        APP.processEvents()
                        self.assertEqual(panel.password_button.property('captureAction'),expected)
                        self.assertEqual(panel.password_pause_button.isVisible(),busy)
                        if busy:
                            self.assertEqual(panel.password_pause_button.y(),panel.password_button.y())
                            self.assertEqual(panel.password_pause_button.height(),panel.password_button.height())
                        if pause=='running':
                            panel.password_pause_button.click()
                            self.assertEqual(spy.at(spy.count()-1)[0],'pause_password')
                        elif pause=='paused':
                            panel.password_pause_button.click()
                            self.assertEqual(spy.at(spy.count()-1)[0],'resume_password')
                        if state!='stopping':
                            panel.password_button.click()
                            self.assertEqual(spy.at(spy.count()-1)[0],expected)
                        else:
                            self.assertFalse(panel.password_button.isEnabled())
                            self.assertFalse(panel.password_pause_button.isEnabled())
                        for button in (panel.password_button,panel.password_pause_button):
                            if button.isVisible():
                                self.assertGreaterEqual(button.width(),button.fontMetrics().horizontalAdvance(button.text()))
                        filename=f"{'light' if light else 'dark'}-{percent}-{pause or state}.png"
                        self.assertTrue(owner.grab().save(str(output/filename)))
                    self.assertLess(panel.password_button.parentWidget().y(),panel.wps_button.parentWidget().y())
                finally:
                    owner.close();owner.deleteLater();APP.processEvents()


if __name__=='__main__':
    unittest.main()
