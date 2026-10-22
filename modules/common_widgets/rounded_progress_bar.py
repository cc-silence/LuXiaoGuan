"""公共圆角进度条，保留 Qt 主题和动画，只限制绘制边界。"""
from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QProgressBar, QStyle, QStyleOptionProgressBar


class RoundedProgressBar(QProgressBar):
    """像 QProgressBar 一样使用；细条两端始终为半圆，无额外定时器。"""
    def paintEvent(self, event):
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)
        ratio = self.devicePixelRatioF()
        size = QSize(round(self.width()*ratio), round(self.height()*ratio))
        buffer = getattr(self, "_paint_buffer", None)
        if buffer is None or buffer.size() != size or buffer.devicePixelRatio() != ratio:
            buffer = QImage(size, QImage.Format_ARGB32_Premultiplied)
            buffer.setDevicePixelRatio(ratio)
            self._paint_buffer = buffer
        buffer.fill(0)
        # QStyle 的忙碌动画内部会重设裁切区域，先在复用的小缓冲中绘制。
        source = QPainter(buffer)
        try:
            self.style().drawControl(QStyle.CE_ProgressBar, option, source, self)
        finally:
            source.end()
        painter = QPainter(self)
        try:
            path = QPainterPath()
            rect = QRectF(self.rect())
            radius = min(rect.width(), rect.height()) / 2
            path.addRoundedRect(rect, radius, radius)
            painter.setClipPath(path)
            painter.drawImage(0, 0, buffer)
        finally:
            painter.end()
