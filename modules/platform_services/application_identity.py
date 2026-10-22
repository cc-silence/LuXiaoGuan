"""\u72ec\u7acb\u5e94\u7528\u7684 Windows \u4efb\u52a1\u680f\u8eab\u4efd\u3002"""
import sys


def set_app_id():
    """\u4ec5\u8bbe\u7f6e\u5f53\u524d\u8fdb\u7a0b\u5206\u7ec4\uff0c\u4e0d\u4fee\u6539\u7cfb\u7edf\u914d\u7f6e\u3002"""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RouterManager.Desktop.1")
