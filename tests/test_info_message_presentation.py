"""Raw diagnostic export must survive concise display and repaint."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from PySide6.QtWidgets import QApplication
from modules.main_pages.info_panel import InfoPanel
from modules.info_message_text import user_info_text


class InfoPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_diagnostics_and_original_text_survive_refresh_and_export(self):
        panel = InfoPanel()
        diagnostic = "\u672c\u5730\u7ed3\u679c\u6838\u9a8c\uff1a0 missing; 1 unknown"
        raw = "\u5c04\u9891 radio0 \u6293\u5305\u5df2\u542f\u52a8 (hcxmon0)"
        failure = "\u65e0\u7ebf\u6062\u590d\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u3002"
        panel.append_info(diagnostic, "diagnostic")
        panel.append_info(raw, "success")
        panel.append_info(failure, "error")
        expected = "\u65e0\u7ebf\u7f51\u5361\u5df2\u5f00\u59cb\u6293\u5305\u3002\n" + failure
        self.assertEqual(panel.info_box.toPlainText(), expected)
        self.assertEqual(panel.export_entries(), ((diagnostic,"diagnostic"),(raw,"success"),(failure,"error")))
        panel.set_light_theme(True)
        panel._refresh_privacy()
        self.assertEqual(panel.info_box.toPlainText(), expected)
        self.assertEqual(panel.export_entries()[1][0], raw)
        panel.deleteLater()

    def test_unknown_errors_and_signal_names_are_not_filtered(self):
        for raw in ("Permission denied: wlan0", "my EAPOL radio0 network", "\u6293\u5305\u5931\u8d25: failed to arm interface"):
            self.assertEqual(user_info_text(raw), raw)

    def test_internal_diagnostic_path_stays_in_export_only(self):
        panel=InfoPanel()
        raw="\u626b\u63cf\u672a\u5b8c\u6210\uff1b\u73b0\u573a\u8bca\u65ad\u5df2\u4fdd\u5b58\uff1aC:/capture-work/"+"a"*32+"/wireless-diagnostic.json"
        panel.append_info(raw,"error")
        self.assertEqual(panel.info_box.toPlainText(),"\u626b\u63cf\u672a\u5b8c\u6210\uff1b\u8bca\u65ad\u8bb0\u5f55\u5df2\u4fdd\u5b58\uff0c\u53ef\u5bfc\u51fa\u65e5\u5fd7\u3002")
        self.assertEqual(panel.export_entries(),((raw,"error"),))
        extra=raw+"\uff1b\u8bf7\u91cd\u65b0\u767b\u5f55"
        self.assertEqual(user_info_text(extra),extra)
        panel.deleteLater()

    def test_result_retains_completion_details(self):
        raw = "\u5b9a\u5411\u91c7\u96c6\u7ed3\u675f\uff1a\u672c\u6b21\u5305\u4e2d\u672a\u53d1\u73b0 EAPOL\uff0c\u65e0\u6709\u6548\u7ed3\u679c\uff0c\u672a\u4fdd\u7559\u6587\u4ef6\u3002\u5df2\u5b8c\u6210 2 \u8f6e\u3002"
        self.assertEqual(user_info_text(raw), "\u672c\u6b21\u672a\u6293\u5230\u53ef\u7528\u6570\u636e\uff0c\u672a\u4fdd\u7559\u6587\u4ef6\u3002\u5df2\u5b8c\u6210 2 \u8f6e\u3002")


if __name__ == '__main__':
    unittest.main()
