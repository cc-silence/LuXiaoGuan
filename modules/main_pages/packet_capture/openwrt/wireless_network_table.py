"""Wireless scan table: stable row identity, numeric sorting and batched privacy."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QWidget, QHBoxLayout, QPushButton, QHeaderView
from modules.common_widgets.spaced_item_delegate import minimum_row_height
from modules.common_widgets.table_search_overlay import TableSearchOverlay
from modules.capture_services.privacy_mask import mask_text
from modules.ui_scale_manager import UiScaleManager

SORT_ROLE = Qt.UserRole + 1


class NumericItem(QTableWidgetItem):
    def __lt__(self, other):
        left, right = self.data(SORT_ROLE), other.data(SORT_ROLE)
        # Treat unknown power as lower than every measured value.
        return (float("-inf") if left is None else left) < (float("-inf") if right is None else right)


class WirelessNetworkTable(QTableWidget):
    joinRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(0, 7, parent)
        self.setObjectName("captureAccessPointTable")
        self.setProperty("wirelessNetworks", True)
        self.networks = ()
        self._privacy_key = None
        self._mask_names = False
        self._private = False
        self._resizing_actions = False
        self._ready = False
        self.search_overlay = TableSearchOverlay(self)
        self.search_overlay.changed.connect(self.apply_filter)
        self.model().layoutChanged.connect(self.apply_filter)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.setSortingEnabled(True)
        self.sortItems(4, Qt.DescendingOrder)

    def network_at(self, row):
        item = self.item(row, 0)
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
                values = (network.ssid or "\u9690\u85cf\u7f51\u7edc", network.bssid, network.band,
                          str(network.channel) if network.channel is not None else "\u672a\u77e5",
                          f"{network.power} dBm" if network.power is not None else "\u672a\u77e5",
                          {"psk2": "WPA2", "sae": "WPA3", "sae-mixed": "WPA2 / WPA3",
                           "none": "\u5f00\u653e\u7f51\u7edc"}.get(network.encryption, network.encryption or "\u4e0d\u652f\u6301"))
                for column, text in enumerate(values):
                    item = self.item(row, column)
                    if item is None:
                        item = NumericItem(text) if column in (3, 4) else QTableWidgetItem(text)
                        self.setItem(row, column, item)
                    elif item.text() != text:
                        item.setText(text)
                    if column in (3, 4):
                        item.setData(SORT_ROLE, network.channel if column == 3 else network.power)
                    if column == 0:
                        item.setData(Qt.UserRole, index)
                    item.setTextAlignment(Qt.AlignVCenter | (Qt.AlignLeft if column == 0 else Qt.AlignHCenter))
                cell = self.cellWidget(row, 6)
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
                self.setCellWidget(row, 6, cell)
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
            network, cell = self.network_at(row), self.cellWidget(row, 6)
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
                for column, text in enumerate(shown):
                    item = self.item(row, column)
                    if item.text() != text:
                        item.setText(text)
                detail = getattr(network, "reason", "") or ("\u7f51\u5361\uff1a" + getattr(network, "radio", "")
                                             + " \u00b7 \u63a5\u5165\u70b9\uff1a" + shown[1])
                for column in range(6):
                    item = self.item(row, column)
                    if item.toolTip() != detail:
                        item.setToolTip(detail)
                cell = self.cellWidget(row, 6)
                button = cell.findChild(QPushButton) if cell is not None else None
                if button is not None:
                    button.setToolTip(getattr(network, "reason", "") or "\u8f93\u5165\u5bc6\u7801\u5e76\u5b9e\u9645\u9a8c\u8bc1\u65e0\u7ebf\u8fde\u63a5\u3002")
            self._privacy_key = key
        finally:
            self.setSortingEnabled(sorting)
            self.setUpdatesEnabled(updates)
        self.apply_filter()

    def apply_filter(self, *_args):
        tokens = self.search_overlay.search.text().casefold().split()
        shown = 0
        for row in range(self.rowCount()):
            text = " ".join(self.item(row, col).text() for col in range(6)).casefold() if tokens else ""
            visible = all(token in text for token in tokens)
            if self.isRowHidden(row) == visible:
                self.setRowHidden(row, not visible)
            shown += visible
        self.search_overlay.set_count(shown, self.rowCount())

    def begin_appearance_update(self):
        self._appearance_depth = getattr(self, "_appearance_depth", 0) + 1

    def end_appearance_update(self):
        self._appearance_depth -= 1
        if self._appearance_depth == 0:
            self.refresh_scale()

    def refresh_scale(self):
        if getattr(self, "_appearance_depth", 0):
            return
        if self._resizing_actions or not hasattr(self, "search_overlay"):
            return
        self._resizing_actions = True
        try:
            scale = UiScaleManager.for_widget(self)
            width = scale.px(120)
            for row in range(self.rowCount()):
                cell = self.cellWidget(row, 6)
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
            self.setColumnWidth(6, width)
            for column in (2, 3, 4):
                self.resizeColumnToContents(column)
            self.search_overlay.refresh_scale()
        finally:
            self._resizing_actions = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.StyleChange, QEvent.FontChange):
            self.refresh_scale()
