"""Run with QT_QPA_PLATFORM=windows to exercise real layered presentation."""
import ctypes
import unittest
from ctypes import wintypes
from tests import test_standalone as standalone
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import Qt


@unittest.skipUnless(QGuiApplication.platformName() == "windows", "Windows display backend required")
class NativeWindowVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window = standalone.StandaloneTests.window
        # Keep only the test window above other apps during screen hit-testing.
        cls.window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        cls.window.showNormal()
        standalone.APP.processEvents()

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def assert_presented(self):
        w = self.window
        w.raise_()
        w.activateWindow()
        standalone.APP.processEvents()
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        api.GetClientRect.argtypes = api.GetWindowRect.argtypes
        api.WindowFromPoint.argtypes = (wintypes.POINT,)
        api.WindowFromPoint.restype = wintypes.HWND
        api.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        api.GetAncestor.restype = wintypes.HWND
        hwnd = int(w.winId())
        outer, client = wintypes.RECT(), wintypes.RECT()
        self.assertTrue(api.GetWindowRect(hwnd, ctypes.byref(outer)))
        self.assertTrue(api.GetClientRect(hwnd, ctypes.byref(client)))
        self.assertEqual((outer.right-outer.left, outer.bottom-outer.top),
                         (client.right-client.left, client.bottom-client.top))
        point = wintypes.POINT((outer.left+outer.right)//2, (outer.top+outer.bottom)//2)
        self.assertEqual(api.GetAncestor(api.WindowFromPoint(point), 2), hwnd,
                         "The screen must contain this window, not the window behind it")

    def test_actual_presentation_survives_theme_and_restore(self):
        for theme in ("dark.qss", "light.qss"):
            self.window.apply_appearance(100, "default", theme)
            self.window.showNormal()
            self.assert_presented()
            self.window.showMaximized()
            standalone.APP.processEvents()
            self.window.showNormal()
            self.assert_presented()
            self.window.showMinimized()
            standalone.APP.processEvents()
            self.window.showNormal()
            self.assert_presented()
            self.window.hide()
            self.window.show()
            self.assert_presented()


if __name__ == "__main__":
    unittest.main()
