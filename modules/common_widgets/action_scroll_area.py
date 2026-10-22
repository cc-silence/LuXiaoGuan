"""操作侧栏的公共滚动容器，不压缩内容的最小布局尺寸。"""

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QLayout, QScrollArea, QSizePolicy

from modules.ui_scale_manager import UiScaleManager
from .auto_hide_scroll_bar import AutoHideScrollBar


class ActionScrollArea(QScrollArea):
    """承载操作面板；内容不足高时滚动，宽度依据按钮文本与缩放计算。"""

    def __init__(self, content, parent=None):
        super().__init__(parent)
        self.setObjectName("captureScrollArea")
        self.setFrameShape(QScrollArea.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBar(AutoHideScrollBar(Qt.Vertical, self))
        content.layout().setSizeConstraint(QLayout.SetMinimumSize)
        self.setWidget(content)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def eventFilter(self, watched, event):
        """内容尺寸改变后通知父布局重算宽度，不轮询。"""
        result = super().eventFilter(watched, event)
        if watched is self.widget() and event.type() in (
                QEvent.LayoutRequest, QEvent.FontChange, QEvent.StyleChange):
            self.updateGeometry()
        return result

    def sizeHint(self):
        hint = super().sizeHint()
        scale = UiScaleManager.for_widget(self)
        content = self.widget()
        margins = content.layout().contentsMargins()
        width = max(scale.px(186) + margins.left() + margins.right(),
                    content.minimumSizeHint().width(), content.minimumWidth())
        hint.setWidth(width + self.verticalScrollBar().sizeHint().width())
        return hint

    def minimumSizeHint(self):
        hint = self.sizeHint()
        hint.setHeight(0)
        return hint
