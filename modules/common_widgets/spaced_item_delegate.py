"""列表行高的共享下限，随字体和界面缩放留出上下呼吸空间。"""

from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem
from modules.ui_scale_manager import UiScaleManager


def minimum_row_height(view):
    """只读取字体与缩放；不逐行测量、不修改列表数据。"""
    scale = UiScaleManager.for_widget(view)
    return max(scale.px(32), view.fontMetrics().lineSpacing() + scale.px(12))


class SpacedItemDelegate(QStyledItemDelegate):
    """保留原生绘制和长内容尺寸，仅为普通单行补足最小行高。"""

    def __init__(self, parent=None, *, show_focus_rect=True):
        super().__init__(parent)
        self._show_focus_rect = show_focus_rect

    def paint(self, painter, option, index):
        if not self._show_focus_rect:
            option = QStyleOptionViewItem(option)
            option.state &= ~QStyle.State_HasFocus
        super().paint(painter, option, index)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(max(hint.height(), minimum_row_height(self.parent())))
        return hint
