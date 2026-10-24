"""Wireless scan table: stable row identity, numeric sorting and batched privacy."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QWidget, QHBoxLayout, QPushButton, QHeaderView
from modules.common_widgets.spaced_item_delegate import minimum_row_height
from modules.common_widgets.table_filter_bar import TableFilterBar
from modules.capture_services.privacy_mask import mask_text
from modules.ui_scale_manager import UiScaleManager

SORT_ROLE = Qt.UserRole + 1


class NumericItem(QTableWidgetItem):
    def __lt__(self, other):
        left, right = self.data(SORT_ROLE), other.data(SORT_ROLE)
        # Treat unknown power as lower than every measured value.
        return (float("-inf") if left is None else left) < (float("-inf") if right is None else right)


class _NetworkHeader(QHeaderView):
    def mousePressEvent(self, event):
        if self.logicalIndexAt(event.position().toPoint()) != 0:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.logicalIndexAt(event.position().toPoint()) != 0:
            super().mouseReleaseEvent(event)


class WirelessNetworkTable(QTableWidget):
    NUMBER, SSID, BSSID, BAND, CHANNEL, POWER, ENCRYPTION, ACTION = range(8)
    HEADERS = ("序号", "信号名称", "接入点地址", "频段", "信道", "强度", "加密", "操作")
    joinRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(0, len(self.HEADERS), parent)
        self.setHorizontalHeader(_NetworkHeader(Qt.Horizontal, self))
        self.horizontalHeader().setSectionsClickable(True)
        self.horizontalHeader().setSortIndicatorShown(True)
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.setObjectName("captureAccessPointTable")
        self.setProperty("wirelessNetworks", True)
        self.networks = ()
        self._privacy_key = None
        self._mask_names = False
        self._private = False
        self._resizing_actions = False
        self._ready = False
        self.filter_bar = TableFilterBar(self)
        self.filter_bar.hide()
        self.filter_bar.changed.connect(self.apply_filter)
        self.model().layoutChanged.connect(self.apply_filter)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.setSortingEnabled(True)
        self.sortItems(self.POWER, Qt.DescendingOrder)
        self.horizontalHeader().sectionResized.connect(self._fit_name_column)

    def sortItems(self, column, order=Qt.AscendingOrder):
        if column != self.NUMBER:
            super().sortItems(column, order)

    def network_at(self, row):
        item = self.item(row, self.SSID)
        index = item.data(Qt.UserRole) if item is not None else None
        return self.networks[index] if type(index) is int and 0 <= index < len(self.networks) else None

    def set_networks(self, networks):
        previous = {row: (network.radio, network.bssid)
                    for row in range(self.rowCount())
                    if (network := self.network_at(row)) is not None}
        scroll = self.verticalScrollBar().value()
        incoming = tuple(networks)[:1024]
        wanted = {(network.radio, network.bssid) for network in incoming}
        self._privacy_key = None
        sorting = self.isSortingEnabled()
        self.setUpdatesEnabled(False)
        self.setSortingEnabled(False)
        try:
            for row in range(self.rowCount() - 1, -1, -1):
                if previous.get(row) not in wanted:
                    self.removeRow(row)
            retained = [key for row, key in previous.items() if key in wanted]
            rows = {key: row for row, key in enumerate(retained)}
            self.networks = incoming
            scale = UiScaleManager.for_widget(self)
            for index, network in enumerate(self.networks):
                key = (network.radio, network.bssid)
                row = rows.get(key)
                if row is None:
                    row = self.rowCount()
                    self.insertRow(row)
                values = (str(row + 1), network.ssid or "\u9690\u85cf\u7f51\u7edc", network.bssid, network.band,
                          str(network.channel) if network.channel is not None else "\u672a\u77e5",
                          f"{network.power} dBm" if network.power is not None else "\u672a\u77e5",
                          getattr(network, "encryption_label", "") or
                          {"psk": "WPA-PSK", "psk2": "WPA2-PSK", "psk-mixed": "WPA/WPA2-PSK",
                           "sae": "WPA3-SAE", "sae-mixed": "WPA2/WPA3-PSK/SAE", "owe": "OWE",
                           "none": "开放网络"}.get(network.encryption, network.encryption or "未知加密"))
                for column, text in enumerate(values):
                    item = self.item(row, column)
                    if item is None:
                        item = NumericItem(text) if column in (self.CHANNEL, self.POWER) else QTableWidgetItem(text)
                        self.setItem(row, column, item)
                    elif item.text() != text:
                        item.setText(text)
                    if column in (self.CHANNEL, self.POWER):
                        item.setData(SORT_ROLE, network.channel if column == self.CHANNEL else network.power)
                    if column == self.SSID:
                        item.setData(Qt.UserRole, index)
                    item.setTextAlignment(Qt.AlignVCenter | (Qt.AlignLeft if column == self.SSID else Qt.AlignHCenter))
                cell = self.cellWidget(row, self.ACTION)
                if cell is not None:
                    cell.findChild(QPushButton)._network = network
                    continue
                cell = QWidget(self)
                layout = QHBoxLayout(cell)
                scale.bind_layout(layout, margins=(6, 3, 6, 3), spacing=0)
                button = QPushButton("\u52a0\u5165\u7f51\u7edc", cell)
                button.setObjectName("captureSecondaryButton")
                button.setAutoDefault(False)
                scale.bind_metrics(button, min_width=82)
                button._network = network
                button.clicked.connect(lambda _checked=False, target=button: self.joinRequested.emit(target._network))
                layout.addWidget(button)
                self.setCellWidget(row, self.ACTION, cell)
            self.refresh_privacy(self._mask_names, self._private)
            self.refresh_scale()
            self.set_actions_enabled(self._ready)
        finally:
            self.setSortingEnabled(sorting)
            self.setUpdatesEnabled(True)
        self.verticalScrollBar().setValue(scroll)
        self.apply_filter()

    def set_actions_enabled(self, ready):
        self._ready = bool(ready)
        for row in range(self.rowCount()):
            network, cell = self.network_at(row), self.cellWidget(row, self.ACTION)
            button = cell.findChild(QPushButton) if cell is not None else None
            if button is not None:
                enabled = bool(ready and network is not None and not getattr(network, "reason", ""))
                if button.isEnabled() != enabled:
                    button.setEnabled(enabled)

    def refresh_privacy(self, mask_names, private):
        key = (bool(mask_names), bool(private))
        self._mask_names, self._private = key
        if key == self._privacy_key:
            return
        sorting, updates = self.isSortingEnabled(), self.updatesEnabled()
        self.setSortingEnabled(False)
        self.setUpdatesEnabled(False)
        try:
            for row in range(self.rowCount()):
                network = self.network_at(row)
                if network is None:
                    continue
                shown = (mask_text(network.ssid or "\u9690\u85cf\u7f51\u7edc", mask_names),
                         mask_text(network.bssid, private))
                for column, text in enumerate(shown, self.SSID):
                    item = self.item(row, column)
                    if item.text() != text:
                        item.setText(text)
                detail = getattr(network, "reason", "") or ("\u7f51\u5361\uff1a" + getattr(network, "radio", "")
                                             + " \u00b7 \u63a5\u5165\u70b9\uff1a" + shown[1])
                for column in range(self.ACTION):
                    item = self.item(row, column)
                    tooltip = ((getattr(network, "encryption_detail", "") or self.item(row, self.ENCRYPTION).text()) + "\n" + detail) if column == self.ENCRYPTION else detail
                    if item.toolTip() != tooltip:
                        item.setToolTip(tooltip)
                cell = self.cellWidget(row, self.ACTION)
                button = cell.findChild(QPushButton) if cell is not None else None
                if button is not None:
                    button.setToolTip(getattr(network, "reason", "") or "\u8f93\u5165\u5bc6\u7801\u5e76\u5b9e\u9645\u9a8c\u8bc1\u65e0\u7ebf\u8fde\u63a5\u3002")
            self._privacy_key = key
        finally:
            self.setSortingEnabled(sorting)
            self.setUpdatesEnabled(updates)
        self.apply_filter()

    def apply_filter(self, *_args):
        tokens = self.filter_bar.search.text().casefold().split()
        shown = 0
        for row in range(self.rowCount()):
            text = " ".join(self.item(row, col).text() for col in range(self.SSID, self.ACTION)).casefold() if tokens else ""
            visible = all(token in text for token in tokens)
            if self.isRowHidden(row) == visible:
                self.setRowHidden(row, not visible)
            if visible:
                shown += 1
                item = self.item(row, self.NUMBER)
                if item is not None and item.text() != str(shown):
                    item.setText(str(shown))
        self.filter_bar.set_count(shown, self.rowCount())

    def begin_appearance_update(self):
        self._appearance_depth = getattr(self, "_appearance_depth", 0) + 1

    def end_appearance_update(self):
        self._appearance_depth -= 1
        if self._appearance_depth == 0:
            self.refresh_scale()

    def refresh_scale(self):
        if getattr(self, "_appearance_depth", 0):
            return
        if self._resizing_actions or not hasattr(self, "filter_bar"):
            return
        self._resizing_actions = True
        try:
            scale = UiScaleManager.for_widget(self)
            key = (scale.global_scale, self.font().toString())
            if getattr(self, "_column_scale_key", None) != key:
                self._column_scale_key = key
                self.setColumnWidth(self.NUMBER, max(scale.px(52), self.fontMetrics().horizontalAdvance(self.HEADERS[0]) + scale.px(18)))
                self.setColumnWidth(self.BSSID, max(scale.px(170), self.fontMetrics().horizontalAdvance("00:00:00:00:00:00") + scale.px(20)))
                self.setColumnWidth(self.ENCRYPTION, scale.px(125))
            width = scale.px(120)
            for row in range(self.rowCount()):
                cell = self.cellWidget(row, self.ACTION)
                if cell is None:
                    continue
                cell.ensurePolished()
                button = cell.findChild(QPushButton)
                button.ensurePolished()
                cell.layout().activate()
                height = max(minimum_row_height(self), scale.px(46),
                             cell.sizeHint().height() + scale.px(2))
                self.setRowHeight(row, height)
                width = max(width, cell.sizeHint().width())
            self.setColumnWidth(self.ACTION, width)
            for column in (self.BAND, self.CHANNEL, self.POWER):
                self.resizeColumnToContents(column)
            self._fit_name_column()
            self.filter_bar.refresh_scale()
        finally:
            self._resizing_actions = False

    def _fit_name_column(self, *_args):
        if getattr(self, "_fitting_name", False) or not hasattr(self, "filter_bar"):
            return
        self._fitting_name = True
        try:
            available = self.viewport().width() - sum(self.columnWidth(c) for c in range(self.columnCount()) if c != self.SSID)
            self.setColumnWidth(self.SSID, max(UiScaleManager.for_widget(self).px(180), available))
        finally:
            self._fitting_name = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_name_column()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.StyleChange, QEvent.FontChange):
            self.refresh_scale()
