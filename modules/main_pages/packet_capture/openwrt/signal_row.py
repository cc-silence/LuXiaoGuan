"""信号行视图，结构不变时就地更新。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton
from modules.display_privacy import PrivacyLabel
from modules.ui_scale_manager import UiScaleManager


def detail_text(row):
    """只显示白名单数据，不支持与无设备分开说明。"""
    d = row.details
    if d is None:
        return "详情尚未读取；请空闲后刷新信号列表。"
    unknown = "未知"
    channel = d.channel or unknown
    if d.configured and d.channel:
        channel += "（配置值）"
    lines = [f"接口：{d.interface or unknown}    信道：{channel}    带宽配置：{d.bandwidth or unknown}    加密：{d.encryption or unknown}"]
    if d.note: lines.append(d.note)
    if d.clients_state == 'not_ap':
        lines.append("连接上级网络的接口，不列出下级设备。")
    elif d.clients_state == 'unavailable':
        lines.append("已连接设备：固件未提供可读取的列表，无法判断数量。")
    else:
        lines.append(f"已连接设备：{len(d.clients)} 台" + ("（达到显示上限）" if d.clients_state == 'limited' else ''))
        for c in d.clients:
            signal = f"{c.signal} dBm" if c.signal is not None else "信号未知"
            lines.append(f"{c.name or '名称未知'}  |  {c.ip or '地址未知'}  |  {c.mac}  |  {signal}")
    return '\n'.join(lines)


class SignalRowWidget(QFrame):
    """保留控件和焦点，按最新模型处理按钮点击。"""
    def __init__(self, page, row):
        super().__init__(page.content)
        self.page, self.row = page, row
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
        self.state_label = QLabel(self)
        self.state_label.setObjectName('captureStatusValue')
        self.state_label.setWordWrap(True)
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
        for widget, stretch in ((self.name_label,1),(self.band_label,1),(self.state_label,2),(self.button,0),(self.edit_button,0),(self.delete_button,0)):
            line.addWidget(widget,stretch)
        outer.addLayout(line)
        self.detail_label = PrivacyLabel(self)
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

    def update_row(self, row, snapshot, editable, show_details):
        """只更新变化的文本与状态，不重建控件。"""
        self.row = row
        page = self.page
        for label, text in ((self.name_label,page._shown_name(row)), (self.band_label,page._band_text(row)),
                            (self.state_label,page._state_text(row)), (self.button,page._button_text(row))):
            if label.text() != text: label.setText(text)
        kind = page._state_kind(row)
        if self.state_label.property('captureStatus') != kind:
            self.state_label.setProperty('captureStatus',kind)
            self.state_label.style().unpolish(self.state_label)
            self.state_label.style().polish(self.state_label)
        self.edit_button.setEnabled(bool(editable and row.mode in ("ap", "sta")
                                         and not snapshot.busy and not snapshot.stale))
        self.delete_button.setEnabled(bool(editable and row.mode in ("ap", "sta") and not snapshot.busy and not snapshot.stale))
        self.button.setEnabled(bool(editable and not snapshot.busy and not snapshot.stale and
                                    not row.reason))
        tip = "启停或重载此信号所属的无线网卡，可能短暂断网；开始前需要确认，建议通过网线连接。"
        self.button.setToolTip(tip)
        self.name_label.setToolTip(f"所属无线网卡：{row.radio}。同网卡的其他信号也可能短暂断网。")
        self.detail_label.setVisible(show_details)
        if show_details:
            text = detail_text(row)
            if self.detail_label.text() != text: self.detail_label.setText(text)
