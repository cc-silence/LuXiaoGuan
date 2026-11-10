"""Bounded log rendering must remain incremental and match a fresh projection."""
import unittest
from unittest.mock import patch
from PySide6.QtGui import QTextCursor
from tests.test_standalone import APP
from modules.display_privacy import masker_for
from modules.main_pages.info_panel import InfoPanel


class IncrementalInfoTests(unittest.TestCase):
    def setUp(self):
        self.panel = InfoPanel()
        self.addCleanup(self.panel.deleteLater)

    def assert_projection_matches_refresh(self):
        box = self.panel.info_box
        actual = box.toPlainText()
        colors = []
        block = box.document().begin()
        while block.isValid():
            colors.append([(f.fragment().text(), f.fragment().charFormat().foreground().color().name())
                           for f in self.fragments(block)])
            block = block.next()
        self.panel._refresh_privacy()
        self.assertEqual(box.toPlainText(), actual)
        rebuilt = []
        block = box.document().begin()
        while block.isValid():
            rebuilt.append([(f.fragment().text(), f.fragment().charFormat().foreground().color().name())
                            for f in self.fragments(block)])
            block = block.next()
        self.assertEqual(rebuilt, colors)
        self.assertEqual(len(self.panel._rendered_sizes), len(self.panel.export_entries()))

    @staticmethod
    def fragments(block):
        iterator = block.begin()
        while not iterator.atEnd():
            yield iterator
            iterator += 1

    def test_full_log_drops_only_expired_entries_without_replay(self):
        self.panel.append_info("\n".join(f"line {i}" for i in range(2000)))
        with patch.object(self.panel, "_refresh_privacy", wraps=self.panel._refresh_privacy) as replay, \
             patch.object(self.panel, "_append_rendered", wraps=self.panel._append_rendered) as render:
            for i in range(30):
                self.panel.append_info(f"new {i}", "error" if i % 2 else "warning")
            replay.assert_not_called()
            self.assertEqual(render.call_count, 30)
        self.assertTrue(self.panel.info_box.toPlainText().startswith("line 30\n"))
        self.assert_projection_matches_refresh()

    def test_hidden_diagnostics_empty_lines_unicode_and_large_batches(self):
        for i in range(2010):
            self.panel.append_info("name \U0001f600\u2029detail" if i % 4 == 0 else "",
                                   "diagnostic" if i % 3 == 0 else "warning")
        self.assertLessEqual(self.panel.info_box.blockCount(), 2000)
        self.assert_projection_matches_refresh()
        self.panel.append_info("\n".join(f"replacement {i}" for i in range(2100)))
        self.assert_projection_matches_refresh()
        self.assertTrue(self.panel.info_box.toPlainText().startswith("replacement 100\n"))
        self.panel.clear()
        self.panel.append_info("only diagnostic", "diagnostic")
        self.panel.append_info("first visible")
        self.assertEqual(self.panel.info_box.toPlainText(), "first visible")

    def test_byte_limit_privacy_and_theme_preserve_raw_entries(self):
        self.panel.set_local_masker(masker_for(["private"]))
        with patch("modules.main_pages.info_panel.MAX_INFO_BYTES", 120):
            for i in range(30):
                self.panel.append_info(f"private \U0001f600 data {i}", "error")
            self.panel.append_info("diagnostic data", "diagnostic")
        self.assertNotIn("private", self.panel.info_box.toPlainText())
        self.assert_projection_matches_refresh()
        raw = self.panel.export_entries()
        self.panel.set_light_theme(True)
        self.assertEqual(self.panel.export_entries(), raw)
        self.assert_projection_matches_refresh()


if __name__ == "__main__":
    unittest.main()
