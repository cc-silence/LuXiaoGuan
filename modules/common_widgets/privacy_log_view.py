"""Bounded raw log storage with a reversible, incremental privacy projection."""
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import QPlainTextEdit
from modules.display_privacy import controller_for, MASKED_TEXT


class PrivacyLogView(QPlainTextEdit):
    MAX_RAW_CHARACTERS = 200000

    def __init__(self, parent=None, *, projector=None, context_blocks=1):
        super().__init__(parent)
        self.setUndoRedoEnabled(False)
        self.setReadOnly(True)
        self._raw = QTextDocument(self)
        self._raw.setUndoRedoEnabled(False)
        self._projector = projector
        self._context_blocks = max(1, int(context_blocks))
        self._leading_context_lost = False
        self.setMaximumBlockCount(1500)
        self._privacy = controller_for(self)
        if self._privacy is not None:
            self._privacy.changed.connect(self._refresh_privacy)

    def setMaximumBlockCount(self, count):
        super().setMaximumBlockCount(count)
        self._raw.setMaximumBlockCount(count + self._context_blocks - 1 if count > 0 else 0)

    def raw_text(self):
        return self._raw.toRawText().replace("\u2029", "\n")

    def _project(self, text, *, partial=False, leading_context=False):
        if self._privacy is None or not self._privacy.enabled:
            return text
        masker = self._privacy.snapshot()
        result = self._projector(text, masker) if self._projector else masker.text(text)
        if leading_context and self._leading_context_lost:
            # A character-budget cut can discard the start of a private record.
            rows = result.split("\n")
            for index in range(min(self._context_blocks - 1, len(rows))):
                if rows[index]:
                    rows[index] = MASKED_TEXT
            result = "\n".join(rows)
        if partial and text and not text.endswith("\n"):
            # A split SSID/MAC must not flash in plaintext before its row completes.
            result = result[:result.rfind("\n") + 1] + MASKED_TEXT
        return result

    def _trim_raw(self):
        extra = self._raw.characterCount() - 1 - self.MAX_RAW_CHARACTERS
        if extra <= 0:
            return False
        cursor = QTextCursor(self._raw)
        cursor.setPosition(0)
        cursor.setPosition(extra, QTextCursor.KeepAnchor)
        # Discard the entire truncated row: preserve Unicode and field boundaries.
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        if not cursor.atEnd():
            cursor.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        self._leading_context_lost = not self._raw.isEmpty()
        return True

    def setPlainText(self, text):
        self._leading_context_lost = False
        self._raw.setPlainText(str(text))
        self._trim_raw()
        self._refresh_privacy()

    def clear(self):
        self._leading_context_lost = False
        self._raw.clear()
        super().clear()

    def insert_raw_text(self, text):
        text = str(text)
        if not text:
            return
        private = self._privacy is not None and self._privacy.enabled
        pattern = self._privacy.snapshot().pattern if private else None
        multiline_identity = pattern is not None and any(char in pattern.pattern for char in ("\n", "\r", "\u2029"))
        blocks = self._context_blocks if private else 1
        block = self._raw.lastBlock()
        used_blocks = 1
        while used_blocks < blocks and block.previous().isValid():
            block = block.previous()
            used_blocks += 1
        tail_cursor = QTextCursor(self._raw)
        tail_cursor.setPosition(block.position())
        tail_cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        tail = tail_cursor.selectedText().replace("\u2029", "\n")
        raw_cursor = QTextCursor(self._raw)
        raw_cursor.movePosition(QTextCursor.End)
        raw_cursor.insertText(text)
        trimmed = self._trim_raw()
        if trimmed or multiline_identity:
            self._refresh_privacy()
            return
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        for _ in range(used_blocks - 1):
            cursor.movePosition(QTextCursor.PreviousBlock, QTextCursor.KeepAnchor)
        cursor.insertText(self._project(tail + text, partial=True, leading_context=block.blockNumber() == 0))
        expected_blocks = self._raw.blockCount()
        if self.maximumBlockCount() > 0:
            expected_blocks = min(expected_blocks, self.maximumBlockCount())
        if self.document().blockCount() != expected_blocks:
            self._refresh_privacy()

    def _refresh_privacy(self):
        bar = self.verticalScrollBar()
        position, at_end = bar.value(), bar.value() == bar.maximum()
        super().setPlainText(self._project(self.raw_text(), partial=True, leading_context=True))
        bar.setValue(bar.maximum() if at_end else position)
