"""Reusable configuration fields with explicit preservation and bounded lists."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget
from modules.ui_scale_manager import UiScaleManager
from modules.common_widgets.icon_button import create_themed_icon_button


class ConfigCheckBox(QCheckBox):
    """Show known values; preserve untouched or unspecified configuration."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.configure(None)

    def configure(self, value, *, preserve=True):
        self._preserve = preserve
        state = (Qt.PartiallyChecked if value is None else
                 Qt.Checked if str(value) == "1" else Qt.Unchecked)
        self.setTristate(value is None)
        self.setCheckState(state)
        self._original = state
        self._update_text()
        if not getattr(self, "_text_connected", False):
            self.stateChanged.connect(self._update_text)
            self._text_connected = True

    def _update_text(self, *_):
        self.setText("沿用系统设置" if self.checkState() == Qt.PartiallyChecked else "")

    def nextCheckState(self):
        # User clicks always choose a definite value; unknown is initialization only.
        self.setCheckState(Qt.Unchecked if self.checkState() == Qt.Checked else Qt.Checked)

    def value(self):
        if self.checkState() == Qt.PartiallyChecked:
            return None
        return "1" if self.isChecked() else "0"

    def pending_value(self):
        if self._preserve and self.checkState() == self._original:
            return None
        return self.value()


class ConfigValueList(QWidget):
    """Editable rows with add/remove buttons, bounded independently of input."""

    MAX_ENTRIES = 512

    def __init__(self, parent=None, *, placeholder=""):
        super().__init__(parent)
        self._rows = []
        self._placeholder = placeholder
        self._layout = QVBoxLayout(self)
        scale = UiScaleManager.for_widget(self)
        scale.bind_layout(self._layout, margins=(0, 0, 0, 0), spacing=4)
        self.add_button = create_themed_icon_button(self, ":/icons/images/icons/cil-plus.png", "添加一项")
        self.add_button.setAccessibleName("添加一项")
        scale.bind_metrics(self.add_button, fixed_size=(30, 30), icon_size=16)
        self.add_button.clicked.connect(lambda: self.add_value())
        self._layout.addWidget(self.add_button, 0, Qt.AlignLeft)
        self.add_value()

    def add_value(self, value=""):
        if len(self._rows) >= self.MAX_ENTRIES:
            return
        row = QWidget(self)
        layout = QHBoxLayout(row)
        scale = UiScaleManager.for_widget(self)
        scale.bind_layout(layout, margins=(0, 0, 0, 0), spacing=4)
        edit = QLineEdit(str(value), row)
        edit.setPlaceholderText(self._placeholder)
        remove = create_themed_icon_button(row, ":/icons/images/icons/cil-minus.png", "删除此项")
        remove.setAccessibleName("删除此项")
        scale.bind_metrics(remove, fixed_size=(30, 30), icon_size=16)
        layout.addWidget(edit, 1)
        layout.addWidget(remove)
        self._rows.append((row, edit))
        self._layout.insertWidget(self._layout.count() - 1, row)
        remove.clicked.connect(lambda: self._remove(row))
        self.add_button.setEnabled(len(self._rows) < self.MAX_ENTRIES)

    def _remove(self, row):
        self._rows = [(widget, edit) for widget, edit in self._rows if widget is not row]
        self._layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        if not self._rows:
            self.add_value()
        self.add_button.setEnabled(len(self._rows) < self.MAX_ENTRIES)

    def set_values(self, values):
        values = list(values)
        if len(values) > self.MAX_ENTRIES:
            raise ValueError("列表项目过多，请先减少到 512 项以内。")
        for row, _ in self._rows:
            self._layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._rows.clear()
        for value in values or [""]:
            self.add_value(value)

    def values(self):
        return [value for _, edit in self._rows for value in edit.text().split()]

    def setPlaceholderText(self, text):
        self._placeholder = text
        for _, edit in self._rows:
            edit.setPlaceholderText(text)
