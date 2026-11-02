"""Wheel routing regressions using the application's real widget policy."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QScrollArea, QPushButton, QLabel,
    QSpinBox, QComboBox, QSlider, QPlainTextEdit, QDialog,
)
from modules.interaction_guards import install_interaction_guards
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.common_widgets.action_scroll_area import ActionScrollArea

APP = QApplication.instance() or QApplication([])


def wheel(widget, angle=-120, pixel=0):
    local = QPoint(4, 4)
    event = QWheelEvent(QPointF(local), QPointF(widget.mapToGlobal(local)),
                        QPoint(0, pixel), QPoint(0, angle), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, False)
    APP.sendEvent(widget, event)
    APP.processEvents()


class WheelRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_interaction_guards(APP)

    def make_area(self, kind=QScrollArea, dialog=False):
        host = QDialog() if dialog else QWidget()
        host_layout = QVBoxLayout(host)
        body = QWidget()
        layout = QVBoxLayout(body)
        button = QPushButton("Install capture dependencies", body)
        label = QLabel("Help text", body)
        spin = QSpinBox(body)
        spin.setValue(5)
        combo = QComboBox(body)
        combo.addItems(["first", "second"])
        slider = QSlider(Qt.Horizontal, body)
        slider.setValue(40)
        for widget in (button, label, spin, combo, slider):
            layout.addWidget(widget)
        spacer = QWidget(body)
        spacer.setFixedHeight(1200)
        layout.addWidget(spacer)
        area = kind(body, host) if kind is ActionScrollArea else kind(host)
        if kind is not ActionScrollArea:
            area.setWidget(body)
            area.setWidgetResizable(True)
        host_layout.addWidget(area)
        host.resize(420, 260)
        host.show()
        APP.processEvents()
        self.addCleanup(host.deleteLater)
        self.addCleanup(host.close)
        return area, (button, label, spin, combo, slider), layout

    def test_enabled_disabled_and_noninteractive_children_scroll(self):
        for kind in (QScrollArea, BounceScrollArea, ActionScrollArea):
            area, controls, _ = self.make_area(kind)
            for control in controls[:2]:
                for enabled in (True, False):
                    with self.subTest(area=kind.__name__, control=type(control).__name__, enabled=enabled):
                        control.setEnabled(enabled)
                        area.verticalScrollBar().setValue(0)
                        wheel(control)
                        self.assertGreater(area.verticalScrollBar().value(), 0)

    def test_parameters_scroll_page_without_changing_values(self):
        area, controls, _ = self.make_area(dialog=True)
        for control in controls[2:]:
            for target in (control, control.lineEdit()) if isinstance(control, QSpinBox) else (control,):
                with self.subTest(control=type(target).__name__):
                    area.verticalScrollBar().setValue(0)
                    wheel(target)
                    self.assertGreater(area.verticalScrollBar().value(), 0)
        self.assertEqual(controls[2].value(), 5)
        self.assertEqual(controls[3].currentIndex(), 0)
        self.assertEqual(controls[4].value(), 40)

    def test_native_text_viewport_keeps_own_scroll(self):
        area, _, layout = self.make_area()
        editor = QPlainTextEdit()
        editor.setPlainText("\n".join(str(i) for i in range(100)))
        editor.setFixedHeight(120)
        layout.insertWidget(0, editor)
        APP.processEvents()
        area.verticalScrollBar().setValue(0)
        wheel(editor.viewport())
        self.assertGreater(editor.verticalScrollBar().value(), 0)
        self.assertEqual(area.verticalScrollBar().value(), 0)

    def test_nearest_nested_scroll_owner(self):
        outer, _, layout = self.make_area()
        inner = QScrollArea()
        inner.setFixedHeight(120)
        body = QWidget()
        body.setMinimumHeight(600)
        child = QPushButton("Nested action", body)
        inner.setWidget(body)
        layout.insertWidget(0, inner)
        APP.processEvents()
        outer.verticalScrollBar().setValue(0)
        wheel(child)
        self.assertGreater(inner.verticalScrollBar().value(), 0)
        self.assertEqual(outer.verticalScrollBar().value(), 0)

    def test_popup_wheel_does_not_scroll_background(self):
        area, controls, _ = self.make_area()
        combo = controls[3]
        combo.addItems([str(i) for i in range(100)])
        combo.showPopup()
        APP.processEvents()
        self.assertIsNotNone(APP.activePopupWidget())
        before = area.verticalScrollBar().value()
        wheel(combo.view().viewport())
        self.assertEqual(area.verticalScrollBar().value(), before)
        combo.hidePopup()

    def test_install_is_idempotent_and_new_widgets_are_covered(self):
        self.assertIs(install_interaction_guards(APP), install_interaction_guards(APP))
        area, controls, _ = self.make_area()
        wheel(controls[0])
        self.assertGreater(area.verticalScrollBar().value(), 0)
