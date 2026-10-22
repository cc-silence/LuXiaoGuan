"""Window-owned display privacy. Raw task data never changes."""
import re
from pathlib import PureWindowsPath
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QLabel, QProgressBar
MASKED_TEXT = "\u2022" * 6


_MAC = re.compile(
    r"(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])"
    r"|(?<![0-9a-f])[0-9a-f]{4}[.][0-9a-f]{4}[.][0-9a-f]{4}(?![0-9a-f])"
    r"|(?<![0-9a-z])[0-9a-f]{12}(?![0-9a-z])", re.IGNORECASE)


def controller_for(widget):
    while widget is not None:
        controller = getattr(widget, "display_privacy", None)
        if controller is not None:
            return controller
        widget = widget.parent() if hasattr(widget, "parent") else None
    return None



from dataclasses import dataclass

@dataclass(frozen=True)
class Masker:
    pattern: object = None
    overflow: bool = False
    mask_mac: bool = True

    def text(self, value):
        text = str(value or "")
        if not text:
            return text
        if self.overflow:
            return MASKED_TEXT
        # Match once against original text, so replacements cannot be replaced again.
        spans = [match.span() for match in _MAC.finditer(text)] if self.mask_mac else []
        if self.pattern is not None:
            spans.extend(match.span() for match in self.pattern.finditer(text))
        if not spans:
            return text
        merged = []
        for start, end in sorted(spans):
            if merged and start < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        parts, previous = [], 0
        for start, end in merged:
            parts.extend((text[previous:start], MASKED_TEXT))
            previous = end
        parts.append(text[previous:])
        return "".join(parts)



def masker_for(values, *, mask_mac=True):
    values = sorted({str(v) for v in values if v}, key=lambda v: (-len(v), v))
    return Masker(re.compile('|'.join(re.escape(v) for v in values)) if values else None,
                  mask_mac=mask_mac)

class DisplayPrivacy(QObject):
    """\u5e38\u9a7b\u9690\u79c1\u6295\u5f71\uff0c\u4e0d\u521b\u5efa\u4e1a\u52a1\u670d\u52a1\u3002"""
    changed = Signal()
    toggled = Signal(bool)
    MAX_IDENTITIES = 50000
    MAX_IDENTITY_BYTES = 8 * 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = False
        self._values = set()
        self._bytes = 0
        self._pattern = None
        self._dirty = False
        self._overflow = False

    def register_snapshot(self, snapshot):
        """\u63a5\u6536\u73b0\u6709\u5feb\u7167\u7684\u6807\u8bc6\u548c\u4ea7\u7269\u540d\uff0c\u65e0\u78c1\u76d8\u6216\u7f51\u7edc\u64cd\u4f5c\u3002"""
        values = []
        for entry in getattr(snapshot, 'access_points', ()):
            values.extend((entry.ssid, entry.bssid, entry.ssid.encode('utf-8').hex() if entry.ssid else ''))
        for entry in getattr(getattr(snapshot, 'inventory', None), 'rows', ()):
            values.extend((getattr(entry, 'ssid', ''), getattr(entry, 'runtime_ssid', ''), getattr(entry, 'bssid', '')))
        for entry in getattr(snapshot, 'artifacts', ()):
            values.extend((getattr(entry, 'ssid', ''), getattr(entry, 'bssid', '')))
            path = getattr(entry, 'local_path', '') or getattr(entry, 'path', '')
            if path:
                values.append(PureWindowsPath(str(path)).name)
        self.register_values(values)

    def snapshot(self, *, mask_mac=True):
        """\u51bb\u7ed3\u7eaf\u6570\u636e\u8131\u654f\u5668\uff0c\u540e\u53f0\u4e0d\u8bbf\u95ee QObject\u3002"""
        if self._overflow:
            return Masker(overflow=True, mask_mac=mask_mac)
        if self._dirty:
            values = sorted(self._values, key=lambda value: (-len(value), value))
            self._pattern = re.compile('|'.join(re.escape(v) for v in values)) if values else None
            self._dirty = False
        return Masker(self._pattern, self._overflow, mask_mac)

    def register_values(self, values):
        """\u767b\u8bb0\u6709\u754c\u6807\u8bc6\u96c6\u5408\uff0c\u8d85\u9650\u65f6\u4fdd\u5b88\u9690\u85cf\u6574\u6bb5\u3002"""
        if self._overflow:
            return
        added = False
        for value in values:
            value = str(value or "")
            if not value or value == "__hidden__" or value in self._values:
                continue
            if len(self._values) >= self.MAX_IDENTITIES or self._bytes + len(value.encode('utf-8')) > self.MAX_IDENTITY_BYTES:
                self._overflow = True
                self._values.clear()
                self._bytes = 0
                self._pattern = None
                added = True
                break
            self._values.add(value)
            self._bytes += len(value.encode('utf-8'))
            added = True
        if added:
            self._dirty = True
            if self.enabled:
                self.changed.emit()

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if self.enabled == enabled:
            return
        self.enabled = enabled
        self.toggled.emit(enabled)
        self.changed.emit()

    def text(self, value):
        text = str(value or "")
        if not self.enabled or not text:
            return text
        return self.masked_text(value)

    def masked_text(self, value):
        return self.snapshot().text(value)


