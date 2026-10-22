"""Windows shell styles and system menu for a custom Qt title bar."""
import ctypes
import sys
from ctypes import wintypes

# Qt owns the frameless client geometry. WS_THICKFRAME adds native margins
# behind its back and prevents layered-window presentation on Windows.
# Resize is already handled by the shared edge/corner grips.
_REQUIRED_STYLE = 0x00020000 | 0x00010000 | 0x00080000
_GWL_STYLE = -16


def _user32():
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    api.GetWindowLongW.restype = wintypes.LONG
    api.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
    api.SetWindowLongW.restype = wintypes.LONG
    api.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
    api.SetWindowPos.restype = wintypes.BOOL
    return api


def enable_window_commands(window):
    """Retain the custom frame and restore Windows resize/min/max/menu capability."""
    from PySide6.QtGui import QGuiApplication
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return False
    hwnd = int(window.winId())
    api = _user32()
    style = api.GetWindowLongW(hwnd, _GWL_STYLE)
    if style & _REQUIRED_STYLE == _REQUIRED_STYLE:
        return True
    ctypes.set_last_error(0)
    if not api.SetWindowLongW(hwnd, _GWL_STYLE, style | _REQUIRED_STYLE):
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
    if not api.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027):
        raise ctypes.WinError(ctypes.get_last_error())
    return True


def show_system_menu(window):
    """Use the actual OS menu, including its native keyboard accelerators."""
    if not enable_window_commands(window):
        return
    api = _user32()
    api.GetSystemMenu.argtypes = (wintypes.HWND, wintypes.BOOL)
    api.GetSystemMenu.restype = wintypes.HMENU
    api.TrackPopupMenu.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p)
    api.TrackPopupMenu.restype = wintypes.UINT
    api.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    api.PostMessageW.restype = wintypes.BOOL
    hwnd = int(window.winId())
    menu = api.GetSystemMenu(hwnd, False)
    api.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    api.ClientToScreen.restype = wintypes.BOOL
    point = wintypes.POINT(0, round(window.title_bar.height() * window.devicePixelRatioF()))
    if not api.ClientToScreen(hwnd, ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    x, y = point.x, point.y
    command = api.TrackPopupMenu(menu, 0x0100 | 0x0002, x, y, 0, hwnd, None)
    if command:
        api.PostMessageW(hwnd, 0x0112, command, 0)
