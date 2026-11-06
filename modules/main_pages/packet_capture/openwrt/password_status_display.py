"""Password-only row labels and active-target following; no task execution."""
from PySide6.QtCore import Qt, QItemSelectionModel, QSignalBlocker
from PySide6.QtWidgets import QAbstractItemView
from PySide6.QtGui import QColor

LABELS = {
    'queued': ('\u5f85\u9a8c\u8bc1', '\u5df2\u52a0\u5165\u672c\u6b21\u4efb\u52a1\uff0c\u5c1a\u672a\u5f00\u59cb\u3002'),
    'verifying': ('\u9a8c\u8bc1\u4e2d', '\u6b63\u5728\u9a8c\u8bc1\u5b57\u5178\u5bc6\u7801\u3002'),
    'found': ('\u627e\u5230\u5bc6\u7801', '\u5df2\u9a8c\u8bc1\u627e\u5230\u5bc6\u7801\uff0c\u7ed3\u679c\u5df2\u4fdd\u5b58\u3002'),
    'no_match': ('\u672a\u627e\u5230', '\u672c\u6b21\u6240\u9009\u5b57\u5178\u7684\u5019\u9009\u5df2\u8bd5\u5b8c\uff0c\u672a\u627e\u5230\u5bc6\u7801\u3002'),
    'timeout': ('\u5df2\u8d85\u65f6', '\u9a8c\u8bc1\u8d85\u65f6\uff0c\u672a\u5b8c\u6210\u90e8\u5206\u4e0d\u5224\u5b9a\u4e3a\u5bc6\u7801\u9519\u8bef\u3002'),
    'unconfirmed': ('\u672a\u786e\u8ba4', '\u8fde\u63a5\u7ed3\u679c\u672a\u786e\u8ba4\uff0c\u4e0d\u80fd\u5224\u5b9a\u4e3a\u5bc6\u7801\u9519\u8bef\u3002'),
    'cancelled': ('\u5df2\u53d6\u6d88', '\u4efb\u52a1\u5df2\u53d6\u6d88\uff0c\u672a\u5b8c\u6210\u90e8\u5206\u4e0b\u6b21\u53ef\u7ee7\u7eed\u9a8c\u8bc1\u3002'),
    'not_started': ('\u672a\u5f00\u59cb', '\u4efb\u52a1\u7ed3\u675f\u524d\u5c1a\u672a\u5f00\u59cb\u5904\u7406\u6b64\u4fe1\u53f7\u3002'),
    'history_skipped': ('\u5386\u53f2\u8df3\u8fc7', '\u5019\u9009\u4ee5\u524d\u5df2\u6709\u786e\u8ba4\u8bb0\u5f55\uff0c\u672c\u6b21\u4e0d\u518d\u91cd\u590d\u9a8c\u8bc1\uff1b\u4e0d\u4ee3\u8868\u5df2\u627e\u5230\u5bc6\u7801\u3002'),
    'round_pending': ('\u5f85\u4e0b\u4e00\u8f6e', '\u672c\u8f6e\u5b57\u5178\u5df2\u5904\u7406\uff0c\u540e\u7eed\u5b57\u5178\u8fd8\u4f1a\u7ee7\u7eed\u9a8c\u8bc1\u3002'),
    'skipped': ('\u5df2\u8df3\u8fc7', '\u672c\u76ee\u6807\u672a\u6267\u884c\u5bc6\u7801\u9a8c\u8bc1\uff0c\u8bf7\u67e5\u770b\u539f\u56e0\u3002'),
    'failed': ('\u9a8c\u8bc1\u5931\u8d25', '\u9a8c\u8bc1\u5f02\u5e38\u7ed3\u675f\uff0c\u672a\u5b8c\u6210\u90e8\u5206\u4e0d\u5224\u5b9a\u4e3a\u5bc6\u7801\u9519\u8bef\u3002'),
}


def apply_password_label(item, point):
    label = LABELS.get(getattr(point, 'password_state', ''))
    if label:
        item.setText(label[0])
        item.setToolTip(label[1] + ('\n' + point.password_note if point.password_note else ''))


def follow_active_target(table):
    if table._row_activity != 'password_verify' or len(table._capturing) != 1:
        return
    target = next(iter(table._capturing))
    for row in range(table.rowCount()):
        item = table.item(row, table.STATUS_COLUMN)
        if item is not None and str(item.data(Qt.UserRole)).upper() == target:
            with QSignalBlocker(table):
                table.setCurrentCell(row, table.SSID_COLUMN, QItemSelectionModel.NoUpdate)
            table.scrollToItem(table.item(row, table.SSID_COLUMN), QAbstractItemView.PositionAtCenter)
            return


def apply_password_color(item, point, table):
    state = getattr(point, 'password_state', '')
    if not state:
        return False
    color = password_state_color(state, table._light_theme)
    if color is None:
        item.setData(Qt.ForegroundRole, None)
    else:
        item.setForeground(color)
    return True


def password_state_color(state, light):
    if state == 'found':
        return QColor('#0a5c2e' if light else '#7ee2a8')
    if state in {'failed', 'timeout'}:
        return QColor('#b52d2d' if light else '#ef7777')
    return None
