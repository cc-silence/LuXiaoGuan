# ///////////////////////////////////////////////////////////////
#
# BY: WANDERSON M.PIMENTA
# PROJECT MADE WITH: Qt Designer and PySide6
# V: 1.0.0
#
# This project can be used freely for all uses, as long as they maintain the
# respective credits only in the Python scripts, any information in the visual
# interface (GUI) can be modified without any implication.
#
# There are limitations on Qt licenses if you want to use your products
# commercially, I recommend reading them on the official website:
# https://doc.qt.io/qtforpython/licenses.html
#
# ///////////////////////////////////////////////////////////////

from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QWidget, QFrame, QSizeGrip, QHBoxLayout

class CustomGrip(QWidget):
    def __init__(self, parent, position, disable_color = False):

        # SETUP UI
        QWidget.__init__(self)
        self.parent = parent
        self.position = position
        self.setCursor(Qt.SizeHorCursor if position in (Qt.LeftEdge, Qt.RightEdge) else Qt.SizeVerCursor)
        self._resize_start_pos = None
        self._resize_start_geometry = None
        self.setParent(parent)
        self.wi = Widgets()

        # SHOW TOP GRIP
        if position == Qt.TopEdge:
            self.wi.top(self)
            self.setGeometry(0, 0, self.parent.width(), 10)
            self.setMaximumHeight(10)

            # GRIPS
            top_left = QSizeGrip(self.wi.top_left)
            top_right = QSizeGrip(self.wi.top_right)

            self.wi.top.mousePressEvent = self._begin_manual_resize
            self.wi.top.mouseMoveEvent = self._manual_resize
            self.wi.top.mouseReleaseEvent = self._finish_manual_resize

            # ENABLE COLOR
            if disable_color:
                self.wi.top_left.setStyleSheet("background: transparent")
                self.wi.top_right.setStyleSheet("background: transparent")
                self.wi.top.setStyleSheet("background: transparent")

        # SHOW BOTTOM GRIP
        elif position == Qt.BottomEdge:
            self.wi.bottom(self)
            self.setGeometry(0, self.parent.height() - 10, self.parent.width(), 10)
            self.setMaximumHeight(10)

            # GRIPS
            self.bottom_left = QSizeGrip(self.wi.bottom_left)
            self.bottom_right = QSizeGrip(self.wi.bottom_right)

            self.wi.bottom.mousePressEvent = self._begin_manual_resize
            self.wi.bottom.mouseMoveEvent = self._manual_resize
            self.wi.bottom.mouseReleaseEvent = self._finish_manual_resize

            # ENABLE COLOR
            if disable_color:
                self.wi.bottom_left.setStyleSheet("background: transparent")
                self.wi.bottom_right.setStyleSheet("background: transparent")
                self.wi.bottom.setStyleSheet("background: transparent")

        # SHOW LEFT GRIP
        elif position == Qt.LeftEdge:
            self.wi.left(self)
            self.setGeometry(0, 10, 10, self.parent.height())
            self.setMaximumWidth(10)

            self.wi.leftgrip.mousePressEvent = self._begin_manual_resize
            self.wi.leftgrip.mouseMoveEvent = self._manual_resize
            self.wi.leftgrip.mouseReleaseEvent = self._finish_manual_resize

            # ENABLE COLOR
            if disable_color:
                self.wi.leftgrip.setStyleSheet("background: transparent")

        # RESIZE RIGHT
        elif position == Qt.RightEdge:
            self.wi.right(self)
            self.setGeometry(self.parent.width() - 10, 10, 10, self.parent.height())
            self.setMaximumWidth(10)

            self.wi.rightgrip.mousePressEvent = self._begin_manual_resize
            self.wi.rightgrip.mouseMoveEvent = self._manual_resize
            self.wi.rightgrip.mouseReleaseEvent = self._finish_manual_resize

            # ENABLE COLOR
            if disable_color:
                self.wi.rightgrip.setStyleSheet("background: transparent")


    def mousePressEvent(self, event):
        self._begin_manual_resize(event)

    def mouseMoveEvent(self, event):
        self._manual_resize(event)

    def mouseReleaseEvent(self, event):
        self._finish_manual_resize(event)

    def _begin_manual_resize(self, event):
        """记录缩放起点，后续始终相对初始窗口几何计算，避免局部坐标随窗口变化抖动。"""
        if event.button() == Qt.LeftButton:
            self._resize_start_pos = event.globalPos()
            self._resize_start_geometry = QRect(self.parent.geometry())
            event.accept()

    def _manual_resize(self, event):
        """根据全局鼠标位移计算一次确定的窗口矩形，并跳过无变化的重复 setGeometry。"""
        if self._resize_start_pos is None or self._resize_start_geometry is None:
            return
        if not event.buttons() & Qt.LeftButton:
            return

        delta = event.globalPos() - self._resize_start_pos
        geometry = QRect(self._resize_start_geometry)
        minimum_width = self.parent.minimumWidth()
        minimum_height = self.parent.minimumHeight()

        if self.position == Qt.TopEdge:
            top = min(geometry.top() + delta.y(), geometry.bottom() - minimum_height + 1)
            geometry.setTop(top)
        elif self.position == Qt.BottomEdge:
            geometry.setHeight(max(minimum_height, geometry.height() + delta.y()))
        elif self.position == Qt.LeftEdge:
            left = min(geometry.left() + delta.x(), geometry.right() - minimum_width + 1)
            geometry.setLeft(left)
        elif self.position == Qt.RightEdge:
            geometry.setWidth(max(minimum_width, geometry.width() + delta.x()))

        if geometry != self.parent.geometry():
            self.parent.setGeometry(geometry)
        event.accept()

    def _finish_manual_resize(self, event):
        """清理本次缩放状态，避免下一次拖动复用旧起点。"""
        self._resize_start_pos = None
        self._resize_start_geometry = None
        event.accept()


    def resizeEvent(self, event):
        if hasattr(self.wi, 'container_top'):
            self.wi.container_top.setGeometry(self.rect())

        elif hasattr(self.wi, 'container_bottom'):
            self.wi.container_bottom.setGeometry(self.rect())

        elif hasattr(self.wi, 'leftgrip'):
            self.wi.leftgrip.setGeometry(self.rect())

        elif hasattr(self.wi, 'rightgrip'):
            self.wi.rightgrip.setGeometry(self.rect())

