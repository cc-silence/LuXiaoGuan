"""表头只裁剪左上和右上圆角，不改下方边缘和列交互。"""
from PySide6.QtCore import QObject, QEvent, QRectF, Qt
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import QHeaderView
from modules.ui_scale_manager import UiScaleManager


class HeaderCorners(QObject):
    """绑定表头尺寸事件，不轮询、不缓存像素图。"""
    def __init__(self, header):
        super().__init__(header)
        self.header = header
        self._key = None
        header.installEventFilter(self)
        self.refresh()

    def refresh(self):
        """缩放或尺寸变化时更新裁剪；底部两角保持直角。"""
        header = self.header
        radius = UiScaleManager.for_widget(header).px(5)
        key = (header.width(), header.height(), radius)
        if self._key == key or min(key[:2]) <= 0:
            return
        self._key = key
        path = QPainterPath()
        path.addRoundedRect(QRectF(0,0,key[0],key[1]+radius),radius,radius)
        header.setMask(QRegion(path.toFillPolygon().toPolygon()).intersected(QRegion(header.rect())))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.refresh()
        return False


def round_table_headers(root):
    """统一处理抓包、信号和文件列表的水平表头。"""
    for header in root.findChildren(QHeaderView):
        if header.orientation() != Qt.Horizontal:
            continue
        helper = getattr(header, '_top_corner_clip', None)
        if helper is None:
            header._top_corner_clip = HeaderCorners(header)
        else:
            helper.refresh()
