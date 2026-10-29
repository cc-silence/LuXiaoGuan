"""\u539f\u751f Qt \u7ec8\u7aef\u663e\u793a\u9002\u914d\uff0c\u4e0d\u521b\u5efa\u672c\u5730\u8fdb\u7a0b\u6216\u8fdc\u7a0b\u8fde\u63a5\u3002"""
import base64
import ctypes
import json
from pathlib import Path
from PySide6.QtCore import QEvent, Qt, Signal, QUrl, QRectF, qVersion
from PySide6.QtGui import QPainterPath, QRegion, QPalette
from PySide6.QtWidgets import QWidget, QVBoxLayout, QApplication
from shiboken6 import getCppPointer, wrapInstance

_WRITE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int)
_RESIZE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int)


class TerminalVersionError(OSError):
    def __init__(self, compiled, runtime):
        self.compiled_version = compiled
        self.runtime_version = runtime
        super().__init__(f'Native terminal Qt version mismatch: compiled={compiled}, runtime={runtime}')


class NativeTerminalView(QWidget):
    """\u4fdd\u6301\u539f\u6709 bridge \u5951\u7ea6\uff0c\u7edf\u4e00\u8f93\u5165\u3001\u7c98\u8d34\u786e\u8ba4\u548c\u8d44\u6e90\u91ca\u653e\u3002"""
    _selectionRefresh = Signal()

    def __init__(self, bridge, parent=None, library_path=None):
        if library_path is None:
            from modules.config_manager import BASE_DIR
            library_path = BASE_DIR / 'bin/router_terminal/winbox_terminal.dll'
        # Validate before creating a parent-owned widget so failed retries leave no hidden children.
        library = self._load_library(Path(library_path))
        super().__init__(parent)
        self.setObjectName("routerTerminalContainer")
        self.ensurePolished()
        self._dll = library
        self._bridge = bridge
        self._connected = False
        self._disposed = False
        self._press_point = None
        self._pending_link = ''
        self._size = (80, 24)
        self._font_size = 14
        self._write_callback = _WRITE(self._write)
        self._resize_callback = _RESIZE(self._resize_terminal)
        self._pointer = self._dll.wb_create(getCppPointer(self)[0], self._write_callback, self._resize_callback)
        if not self._pointer:
            raise OSError('Native terminal creation failed')
        self.terminal = wrapInstance(self._pointer, QWidget)
        self.terminal.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.terminal.installEventFilter(self)
        self.terminal.setCursor(Qt.IBeamCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.terminal)
        self._selectionRefresh.connect(self._refresh_selection, Qt.QueuedConnection)
        bridge.output.connect(self.receive_output)
        bridge.control.connect(self.apply_control)
        self._dll.wb_colors(self._pointer, self.palette().color(QPalette.WindowText).rgba(), self.palette().color(QPalette.Window).rgba())
        self._dll.wb_font(self._pointer, self._font_size)

    @staticmethod
    def _load_library(path):
        """\u4f7f\u7528\u56fa\u5b9a\u8def\u5f84\u548c\u663e\u5f0f ABI\uff0c\u7981\u6b62\u52a0\u8f7d\u4e0d\u5339\u914d\u7684 Qt \u5e93\u3002"""
        if not path.is_file():
            raise FileNotFoundError(str(path))
        dll = ctypes.CDLL(str(path.resolve()))
        specs = {
            'wb_qt_version': ([], ctypes.c_char_p),
            'wb_create': ([ctypes.c_void_p, _WRITE, _RESIZE], ctypes.c_void_p),
            'wb_feed': ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int], None),
            'wb_read': ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
            'wb_reset': ([ctypes.c_void_p], None),
            'wb_selected': ([ctypes.c_void_p], ctypes.c_int),
            'wb_clear_selection': ([ctypes.c_void_p], None),
            'wb_paste_text': ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int], None),
            'wb_colors': ([ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint], None),
            'wb_font': ([ctypes.c_void_p, ctypes.c_int], None),
            'wb_find': ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_int),
            'wb_link': ([ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
            'wb_select_all': ([ctypes.c_void_p], None),
            'wb_clear': ([ctypes.c_void_p], None),
            'wb_detach': ([ctypes.c_void_p], None),
        }
        for name, (arguments, result) in specs.items():
            function = getattr(dll, name)
            function.argtypes, function.restype = arguments, result
        compiled = dll.wb_qt_version().decode('ascii')
        if compiled != qVersion():
            raise TerminalVersionError(compiled, qVersion())
        return dll

    def _write(self, pointer, size):
        if not self._disposed and self._connected:
            self._bridge.input(ctypes.string_at(pointer, size).decode('utf-8'))

    def _resize_terminal(self, columns, rows):
        self._size = (columns, rows)
        if not self._disposed:
            self._bridge.resize(columns, rows)

    def receive_output(self, encoded):
        """\u6d88\u8d39\u539f\u59cb\u5b57\u8282\u540e\u786e\u8ba4\u6d41\u91cf\uff0c\u4fdd\u7559\u8fde\u63a5\u670d\u52a1\u80cc\u538b\u3002"""
        if self._disposed:
            return
        data = base64.b64decode(encoded, validate=True)
        self._dll.wb_feed(self._pointer, data, len(data))
        self._bridge.ack(len(data))

    def terminal_text(self, selected=False):
        if self._disposed:
            return ''
        size = self._dll.wb_read(self._pointer, int(selected), None, 0)
        if size > 32 * 1024 * 1024:
            raise ValueError('Terminal text exceeds clipboard budget')
        buffer = ctypes.create_string_buffer(size + 1)
        self._dll.wb_read(self._pointer, int(selected), buffer, len(buffer))
        return buffer.value.decode('utf-8')

    def _link_at(self, point):
        buffer = ctypes.create_string_buffer(8193)
        size = self._dll.wb_link(self._pointer, point.x(), point.y(), buffer, len(buffer))
        return buffer.value.decode('utf-8') if 0 < size < len(buffer) else ''

    def _refresh_selection(self):
        if self._disposed:
            return
        if self._pending_link:
            link, self._pending_link = self._pending_link, ''
            self._dll.wb_clear_selection(self._pointer)
            self._bridge.selectionChanged(False)
            self._bridge.openUrl(link)
            return
        text = self.terminal_text(selected=True)
        self._bridge.selectionChanged(bool(text))
        if text:
            self._bridge.copy(text)

    def apply_control(self, encoded):
        if self._disposed:
            return
        value = json.loads(encoded)
        action = value.get('action')
        if action == 'state':
            self._connected = bool(value['connected'])
        elif action == 'theme':
            size = int(value['fontSize'])
            if size != self._font_size:
                self._font_size = size
                self._dll.wb_font(self._pointer, size)
        elif action == 'copy':
            text = self.terminal_text(selected=True)
            if text:
                self._bridge.copy(text)
        elif action == 'selectAll':
            self._dll.wb_select_all(self._pointer)
            self._refresh_selection()
        elif action in {'clear', 'reset'}:
            getattr(self._dll, 'wb_clear' if action == 'clear' else 'wb_reset')(self._pointer)
            self._bridge.selectionChanged(False)
        elif action == 'paste' and self._connected:
            data = value['text'].encode('utf-8')
            self._dll.wb_paste_text(self._pointer, data, len(data))
            self.terminal.setFocus()
        elif action == 'find':
            data = value['text'].encode('utf-8')
            self._dll.wb_find(self._pointer, data, len(data))
            self._refresh_selection()
        elif action == 'focus':
            self.terminal.setFocus()
            self._bridge.resize(*self._size)

    def eventFilter(self, watched, event):
        if self._disposed or watched is not self.terminal:
            return super().eventFilter(watched, event)
        kind = event.type()
        if kind == QEvent.KeyPress:
            modifiers = event.modifiers()
            control = bool(modifiers & Qt.ControlModifier)
            shift = bool(modifiers & Qt.ShiftModifier)
            if control and event.key() == Qt.Key_F:
                self._bridge.findRequested(); return True
            if control and event.key() == Qt.Key_C and shift:
                self.apply_control('{"action":"copy"}'); return True
            if control and event.key() == Qt.Key_V:
                self._bridge.requestPaste(); return True
            if not self._connected:
                return True
        elif kind == QEvent.InputMethod:
            if self._connected and event.commitString():
                self._bridge.input(event.commitString())
            event.accept(); return True
        elif kind == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self._bridge.zoom(1 if delta > 0 else -1)
            event.accept(); return True
        elif kind == QEvent.ContextMenu:
            point = event.pos()
            self._bridge.contextMenu(point.x(), point.y(), bool(self.terminal_text(True)), self._link_at(point))
            return True
        elif kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._press_point = event.position().toPoint()
        elif kind == QEvent.MouseMove:
            self.terminal.setCursor(Qt.PointingHandCursor if self._link_at(event.position().toPoint()) else Qt.IBeamCursor)
        elif kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self._press_point is not None and (event.position().toPoint() - self._press_point).manhattanLength() < QApplication.startDragDistance():
                self._pending_link = self._link_at(event.position().toPoint())
            self._press_point = None
            self._selectionRefresh.emit()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 6, 6)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        super().resizeEvent(event)

    def dispose(self):
        """\u5148\u89e3\u9664\u8ba2\u9605\u4e0e\u539f\u751f\u56de\u8c03\uff0c\u518d\u7531 Qt \u7236\u5bf9\u8c61\u56de\u6536\u63a7\u4ef6\u3002"""
        if self._disposed:
            return
        self._disposed = True
        self._bridge.output.disconnect(self.receive_output)
        self._bridge.control.disconnect(self.apply_control)
        self._dll.wb_detach(self._pointer)
        self.terminal.removeEventFilter(self)
