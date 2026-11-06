"""Enter respects the selected backup/direct/cancel action in shared dialogs."""
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit
from tests.test_standalone import APP
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons
from modules.main_pages.packet_capture.openwrt.maintenance_review_dialog import MaintenanceReviewDialog


class DialogButtonKeyboardTests(unittest.TestCase):
    def show(self, dialog, field):
        dialog.show()
        dialog.activateWindow()
        field.setFocus()
        APP.processEvents()
        self.assertTrue(field.hasFocus())

    def test_maintenance_buttons_keep_their_own_choice_for_both_enter_keys(self):
        for kind in ("reset", "restore"):
            for key in (Qt.Key_Return, Qt.Key_Enter):
                for choice in ("direct", "backup", "cancel"):
                    with self.subTest(kind=kind, key=key, choice=choice):
                        review = dict(operation=kind, model="Router", version="23.05.4", entries=1, size=100, files=[])
                        dialog = MaintenanceReviewDialog(review, None)
                        self.addCleanup(dialog.deleteLater)
                        if kind == "reset":
                            dialog.confirm_reset.click()
                        cancel = dialog.button_layout.itemAt(dialog.button_layout.count()-1).widget()
                        button = {"direct":dialog.direct_button, "backup":dialog.go, "cancel":cancel}[choice]
                        self.show(dialog, button)
                        QTest.keyClick(button, key)
                        APP.processEvents()
                        self.assertEqual(dialog.result(), dialog.Rejected if choice=="cancel" else dialog.Accepted)
                        self.assertEqual(dialog.backup_requested, choice=="backup")
                        self.assertFalse(dialog.isVisible())

    def test_single_line_field_still_uses_primary_and_multiline_keeps_newline(self):
        for multiline in (False, True):
            dialog = FramelessDialog("Test")
            self.addCleanup(dialog.deleteLater)
            field = QPlainTextEdit(dialog) if multiline else QLineEdit(dialog)
            dialog.content_layout.addWidget(field)
            ok, cancel = create_dialog_ok_cancel_buttons(dialog)
            ok.clicked.connect(dialog.accept)
            cancel.clicked.connect(dialog.reject)
            dialog.button_layout.addWidget(ok)
            dialog.button_layout.addWidget(cancel)
            self.show(dialog, field)
            QTest.keyClick(field, Qt.Key_Return)
            if multiline:
                self.assertTrue(dialog.isVisible())
                self.assertEqual(field.toPlainText(), "\n")
                dialog.reject()
            else:
                self.assertEqual(dialog.result(), dialog.Accepted)
