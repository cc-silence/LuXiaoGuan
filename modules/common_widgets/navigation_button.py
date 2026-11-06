"""导航按钮保留键盘操作，不绘制原生虚线焦点框。"""
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton, QStylePainter


class NavigationButton(QPushButton):
    """Keep keyboard focus; stack main navigation, use native rows for settings."""

    stack_icon_text = True
    ICON_TEXT_SPACING = 4

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.state &= ~QStyle.State_HasFocus

        text = self.text()
        icon = self.icon()
        if self.stack_icon_text and text and not icon.isNull():
            # 1. 临时去掉文字和图标，让 QStyle 绘制纯净的按钮背景、边框和状态色（hover/pressed/selected）
            option.text = ""
            option.icon = QIcon()
            painter.drawControl(QStyle.CE_PushButton, option)

            # 2. 计算图标在上、文字在下，左右居中的几何坐标
            rect = self.rect()
            icon_size = self.iconSize()
            iw, ih = icon_size.width(), icon_size.height()

            fm = self.fontMetrics()
            th = fm.height()
            spacing = getattr(self, 'icon_text_spacing', self.ICON_TEXT_SPACING)

            total_h = ih + spacing + th
            start_y = max(1, (rect.height() - total_h) // 2)

            icon_x = (rect.width() - iw) // 2
            icon_y = start_y
            icon_rect = QRect(icon_x, icon_y, iw, ih)

            mode = QIcon.Normal
            if not self.isEnabled():
                mode = QIcon.Disabled
            elif self.underMouse():
                mode = QIcon.Active
            state = QIcon.On if (self.isChecked() or self.property("sidebarSelected") in (True, "true")) else QIcon.Off
            icon.paint(painter, icon_rect, Qt.AlignCenter, mode, state)

            # 3. 绘制居中文本（使用原样式 CE_PushButtonLabel 保证继承 QSS 颜色、选中态和文字效果）
            option.text = text
            option.icon = QIcon()
            text_y = icon_y + ih + spacing
            option.rect = QRect(0, text_y, rect.width(), th)
            painter.drawControl(QStyle.CE_PushButtonLabel, option)
        else:
            painter.drawControl(QStyle.CE_PushButton, option)
