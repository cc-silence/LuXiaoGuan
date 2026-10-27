"""Native and themed button pixels plus keyboard activation."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from PySide6.QtGui import QImage, QPainter
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QToolButton, QStyleFactory, QStyle, QStyleOptionButton
from modules.common_widgets.button_focus_style import ButtonFocusStyle, install_button_focus_style

APP = QApplication.instance() or QApplication([])


class ButtonFocusTests(unittest.TestCase):
    def test_native_and_stylesheet_buttons_keep_keyboard_without_focus_pixels(self):
        original = APP.style().objectName()
        try:
            for name in QStyleFactory.keys():
                APP.setStyle(ButtonFocusStyle(name))
                for color in (None, "#24282e", "#f0f2f5"):
                    with self.subTest(style=name, color=color):
                        host = QWidget()
                        layout = QVBoxLayout(host)
                        buttons = [QPushButton("Open package folder"), QToolButton()]
                        buttons[1].setText("Tool action")
                        for button in buttons:
                            layout.addWidget(button)
                        if color:
                            host.setStyleSheet("QPushButton, QToolButton {background: " + color + "; border: 1px solid #666; padding: 8px;}")
                        host.show()
                        host.activateWindow()
                        APP.processEvents()
                        for button in buttons:
                            button.setFocus(Qt.TabFocusReason)
                            APP.processEvents()
                            self.assertTrue(button.hasFocus())
                            focused = button.grab().toImage()
                            spy = QSignalSpy(button.clicked)
                            QTest.keyClick(button, Qt.Key_Space)
                            self.assertEqual(spy.count(), 1)
                            button.clearFocus()
                            APP.processEvents()
                            self.assertEqual(focused, button.grab().toImage())
                        buttons[0].setFocus(Qt.TabFocusReason)
                        QTest.keyClick(buttons[0], Qt.Key_Tab)
                        self.assertTrue(buttons[1].hasFocus())
                        host.close()
                        host.deleteLater()
        finally:
            APP.setStyle(original)

    def test_explicit_native_keyboard_focus_changes_only_focus_pixels(self):
        original = APP.style().objectName()
        try:
            for name in QStyleFactory.keys():
                if name.lower() not in ("windows", "windowsvista"):
                    continue
                button = QPushButton("Open package folder")
                button.resize(220, 40)

                def render(focused):
                    image = QImage(220, 40, QImage.Format_ARGB32)
                    image.fill(0)
                    option = QStyleOptionButton()
                    button.initStyleOption(option)
                    option.state &= ~QStyle.State_HasFocus
                    if focused:
                        option.state |= QStyle.State_HasFocus | QStyle.State_KeyboardFocusChange
                    painter = QPainter(image)
                    button.style().drawControl(QStyle.CE_PushButton, option, painter, button)
                    painter.end()
                    return image

                APP.setStyle(name)
                normal, focus = render(False), render(True)
                self.assertNotEqual(normal, focus, name)
                APP.setStyle(ButtonFocusStyle(name))
                self.assertEqual(normal, render(False), name)
                self.assertEqual(normal, render(True), name)
                from pathlib import Path
                output = Path("build/review/button-focus")
                output.mkdir(parents=True, exist_ok=True)
                focus.save(str(output / (name + "-before.png")))
                render(True).save(str(output / (name + "-after.png")))
        finally:
            APP.setStyle(original)

    def test_real_startup_policy_order_shares_one_live_native_style(self):
        import subprocess
        import sys
        code = r"""
from PySide6.QtWidgets import QApplication, QPushButton, QLineEdit, QStyle, QStyleOptionButton, QStyleOptionFocusRect
from PySide6.QtGui import QImage, QPainter
from shiboken6 import isValid
from modules.common_widgets.button_focus_style import install_button_focus_style
from modules.interaction_guards import install_interaction_guards
from modules.common_widgets.path_drop import install_path_drop_support
from modules.common_widgets.input_appearance import install_input_appearance
from modules.common_widgets.tooltip_policy import install_tooltip_policy
import sys
app = QApplication([])
app.setStyle(sys.argv[1])
install_button_focus_style(app)
style = app.style()
for install in (install_interaction_guards, install_path_drop_support, install_input_appearance, install_tooltip_policy):
    install(app)
    assert app.style() is style and isValid(style)
assert len(style.registrations) == 4
button = QPushButton('Test')
button.resize(120, 40)
edit = QLineEdit()
edit.ensurePolished()
def render(focused):
    image = QImage(120, 40, QImage.Format_ARGB32)
    image.fill(0)
    option = QStyleOptionButton()
    button.initStyleOption(option)
    option.state &= ~QStyle.State_HasFocus
    if focused:
        option.state |= QStyle.State_HasFocus | QStyle.State_KeyboardFocusChange
    painter = QPainter(image)
    button.style().drawControl(QStyle.CE_PushButton, option, painter, button)
    painter.end()
    return image
if sys.argv[1].lower() in ('windows','windowsvista'):
    assert render(False) == render(True)
# Fusion can retain a solid focus highlight. Only the dotted frame primitive
# is suppressed; other native focus-dependent colors/borders stay intact.
image = QImage(120, 40, QImage.Format_ARGB32)
image.fill(0)
option = QStyleOptionFocusRect()
option.initFrom(button)
option.rect = button.rect()
option.state |= QStyle.State_HasFocus | QStyle.State_KeyboardFocusChange
painter = QPainter(image)
button.style().drawPrimitive(QStyle.PE_FrameFocusRect, option, painter, button)
painter.end()
assert not any(image.pixelColor(x,y).alpha() for x in range(image.width()) for y in range(image.height()))
print('shared_startup_style_ok')
"""
        for name in QStyleFactory.keys():
            with self.subTest(style=name):
                result = subprocess.run([sys.executable, '-c', code, name], capture_output=True,
                                        text=True, timeout=25, env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('shared_startup_style_ok', result.stdout)

    def test_installation_is_idempotent(self):
        install_button_focus_style(APP)
        style = APP.style()
        install_button_focus_style(APP)
        self.assertIs(APP.style(), style)


if __name__ == "__main__":
    unittest.main()
