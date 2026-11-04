"""Reject mismatched GUI, native terminal and dependency profiles before packaging."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import PySide6
from scripts import qt_runtime_contract as contract, linux_runtime_check as linux
from scripts import build_linux_terminal as terminal


class QtRuntimeContractTests(unittest.TestCase):
    def test_real_test_interpreter_matches_current_pin(self):
        self.assertEqual(contract.require_qt_runtime(), dict(pyside='6.3.2', qt='6.3.2'))

    def test_binding_and_loaded_qt_must_both_match(self):
        for pyside, qt in [('6.3.0', '6.3.2'), ('6.3.2', '6.3.0')]:
            with self.subTest(pyside=pyside, qt=qt), patch.object(PySide6, '__version__', pyside), patch('PySide6.QtCore.qVersion', return_value=qt):
                with self.assertRaisesRegex(RuntimeError, 'Qt runtime mismatch'):
                    contract.require_qt_runtime()

    def test_ambiguous_or_unpinned_requirements_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'requirements.txt'
            for text in ['PySide6>=6.3.2', 'PySide6==6.3.2\nPySide6==6.3.0', 'paramiko==5.0.0']:
                path.write_text(text, encoding='utf-8')
                with self.subTest(text=text), self.assertRaises(RuntimeError):
                    contract.expected_qt_version(path)

    def test_old_linux_profile_stops_before_dependency_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'requirements.txt'
            path.write_text('PySide6==6.3.0\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Review the runtime profile'):
                linux.pinned_requirements(path)

    def test_native_terminal_admission_checks_binding_before_compilation(self):
        with patch.object(terminal, 'host_errors', return_value=[]), patch.object(PySide6, '__version__', '6.3.0'):
            with self.assertRaisesRegex(RuntimeError, 'Qt runtime mismatch'):
                terminal.admit_host()


if __name__=='__main__':unittest.main()
