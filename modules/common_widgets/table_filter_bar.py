"""Persistent table filtering; matching stays in the host table."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QLabel
from modules.ui_scale_manager import UiScaleManager


class TableFilterBar(QWidget):
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setObjectName("captureLineEdit")
        self.search.setPlaceholderText("搜索过滤表格内容")
        self.search.setToolTip("输入即过滤；多个关键词用空格分隔。")
        self.search.setClearButtonEnabled(True)
        self.search.setMaxLength(256)
        self._row.addWidget(self.search, 1)
        self.count = QLabel("0/0", self)
        self.count.setObjectName("captureDescription")
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._row.addWidget(self.count)
        self.search.textChanged.connect(self.changed)
        self.refresh_scale()

    def set_count(self, shown, total):
        self.count.setText(f"{shown}/{total}")

    def refresh_scale(self):
        scale = UiScaleManager.for_widget(self)
        scale.bind_layout(self._row, margins=(0, 0, 0, 0), spacing=8)
        scale.bind_metrics(self.search, min_height=32)
