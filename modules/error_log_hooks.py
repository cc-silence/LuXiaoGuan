"""进程级错误捕获，不依赖页面生命周期。"""
import atexit
import faulthandler
import logging
import sys
import threading
from modules.error_logs import ErrorFileHandler, ErrorLogStore, record_message

_installed = None


class ErrorLogHooks:
    """安装和回收 Python/Qt 错误入口及原生崩溃文件句柄。"""
    def __init__(self, store):
        self.store = store
        self.handler = ErrorFileHandler(store)
        self.previous_sys = sys.excepthook
        self.previous_thread = threading.excepthook
        self.previous_unraisable = sys.unraisablehook
        self.previous_qt = None
        self.qt_installed = False
        self.crash_stream = None
        self.owns_faulthandler = False
        self.closed = False
        logging.getLogger().addHandler(self.handler)
        sys.excepthook = self.exception
        threading.excepthook = self.thread_exception
        sys.unraisablehook = self.unraisable
        try:
            store.crash_path = store.new_path("crash")
            self.crash_stream = store.crash_path.open("xb", buffering=0)
            faulthandler.enable(file=self.crash_stream, all_threads=True)
            self.owns_faulthandler = True
        except (OSError, RuntimeError) as exc:
            if self.crash_stream is not None:
                self.crash_stream.close()
                self.crash_stream = None
            store.last_error = str(exc)
            record_message("Cannot enable native crash diagnostics: " + str(exc), source="diagnostics")

    def exception(self, kind, value, traceback):
        """保存未处理异常栈，不收集局部变量。"""
        if not issubclass(kind, (SystemExit, KeyboardInterrupt)):
            logging.getLogger("routermanager.exception").critical("Unhandled exception", exc_info=(kind, value, traceback))
        self.previous_sys(kind, value, traceback)

    def thread_exception(self, args):
        """捕获普通后台线程的未处理异常。"""
        if not issubclass(args.exc_type, (SystemExit, KeyboardInterrupt)):
            logging.getLogger("routermanager.thread").critical("Unhandled thread exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        self.previous_thread(args)

    def unraisable(self, args):
        """捕获析构等无法正常抛出的异常，不记对象内容。"""
        logging.getLogger("routermanager.unraisable").error("Unraisable exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        self.previous_unraisable(args)

    def install_qt(self):
        """Qt 加载后接入严重错误，原有处理器仍会被调用。"""
        if not self.qt_installed:
            from PySide6.QtCore import qInstallMessageHandler
            self.previous_qt = qInstallMessageHandler(self.qt_message)
            self.qt_installed = True

    def qt_message(self, kind, context, text):
        from PySide6.QtCore import QtMsgType
        if kind in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            record_message(text, source="qt")
        if self.previous_qt is not None:
            self.previous_qt(kind, context, text)
        elif sys.__stderr__ is not None:
            sys.__stderr__.write(str(text) + "\n")

    def close(self):
        """退出时释放句柄，正常运行不留空崩溃日志。"""
        if self.closed:
            return
        self.closed = True
        if sys.excepthook == self.exception:
            sys.excepthook = self.previous_sys
        if threading.excepthook == self.thread_exception:
            threading.excepthook = self.previous_thread
        if sys.unraisablehook == self.unraisable:
            sys.unraisablehook = self.previous_unraisable
        if self.qt_installed:
            from PySide6.QtCore import qInstallMessageHandler
            qInstallMessageHandler(self.previous_qt)
        if self.owns_faulthandler:
            faulthandler.disable()
        if self.crash_stream is not None:
            self.crash_stream.close()
            try:
                if self.store.crash_path.stat().st_size == 0:
                    self.store.crash_path.unlink()
            except OSError:
                pass
        logging.getLogger().removeHandler(self.handler)
        self.handler.close()


def install_error_logging(version="", directory=None):
    """在 Qt/页面导入前安装一次，目录不可写时不阻断启动。"""
    global _installed
    if _installed is None or _installed.closed:
        try:
            _installed = ErrorLogHooks(ErrorLogStore(directory, version))
        except OSError as exc:
            if sys.__stderr__ is not None:
                sys.__stderr__.write("Cannot initialize LuXiaoGuan error logs: " + str(exc) + "\n")
            return None
        atexit.register(_installed.close)
    return _installed


def current_error_logs():
    """取得已启用的日志库，供后台导出任务读取。"""
    return _installed.store if _installed is not None and not _installed.closed else ErrorLogStore()
