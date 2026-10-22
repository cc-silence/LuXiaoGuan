"""Native cursor hit-testing and side drags must reach the outermost window pixel."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent, QRect
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from modules.common_widgets.frameless_dialog import FramelessDialog

APP=QApplication.instance() or QApplication([])


class ResizeEdgeTests(unittest.TestCase):
    def setUp(self):
        self.window=FramelessDialog()
        self.window.setMinimumSize(300,200)
        self.window.resize(800,600)
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def test_every_pixel_of_each_side_strip_has_resize_cursor(self):
        w=self.window
        for size in ((800,600),(1000,700)):
            w.resize(*size);APP.processEvents()
            for depth in range(w.grip_size):
                for point,cursor in (
                    (QPoint(w.width()-1-depth,w.height()//2),Qt.SizeHorCursor),
                    (QPoint(depth,w.height()//2),Qt.SizeHorCursor),
                    (QPoint(w.width()//2,depth),Qt.SizeVerCursor),
                    (QPoint(w.width()//2,w.height()-1-depth),Qt.SizeVerCursor),
                    (QPoint(w.width()-1-depth,w.height()-w.grip_size-1),Qt.SizeHorCursor)):
                    target=w.childAt(point)
                    self.assertIsNotNone(target)
                    self.assertEqual(target.cursor().shape(),cursor,(point,target.objectName()))

    def test_outermost_pixels_really_resize_and_keep_opposite_edge(self):
        w=self.window
        for side,delta in (("right",QPoint(20,0)),("left",QPoint(-20,0)),
                           ("top",QPoint(0,-20)),("bottom",QPoint(0,20))):
            w.setGeometry(100,100,800,600);APP.processEvents()
            point={"right":QPoint(w.width()-1,w.height()//2),"left":QPoint(0,w.height()//2),
                   "top":QPoint(w.width()//2,0),"bottom":QPoint(w.width()//2,w.height()-1)}[side]
            target=w.childAt(point)
            local=target.mapFrom(w,point)
            start=w.geometry()
            global_pos=target.mapToGlobal(local)
            QTest.mousePress(target,Qt.LeftButton,Qt.NoModifier,local)
            event=QMouseEvent(QEvent.MouseMove,QPointF(local+delta),QPointF(global_pos+delta),
                              Qt.NoButton,Qt.LeftButton,Qt.NoModifier)
            APP.sendEvent(target,event)
            QTest.mouseRelease(target,Qt.LeftButton,Qt.NoModifier,local+delta)
            actual=w.geometry()
            if side in ("left","right"):
                self.assertEqual(actual.width(),start.width()+20,side)
                self.assertEqual(actual.right() if side=="left" else actual.left(),
                                 start.right() if side=="left" else start.left())
            else:
                self.assertEqual(actual.height(),start.height()+20,side)
                self.assertEqual(actual.bottom() if side=="top" else actual.top(),
                                 start.bottom() if side=="top" else start.top())

class MainWindowResizeTests(ResizeEdgeTests):
    @classmethod
    def setUpClass(cls):
        from tests import test_standalone as standalone
        cls.standalone = standalone
        standalone.StandaloneTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.standalone.StandaloneTests.tearDownClass()

    def setUp(self):
        self.window = self.standalone.StandaloneTests.window
        area_patch = patch.object(self.window.geometry_policy, "area", return_value=QRect(0, 0, 3840, 2160))
        area_patch.start()
        self.addCleanup(area_patch.stop)
        self.window.showNormal()
        self.window.apply_appearance(100, "default", "dark.qss")
        self.window.showNormal()
        APP.processEvents()

    def tearDown(self):
        pass

    def test_both_themes_and_scales_keep_outer_pixel_and_maximized_hides_grips(self):
        w = self.window
        with patch.object(w.geometry_policy, "area", return_value=QRect(0, 0, 3840, 2160)):
            for theme in ("light.qss", "dark.qss"):
                for scale in (100, 150):
                    w.showNormal();APP.processEvents()
                    w.apply_appearance(scale, "default", theme)
                    w.resize(1600, 1000)
                    APP.processEvents()
                    self.assertFalse(w.isMaximized())
                    target = w.childAt(QPoint(w.width()-1, w.height()//2))
                    self.assertEqual(target.cursor().shape(), Qt.SizeHorCursor)
                    self.assertTrue(w.right_grip.isAncestorOf(target) or target is w.right_grip)
                    w.showMaximized();APP.processEvents()
                    self.assertTrue(all(grip.isHidden() for grip in w._all_grips()))
                    w.showNormal();APP.processEvents()
                    self.assertTrue(all(grip.isVisible() for grip in w._all_grips()))
