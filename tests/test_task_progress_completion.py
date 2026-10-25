"""Task completion freezes native busy animation and rejects late signals."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
import time
from pathlib import Path
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QFontDatabase, QFont
from modules.dialog_theme import build_base_stylesheet
from modules.config_manager import ConfigManager
from unittest.mock import patch
from modules import resources_rc
from modules.common_widgets.task_progress_dialog import TaskProgressDialog
from modules.main_pages.packet_capture.openwrt.router_task_progress import RouterTaskProgressDialog
from modules.config_manager import BASE_DIR

APP=QApplication.instance() or QApplication([])
for name in ("msyh.ttc", "msyhbd.ttc"):
    QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts")/name))
APP.setFont(QFont("Microsoft YaHei",10))


class CompletionTests(unittest.TestCase):
    def test_failed_indeterminate_progress_stops_and_pixels_remain_still(self):
        parent=QWidget()
        for light in (True,False):
            parent.light_theme=light
            theme="light.qss" if light else "dark.qss"
            style=build_base_stylesheet(ConfigManager(),light,theme,None)
            with patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet",return_value=style):
                dialog=TaskProgressDialog("\u5b89\u88c5\u6587\u4ef6\u4f20\u8f93\u652f\u6301",parent=parent)
            cancel=Mock()
            dialog.cancelRequested.connect(cancel)
            dialog.update_stage("Installing")
            self.assertEqual(dialog.bar.maximum(),0)
            dialog.finish(False,"\u5b89\u88c5\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u8be6\u7ec6\u4fe1\u606f\u540e\u91cd\u8bd5\u3002")
            APP.processEvents()
            self.assertEqual(dialog.bar.maximum(),1000)
            self.assertEqual(dialog.bar.value(),0)
            first=dialog.bar.grab().toImage()
            time.sleep(.18)
            APP.processEvents()
            self.assertEqual(first,dialog.bar.grab().toImage())
            folder=BASE_DIR/"build/ui-dialog-fixes-20261025"
            folder.mkdir(parents=True,exist_ok=True)
            dialog.grab().save(str(folder/("failed-light.png" if light else "failed-dark.png")))
            dialog.update_stage("late stage")
            dialog.update_progress({"completed":50,"total":100})
            dialog.finish(True)
            self.assertEqual(dialog.message.text(),"\u5b89\u88c5\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u8be6\u7ec6\u4fe1\u606f\u540e\u91cd\u8bd5\u3002")
            self.assertEqual(dialog.bar.value(),0)
            dialog.cancel_button.click()
            cancel.assert_not_called()
        parent.deleteLater()

    def test_known_progress_is_preserved_and_adapter_ignores_late_phase(self):
        dialog=RouterTaskProgressDialog("Install",Mock())
        dialog.update_progress({"completed":25,"total":100,"stage":"Install"})
        previous=dialog.bar.value()
        dialog.finish(False,"failure")
        dialog.update_progress({"stage":"late", "uncancellable":True})
        self.assertEqual(dialog.bar.value(),previous)
        self.assertTrue(dialog.cancel_button.isEnabled())
        self.assertEqual(dialog.phase,"Install")
        self.assertEqual(dialog.message.text(),"failure")
        dialog.reject()


if __name__ == "__main__":
    unittest.main()
