"""\u51bb\u7ed3\u5305\u7ec8\u7aef\u81ea\u68c0\uff1a\u4e0d\u8fde\u63a5\u8def\u7531\u5668\uff0c\u4e0d\u542f\u52a8\u4efb\u4f55\u547d\u4ee4\u3002"""
import base64
import json
from PySide6.QtCore import QObject,Signal,Qt,QEvent,QCoreApplication
from PySide6.QtGui import QKeyEvent
from .native_view import NativeTerminalView


def run_terminal_smoke(app):
    """\u5b9e\u9645\u52a0\u8f7d\u539f\u751f DLL \u5e76\u9a8c\u8bc1\u8f93\u51fa\u3001\u5907\u7528\u5c4f\u5e55\u548c\u8f93\u5165\u3002"""
    class Bridge(QObject):
        output=Signal(str)
        control=Signal(str)
        def __init__(self):super().__init__();self.sent=[];self.acknowledged=0
        def input(self,value):self.sent.append(value)
        def resize(self,*value):pass
        def ack(self,value):self.acknowledged+=value
        def selectionChanged(self,value):pass
        def copy(self,value):pass
    bridge=Bridge();view=NativeTerminalView(bridge)
    try:
        bridge.control.emit(json.dumps({'action':'state','connected':True}))
        data='\u8def\u7531\u5668'.encode('utf-8')
        for chunk in (data[:1],data[1:5],data[5:]):bridge.output.emit(base64.b64encode(chunk).decode('ascii'))
        assert view.terminal_text().strip()=='\u8def\u7531\u5668'
        assert bridge.acknowledged==len(data)
        data=b'\x1b[?1049h\x1b[2J\x1b[Hmenu\x1b[?1049l'
        bridge.output.emit(base64.b64encode(data).decode('ascii'))
        assert view.terminal_text().strip()=='\u8def\u7531\u5668'
        app.sendEvent(view.terminal,QKeyEvent(QEvent.KeyPress,Qt.Key_Delete,Qt.NoModifier))
        assert bridge.sent==['\x1b[3~']
        assert 'PySide6.QtWebEngineWidgets' not in __import__('sys').modules
    finally:
        view.dispose();view.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    return True
