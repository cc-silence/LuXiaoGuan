"""Bounded, independently closable notifications using the shared dialog theme."""
from collections import deque
from dataclasses import dataclass
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextEdit
from .common_dialogs import MessageDialog


class ModelessMessageDialog(MessageDialog):
    def __init__(self, title, message, parent):
        super().__init__(title, str(message) + "\n", parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.message_box = self.findChild(QTextEdit, "settingsMessageText")
        self.message_box.setUndoRedoEnabled(False)


@dataclass
class _NoticeWindow:
    dialog: ModelessMessageDialog
    messages: deque
    total: int = 1
    title: str = ""


class ModelessMessageGroup(QObject):
    """Never exec/open or queue acknowledgments; overflow merges into one window.

    Only the most recent bounded messages remain in memory. Callers must save
    durable results separately. Closing a window clears its text and registry.
    """
    def __init__(self, owner, *, max_windows=8, max_messages=64, incremental=False):
        super().__init__(owner)
        self.owner = owner
        self.max_windows = max(1, int(max_windows))
        self.max_messages = max(1, int(max_messages))
        self.incremental = incremental
        self._windows = {}

    def show_message(self, title, message):
        message = str(message).replace("\r\n", "\n").replace("\r", "\n")
        if len(self._windows) >= self.max_windows:
            record = next(reversed(self._windows.values()))
            if self.incremental:
                self._append(record, message)
                record.dialog.show()
                record.dialog.raise_()
                return record.dialog
            record.total += 1
            record.messages.append(str(message))
            hint = "\u540e\u7eed\u7ed3\u679c\u5df2\u5408\u5e76\u663e\u793a\uff08\u5171 {0} \u6761\uff0c\u4fdd\u7559\u6700\u8fd1 {1} \u6761\uff09\u3002\n\u5b8c\u6574\u7ed3\u679c\u5df2\u4fdd\u5b58\u5230\u7ed3\u679c\u76ee\u5f55\u3002"
            record.dialog.message_box.setPlainText(hint.format(record.total, self.max_messages) + "\n\n" + "\n\n".join(record.messages))
            cursor = record.dialog.message_box.textCursor()
            cursor.movePosition(QTextCursor.End)
            record.dialog.message_box.setTextCursor(cursor)
            record.dialog.show()
            record.dialog.raise_()
            return record.dialog
        dialog = ModelessMessageDialog(title, message, self.owner)
        key = id(dialog)
        self._windows[key] = _NoticeWindow(dialog, deque((str(message),), maxlen=self.max_messages), title=title)
        dialog.finished.connect(lambda _result, token=key: self._release(token))
        dialog.destroyed.connect(lambda _obj=None, token=key: self._release(token, destroyed=True))
        dialog.show()
        offset = (len(self._windows) - 1) * 24
        area = dialog.screen().availableGeometry()
        dialog.move(max(area.left(), min(dialog.x() + offset, area.right() - dialog.width() + 1)),
                    max(area.top(), min(dialog.y() + offset, area.bottom() - dialog.height() + 1)))
        return dialog

    def _append(self, record, message):
        box = record.dialog.message_box
        follow = (box.verticalScrollBar().value() >= box.verticalScrollBar().maximum()
                  and not box.textCursor().hasSelection())
        evicted = record.messages[0] if len(record.messages) == self.max_messages else None
        record.messages.append(message)
        record.total += 1
        cursor = QTextCursor(box.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertText("\n" + message + "\n")
        if evicted is not None:
            start = QTextCursor(box.document())
            start.movePosition(QTextCursor.Start)
            start.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor, evicted.count("\n") + 2)
            start.removeSelectedText()
        if follow:
            box.setTextCursor(cursor)
            box.ensureCursorVisible()
        record.dialog.title_label.setText(record.title + f" ({record.total})")

    def _release(self, key, *, destroyed=False):
        record = self._windows.pop(key, None)
        if record is not None:
            record.messages.clear()
            if not destroyed:
                record.dialog.message_box.clear()

    def close_all(self):
        for record in tuple(self._windows.values()):
            record.dialog.close()
