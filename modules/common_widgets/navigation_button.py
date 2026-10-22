"""导航按钮保留键盘操作，不绘制原生虚线焦点框。"""
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton, QStylePainter


class NavigationButton(QPushButton):
    """仅隐藏焦点框，选中、悬停和按下状态仍交由主题绘制。"""
    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.state &= ~QStyle.State_HasFocus
        painter = QStylePainter(self)
        painter.drawControl(QStyle.CE_PushButton, option)