class PrivacyLabel(QLabel):
    """\u4fdd\u7559\u6587\u672c\u548c\u60ac\u505c\u539f\u6587\uff0c\u4ec5\u66f4\u65b0\u663e\u793a\u6295\u5f71\u3002"""
    def __init__(self, *args, **kwargs):
        self._privacy = None
        self._raw_text = ""
        self._raw_tooltip = ""
        super().__init__(*args, **kwargs)
        self._raw_text = self.text()
        self._local_masker = None
        self._privacy = controller_for(self)
        if self._privacy is not None:
            self._privacy.changed.connect(self._refresh_privacy)
            self._refresh_privacy()

    def clear(self):
        self.setText("")

    def setText(self, text):
        self._raw_text = str(text)
        QLabel.setText(self, self._project(text))

    def setToolTip(self, text):
        self._raw_tooltip = str(text)
        QLabel.setToolTip(self, self._project(text))

    def _project(self, text):
        if self._privacy and self._privacy.enabled:
            return self._privacy.text(text)
        return self._local_masker.text(text) if self._local_masker else str(text)

    def set_local_masker(self, masker):
        if masker == self._local_masker:
            return
        self._local_masker = masker
        if not (self._privacy and self._privacy.enabled):
            self._refresh_privacy()

    def _refresh_privacy(self):
        QLabel.setText(self, self._project(self._raw_text))
        QLabel.setToolTip(self, self._project(self._raw_tooltip))


from modules.common_widgets.rounded_progress_bar import RoundedProgressBar


class PrivacyProgressBar(RoundedProgressBar):
    """\u8fdb\u5ea6\u60ac\u505c\u4f7f\u7528\u4e0e\u6807\u9898\u4e00\u81f4\u7684\u53ef\u6062\u590d\u6295\u5f71\u3002"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._local_masker = None
        self._privacy = controller_for(self)
        self._raw_tooltip = ""
        if self._privacy is not None:
            self._privacy.changed.connect(self._refresh_privacy)

    def setToolTip(self, text):
        self._raw_tooltip = str(text)
        self._refresh_privacy()

    def _project(self, text):
        if self._privacy and self._privacy.enabled:
            return self._privacy.text(text)
        return self._local_masker.text(text) if self._local_masker else str(text)

    def set_local_masker(self, masker):
        if masker == self._local_masker:
            return
        self._local_masker = masker
        if not (self._privacy and self._privacy.enabled):
            self._refresh_privacy()

    def _refresh_privacy(self):
        QProgressBar.setToolTip(self, self._project(self._raw_tooltip))
