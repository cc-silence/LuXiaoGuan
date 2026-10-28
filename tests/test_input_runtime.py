"""Runtime pin and shared input behavior; synthetic events are not a Win11 IME acceptance test."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QCoreApplication,QEvent
from PySide6.QtGui import QInputMethodEvent,QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.main_pages.packet_capture.openwrt.signal_setup_forms import PasswordInput,text_input
from scripts.qt_runtime_contract import require_qt_runtime


class InputRuntimeTests(unittest.TestCase):
    def test_dependency_runtime_pin_is_enforced_for_packaging(self):
        self.assertEqual(require_qt_runtime(),dict(pyside='6.3.2',qt='6.3.2'))
        with patch('PySide6.__version__','6.3.0'):
            with self.assertRaises(RuntimeError):require_qt_runtime()
        with patch('PySide6.QtCore.qVersion',return_value='6.3.0'):
            with self.assertRaises(RuntimeError):require_qt_runtime()

    def test_shared_password_and_name_fields_keep_uppercase_and_composition(self):
        parent=QWidget();self.addCleanup(parent.deleteLater)
        password=PasswordInput(parent);name=text_input(parent)
        parent.show()
        for edit in (password.edit,name):
            edit.show();edit.setFocus();APP.processEvents()
            QCoreApplication.sendEvent(edit,QKeyEvent(QEvent.KeyPress,Qt.Key_A,Qt.ShiftModifier,"A"))
            QCoreApplication.sendEvent(edit,QKeyEvent(QEvent.KeyRelease,Qt.Key_A,Qt.ShiftModifier,"A"))
            self.assertEqual(edit.text(),'A')
            QTest.keyClick(edit,Qt.Key_CapsLock)
            QTest.keyClicks(edit,'B')
            self.assertEqual(edit.text(),'AB')
            event=QInputMethodEvent();event.setCommitString('\u8def\u7531')
            QCoreApplication.sendEvent(edit,event)
            self.assertEqual(edit.text(),'AB\u8def\u7531')
            QTest.keyClick(edit,Qt.Key_Backspace)
            self.assertEqual(edit.text(),'AB\u8def')
            event=QInputMethodEvent();event.setCommitString('C')
            QCoreApplication.sendEvent(edit,event)
            self.assertEqual(edit.text(),'AB\u8defC')
        password.toggle.click()
        self.assertEqual(password.edit.text(),'AB\u8defC')