class Widgets(object):
    def top(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        self.container_top = QFrame(Form)
        self.container_top.setObjectName(u"container_top")
        self.container_top.setGeometry(QRect(0, 0, 500, 10))
        self.container_top.setMinimumSize(QSize(0, 10))
        self.container_top.setMaximumSize(QSize(16777215, 10))
        self.container_top.setFrameShape(QFrame.NoFrame)
        self.container_top.setFrameShadow(QFrame.Raised)
        self.top_layout = QHBoxLayout(self.container_top)
        self.top_layout.setSpacing(0)
        self.top_layout.setObjectName(u"top_layout")
        self.top_layout.setContentsMargins(0, 0, 0, 0)
        self.top_left = QFrame(self.container_top)
        self.top_left.setObjectName(u"top_left")
        self.top_left.setMinimumSize(QSize(10, 10))
        self.top_left.setMaximumSize(QSize(10, 10))
        self.top_left.setCursor(QCursor(Qt.SizeFDiagCursor))
        self.top_left.setStyleSheet(u"background: transparent;")
        self.top_left.setFrameShape(QFrame.NoFrame)
        self.top_left.setFrameShadow(QFrame.Raised)
        self.top_layout.addWidget(self.top_left)
        self.top = QFrame(self.container_top)
        self.top.setObjectName(u"top")
        self.top.setCursor(QCursor(Qt.SizeVerCursor))
        self.top.setStyleSheet(u"background: transparent;")
        self.top.setFrameShape(QFrame.NoFrame)
        self.top.setFrameShadow(QFrame.Raised)
        self.top_layout.addWidget(self.top)
        self.top_right = QFrame(self.container_top)
        self.top_right.setObjectName(u"top_right")
        self.top_right.setMinimumSize(QSize(10, 10))
        self.top_right.setMaximumSize(QSize(10, 10))
        self.top_right.setCursor(QCursor(Qt.SizeBDiagCursor))
        self.top_right.setStyleSheet(u"background: transparent;")
        self.top_right.setFrameShape(QFrame.NoFrame)
        self.top_right.setFrameShadow(QFrame.Raised)
        self.top_layout.addWidget(self.top_right)

    def bottom(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        self.container_bottom = QFrame(Form)
        self.container_bottom.setObjectName(u"container_bottom")
        self.container_bottom.setGeometry(QRect(0, 0, 500, 10))
        self.container_bottom.setMinimumSize(QSize(0, 10))
        self.container_bottom.setMaximumSize(QSize(16777215, 10))
        self.container_bottom.setFrameShape(QFrame.NoFrame)
        self.container_bottom.setFrameShadow(QFrame.Raised)
        self.bottom_layout = QHBoxLayout(self.container_bottom)
        self.bottom_layout.setSpacing(0)
        self.bottom_layout.setObjectName(u"bottom_layout")
        self.bottom_layout.setContentsMargins(0, 0, 0, 0)
        self.bottom_left = QFrame(self.container_bottom)
        self.bottom_left.setObjectName(u"bottom_left")
        self.bottom_left.setMinimumSize(QSize(10, 10))
        self.bottom_left.setMaximumSize(QSize(10, 10))
        self.bottom_left.setCursor(QCursor(Qt.SizeBDiagCursor))
        self.bottom_left.setStyleSheet(u"background: transparent;")
        self.bottom_left.setFrameShape(QFrame.NoFrame)
        self.bottom_left.setFrameShadow(QFrame.Raised)
        self.bottom_layout.addWidget(self.bottom_left)
        self.bottom = QFrame(self.container_bottom)
        self.bottom.setObjectName(u"bottom")
        self.bottom.setCursor(QCursor(Qt.SizeVerCursor))
        self.bottom.setStyleSheet(u"background: transparent;")
        self.bottom.setFrameShape(QFrame.NoFrame)
        self.bottom.setFrameShadow(QFrame.Raised)
        self.bottom_layout.addWidget(self.bottom)
        self.bottom_right = QFrame(self.container_bottom)
        self.bottom_right.setObjectName(u"bottom_right")
        self.bottom_right.setMinimumSize(QSize(10, 10))
        self.bottom_right.setMaximumSize(QSize(10, 10))
        self.bottom_right.setCursor(QCursor(Qt.SizeFDiagCursor))
        self.bottom_right.setStyleSheet(u"background: transparent;")
        self.bottom_right.setFrameShape(QFrame.NoFrame)
        self.bottom_right.setFrameShadow(QFrame.Raised)
        self.bottom_layout.addWidget(self.bottom_right)

    def left(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        self.leftgrip = QFrame(Form)
        self.leftgrip.setObjectName(u"left")
        self.leftgrip.setGeometry(QRect(0, 10, 10, 480))
        self.leftgrip.setMinimumSize(QSize(10, 0))
        self.leftgrip.setCursor(QCursor(Qt.SizeHorCursor))
        self.leftgrip.setStyleSheet(u"background: transparent;")
        self.leftgrip.setFrameShape(QFrame.NoFrame)
        self.leftgrip.setFrameShadow(QFrame.Raised)

    def right(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        self.rightgrip = QFrame(Form)
        self.rightgrip.setObjectName(u"right")
        self.rightgrip.setGeometry(QRect(0, 0, 10, 500))
        self.rightgrip.setMinimumSize(QSize(10, 0))
        self.rightgrip.setCursor(QCursor(Qt.SizeHorCursor))
        self.rightgrip.setStyleSheet(u"background: transparent;")
        self.rightgrip.setFrameShape(QFrame.NoFrame)
        self.rightgrip.setFrameShadow(QFrame.Raised)
