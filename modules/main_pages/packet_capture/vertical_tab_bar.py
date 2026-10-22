"""抓包页面外层纵向标签栏。"""

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QStyle, QStyleOptionTab, QStylePainter, QTabBar
from modules.ui_scale_manager import UiScaleManager


class VerticalTextTabBar(QTabBar):
    """窄竖向标签栏，中文按字符上下排列，不旋转文字。

    参数：parent 是 QTabWidget。返回值和副作用沿用 QTabBar；只负责绘制标签，
    不保存配置、不切换页面。标签标题使用普通连续文字即可。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setShape(QTabBar.RoundedWest)
        self.setExpanding(False)
        self.setUsesScrollButtons(False)
        self.setElideMode(Qt.ElideNone)
        self.refresh_scale()

    def refresh_scale(self):
        """字体和背景使用同一逻辑尺寸，允许切档后重新计算标签几何。"""
        scale = UiScaleManager.for_widget(self)
        self.setFixedWidth(max(scale.px(46), self.fontMetrics().maxWidth() + scale.px(12)))
        self.updateGeometry()
        self.update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self.refresh_scale()

    def _line_height(self):
        """返回绘制和标签测量共同使用的字行高度，避免两套常量不一致。"""
        return max(UiScaleManager.for_widget(self).px(18), self.fontMetrics().height())

    def tabSizeHint(self, index):
        """返回窄栏标签尺寸，避免纵向标签占用过宽内容区。"""

        text = self.tabText(index).replace(" ", "")
        scale = UiScaleManager.for_widget(self)
        return QSize(self.width() - scale.px(2), max(scale.px(82), len(text) * self._line_height() + scale.px(14)))

    def paintEvent(self, event):
        """按普通水平文字绘制每个汉字，禁止 QTabBar 的默认旋转绘制。"""

        painter = QStylePainter(self)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            rect = self.tabRect(index)
            option.rect = rect
            # CE_TabBarTab 会同时画外形和默认标签。先清空文字，只让 Qt 画背景和选中态，
            # 再逐字绘制中文，否则默认旋转文字会和自定义文字叠在一起。
            text = self.tabText(index).replace(" ", "")
            option.text = ""
            painter.drawControl(QStyle.CE_TabBarTab, option)
            lines = list(text)
            painter.save()
            # 自定义文字不使用 HighlightedText。Windows/Qt 的默认 HighlightedText 在亮色
            # 主题下通常是白色，会覆盖 QSS 中选中项的深色文字并降低对比度；Text 角色会
            # 正确服从普通态和选中态的 QSS color。
            painter.setPen(option.palette.color(QPalette.Text))
            font = painter.font()
            font.setPointSize(max(9, font.pointSize()))
            painter.setFont(font)
            line_height = self._line_height()
            start_y = rect.top() + max(UiScaleManager.for_widget(self).px(4), (rect.height() - line_height * len(lines)) // 2)
            for line_index, character in enumerate(lines):
                character_rect = QRect(
                    rect.left(),
                    start_y + line_index * line_height,
                    rect.width(),
                    line_height,
                )
                painter.drawText(character_rect, Qt.AlignHCenter | Qt.AlignVCenter, character)
            painter.restore()
