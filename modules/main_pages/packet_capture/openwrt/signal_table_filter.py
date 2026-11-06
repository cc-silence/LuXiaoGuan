"""Filter rows using original scan metadata, independent of target selection."""
from PySide6.QtCore import QObject, Qt
from modules.common_widgets.table_filter_panel import TableFilterPanel


class SignalTableFilter(QObject):
    def __init__(self, table):
        super().__init__(table)
        self.table = table
        self.overlay = TableFilterPanel(table)
        self.overlay.changed.connect(self.refresh)
        table.selectionChangedByUser.connect(self.refresh)
        table.horizontalHeader().sortIndicatorChanged.connect(self.refresh)

    def refresh(self, *_args):
        table = self.table
        if table._checkbox_paint.active:
            return
        terms = tuple(line.strip().casefold() for line in self.overlay.search.toPlainText().splitlines() if line.strip())
        points = {point.bssid: point for point in table._points}
        shown = 0
        for row in range(table.rowCount()):
            item = table.item(row, table.STATUS_COLUMN)
            point = points.get(str(item.data(Qt.UserRole))) if item else None
            text = (point.ssid + ' ' + point.bssid).casefold() if point else ''
            visible = bool(point) and (not terms or any(term in text for term in terms))
            visible = visible and (not self.overlay.only_checked.isChecked() or item.checkState() == Qt.Checked)
            visible = visible and (not self.overlay.hide_hidden.isChecked() or not point.ssid_hidden)
            if table.isRowHidden(row) == visible:
                table.setRowHidden(row, not visible)
            shown += int(visible)
        self.overlay.count.setText(f'\u663e\u793a {shown}/{table.rowCount()}')
        self.overlay.reposition()
