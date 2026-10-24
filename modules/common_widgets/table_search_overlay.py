"""Single-line adaptation of WiKey's bottom-right table filter entrypoint."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel
from modules.common_widgets.icon_button import create_themed_icon_button, refresh_tool_button_icons
from modules.config_manager import BASE_DIR
from modules.dialog_theme import detect_light_theme
from modules.ui_scale_manager import UiScaleManager


class TableSearchOverlay(QWidget):
    """Own only search UI; filter policy and row data belong to the table."""
    changed = Signal(str)

    def __init__(self, table):
        super().__init__(table)
        self._table = table
        self._positioning = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        self.panel = QFrame(self)
        self.panel.setObjectName("tableSearchPanel")
        row = QHBoxLayout(self.panel)
        row.setContentsMargins(8, 6, 8, 6)
        self.search = QLineEdit(self.panel)
        self.search.setObjectName("captureLineEdit")
        self.search.setPlaceholderText("\u641c\u7d22\u8868\u683c\u5185\u5bb9")
        self.search.setToolTip("\u8f93\u5165\u5373\u8fc7\u6ee4\uff1b\u591a\u4e2a\u5173\u952e\u8bcd\u7528\u7a7a\u683c\u5206\u9694\u3002")
        self.search.setClearButtonEnabled(True)
        self.search.setMaxLength(256)
        row.addWidget(self.search, 1)
        self.count = QLabel(self.panel)
        self.count.setObjectName("captureDescription")
        row.addWidget(self.count)
        root.addWidget(self.panel)
        bottom = QHBoxLayout()
        bottom.addStretch()
        self.toggle = create_themed_icon_button(self, "", "\u5c55\u5f00\u641c\u7d22",
            light_color=QColor("#15803d"), dark_color=QColor("#4ade80"))
        self.toggle.setObjectName("tableSearchToggle")
        self.toggle.setCheckable(True)
        self.toggle.setAutoRaise(True)
        self.toggle.setAccessibleName("\u5c55\u5f00\u6216\u6536\u8d77\u8868\u683c\u641c\u7d22")
        bottom.addWidget(self.toggle)
        root.addLayout(bottom)
        self.panel.hide()
        self.toggle.toggled.connect(self._expand)
        self.search.textChanged.connect(self.changed)
        table.installEventFilter(self)
        table.viewport().installEventFilter(self)
        for bar in (table.verticalScrollBar(), table.horizontalScrollBar()):
            bar.rangeChanged.connect(self.reposition)
        self.refresh_scale()

    def _expand(self, expanded):
        self.panel.setVisible(expanded)
        self._sync_icon()
        self.reposition()
        if expanded:
            self.search.setFocus(Qt.OtherFocusReason)

    def _sync_icon(self):
        icon = "cil-chevron-bottom.png" if self.toggle.isChecked() else "cil-chevron-top.png"
        self.toggle.setProperty("iconPath", str(BASE_DIR / "images/icons" / icon))
        self.toggle.setToolTip("\u6536\u8d77\u641c\u7d22" if self.toggle.isChecked() else "\u5c55\u5f00\u641c\u7d22")
        refresh_tool_button_icons(self, detect_light_theme(self))

    def set_count(self, shown, total):
        self.count.setText(f"{shown}/{total}")
        self.reposition()

    def refresh_scale(self):
        scale = UiScaleManager.for_widget(self)
        scale.bind_metrics(self.toggle, fixed_size=(36, 36), icon_size=25)
        self._sync_icon()
        self.reposition()

    def reposition(self, *_args):
        if self._positioning:
            return
        self._positioning = True
        try:
            rect = self._table.viewport().geometry()
            scale = UiScaleManager.for_widget(self)
            margin = scale.px(8)
            width = max(1, min(scale.px(400), rect.width() - margin * 2))
            self.count.setVisible(width >= scale.px(220))
            if self.toggle.isChecked():
                self.setFixedWidth(width)
            else:
                self.setFixedWidth(self.toggle.width())
            self.setFixedHeight(self.sizeHint().height())
            self.move(max(rect.left(), rect.right()+1-self.width()-margin),
                      max(rect.top(), rect.bottom()+1-self.height()-margin))
            self.raise_()
        finally:
            self._positioning = False

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.reposition()
        return False
