"""Table-owned search overlay; viewport events position it without timers."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QWidget, QFrame, QToolButton, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QCheckBox, QLabel
from modules.ui_scale_manager import UiScaleManager


class TableFilterPanel(QWidget):
    changed = Signal()

    def __init__(self, table):
        super().__init__(table)
        self._viewport = table.viewport()
        self._viewport.installEventFilter(self)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self.panel = QFrame(self)
        self.panel.setObjectName('tableFilterPanel')
        self.panel.setAttribute(Qt.WA_StyledBackground)
        body = QVBoxLayout(self.panel)
        self.search = QPlainTextEdit(self.panel)
        self.search.setObjectName('captureTextEdit')
        self.search.setPlaceholderText('\u641c\u7d22\u4fe1\u53f7\u540d\u79f0\u6216 MAC\uff0c\u6bcf\u884c\u4e00\u4e2a\u5173\u952e\u8bcd')
        self.search.setMaximumBlockCount(100)
        self.search.setTabChangesFocus(True)
        self.search.setFixedHeight(self.fontMetrics().lineSpacing() * 3 + 16)
        body.addWidget(self.search)
        self.only_checked = QCheckBox('\u4ec5\u663e\u793a\u52fe\u9009\u7684\u4fe1\u53f7', self.panel)
        self.hide_hidden = QCheckBox('\u4e0d\u663e\u793a\u9690\u85cf\u4fe1\u53f7', self.panel)
        for check in (self.only_checked, self.hide_hidden):
            check.setObjectName('captureCheckBox')
            check.toggled.connect(self.changed)
            body.addWidget(check)
        self.count = QLabel(self.panel)
        self.count.setObjectName('captureDescription')
        body.addWidget(self.count)
        self.search.textChanged.connect(self.changed)
        root.addWidget(self.panel, 1)
        self.toggle = QToolButton(self)
        self.toggle.setObjectName('tableFilterToggle')
        title = '\u641c\u7d22\u4e0e\u7b5b\u9009'
        self.toggle.setText('\n'.join(title))
        self.toggle.setAccessibleName(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.toggled.connect(self._expand)
        root.addWidget(self.toggle, 0, Qt.AlignBottom)
        self._expand(False)

    def _expand(self, expanded):
        self.panel.setVisible(expanded)
        self.toggle.setArrowType(Qt.RightArrow if expanded else Qt.LeftArrow)
        self.reposition()
        if expanded:
            self.search.setFocus(Qt.OtherFocusReason)

    def reposition(self):
        scale = UiScaleManager.for_widget(self)
        margin = scale.px(8)
        width = max(1, self._viewport.width() - margin * 2)
        self.setFixedWidth(min(scale.px(360), width) if self.toggle.isChecked() else min(self.toggle.sizeHint().width() + 2, width))
        self.adjustSize()
        rect = self._viewport.geometry()
        self.move(max(rect.left(), rect.right()+1-self.width()-scale.px(2)), max(rect.top(), rect.bottom()+1-self.height()-scale.px(2)))
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is self._viewport and event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show, QEvent.FontChange):
            self.reposition()
        return super().eventFilter(watched, event)
