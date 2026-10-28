"""信号行视图，结构不变时就地更新。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget
from modules.ui_scale_manager import UiScaleManager
from .signal_runtime_details import runtime_detail_text
from .signal_runtime_state import header_state, card_emphasis


class SignalRowWidget(QFrame):
    """保留控件和焦点，按最新模型处理按钮点击。"""
    def __init__(self, page, row):
        super().__init__(page.content)
        self.page, self.row = page, row
        self._runtime_status = None
        self._runtime_row = None
        self.setObjectName('captureSignalRow')
        self.setProperty('rowSection', row.section)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10,4,10,4)
        outer.setSpacing(4)
        line = QHBoxLayout()
        line.setSpacing(8)
        self.name_label = QLabel(self)
        self.name_label.setObjectName('captureDescription')
        self.name_label.setTextFormat(Qt.PlainText)
        self.band_label = QLabel(self)
        self.band_label.setObjectName('captureDescription')
        state_group = QWidget(self)
        state_layout = QHBoxLayout(state_group)
        state_layout.setContentsMargins(0, 0, 0, 0)
        state_layout.setSpacing(0)
        self.state_label = QLabel(state_group)
        self.state_label.setObjectName('captureStatusValue')
        self.state_label.setTextFormat(Qt.PlainText)
        self.state_label.setWordWrap(True)
        self.network_label = QLabel(state_group)
        self.network_label.setObjectName('captureStatusValue')
        self.network_label.setTextFormat(Qt.PlainText)
        self.network_label.setWordWrap(True)
        state_layout.addWidget(self.state_label)
        state_layout.addWidget(self.network_label)
        state_layout.addStretch(1)
        self.button = QPushButton(self)
        self.button.setObjectName('captureSecondaryButton')
        self.button.setCursor(Qt.PointingHandCursor)
        UiScaleManager.for_widget(page).bind_metrics(self.button, fixed_height=26)
        self.button.clicked.connect(self._clicked)
        self.edit_button = QPushButton("编辑", self)
        self.edit_button.setObjectName("captureSecondaryButton")
        self.edit_button.setCursor(Qt.PointingHandCursor)
        self.edit_button.setAutoDefault(False)
        self.edit_button.setToolTip("查看和编辑对应的发射或中继设置。")
        UiScaleManager.for_widget(page).bind_metrics(self.edit_button, fixed_height=26)
        self.edit_button.clicked.connect(self._edit_clicked)
        self.delete_button = QPushButton("删除", self)
        self.delete_button.setObjectName("captureSecondaryButton")
        self.delete_button.setProperty("riskAction", True)
        self.delete_button.setCursor(Qt.PointingHandCursor)
        self.delete_button.setAutoDefault(False)
        self.delete_button.setToolTip("删除这条信号配置；开始前需要确认。")
        UiScaleManager.for_widget(page).bind_metrics(self.delete_button, fixed_height=26)
        self.delete_button.clicked.connect(lambda: self.page._request_delete(self.row))
        for widget, stretch in ((self.name_label,1),(self.band_label,1),(state_group,2),(self.button,0),(self.edit_button,0),(self.delete_button,0)):
            line.addWidget(widget,stretch)
        outer.addLayout(line)
        self.detail_label = QLabel(self)
        self.detail_label.setObjectName('captureDescription')
        self.detail_label.setTextFormat(Qt.PlainText)
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.detail_label)

    def _clicked(self):
        """点击时用最新行，不捕获过期快照。"""
        if self.page._needs_repair(self.row): self.page._request_repair(self.row)
        else: self.page._request_change(self.row)

    def _edit_clicked(self):
        """编辑始终使用最新行模型，复用新增窗口。"""
        self.page._request_edit(self.row)

    def update_row(self, row, snapshot, editable):
        """只更新变化的文本与状态，不重建控件。"""
        self.row = row
        page = self.page
        for label, text in ((self.name_label,page._shown_name(row)), (self.band_label,page._band_text(row)),
                            (self.button,page._button_text(row))):
            if label.text() != text: label.setText(text)
        self.edit_button.setEnabled(bool(editable and row.mode in ("ap", "sta")
                                         and not snapshot.busy and not snapshot.stale))
        self.delete_button.setEnabled(bool(editable and row.mode in ("ap", "sta") and not snapshot.busy and not snapshot.stale))
        self.button.setEnabled(bool(editable and not snapshot.busy and not snapshot.stale and
                                    not row.reason))
        tip = "启停或重载此信号所属的无线网卡，可能短暂断网；开始前需要确认，建议通过网线连接。"
        self.button.setToolTip(tip)
        self.name_label.setToolTip(f"所属无线网卡：{row.radio}。同网卡的其他信号也可能短暂断网。")

        self._update_runtime(row, snapshot)

    def _update_runtime(self, row, snapshot):
        page = self.page
        # A pending read has no new measurement. Preserve the last rendered result,
        # including when a proof ages during the bounded read; publish changes on completion.
        if (getattr(page.service, '_status_running', False) and snapshot.connected
                and not snapshot.wireless_status_stale and self._runtime_row == row
                and self._runtime_status is snapshot.wireless_status):
            return
        self._runtime_status = snapshot.wireless_status
        self._runtime_row = row
        text, kind, outcome, outcome_kind, explanation = header_state(
            row, snapshot, page._state_text(row), page._state_kind(row))
        for label, value, status in ((self.state_label, text, kind),
                                     (self.network_label, outcome, outcome_kind)):
            if label.text() != value:
                label.setText(value)
            label.setToolTip(explanation)
            if label.property('captureStatus') != status:
                label.setProperty('captureStatus', status)
                label.style().unpolish(label)
                label.style().polish(label)
        self.network_label.setVisible(bool(outcome))
        private = bool(page._privacy and page._privacy.enabled)
        detail = runtime_detail_text(row, snapshot, private)
        if self.detail_label.text() != detail:
            self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))

        emphasis = card_emphasis(row, snapshot)
        if self.property('signalEmphasis') != emphasis:
            self.setProperty('signalEmphasis', emphasis)
            # Descendant selectors must also update when a reused row changes state.
            for widget in (self, *self.findChildren(QLabel), *self.findChildren(QPushButton)):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()
