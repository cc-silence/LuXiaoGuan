"""文本居中的下拉框，用于下载方式列和架构列。

覆盖 paintEvent 手动绘制居中的当前文字；下拉箭头和弹出列表保持原生样式。
"""

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QStyle, QStyleOptionComboBox, QStylePainter
from .combo_box import CommonComboBox


class CenteredComboBox(CommonComboBox):
    """文本居中的下拉框。
    
    适合场景：在 QTableWidget 等网格布局中使用，需要下拉框文字居中显示。
    调用方式：直接替换 QComboBox 即可，无需额外设置。
    限制：仅影响绘制时的文字对齐，不影响弹出列表的行为。
    """
    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)

        # 计算下拉箭头区域
        arrow_rect = self.style().subControlRect(
            self.style().CC_ComboBox, option,
            self.style().SC_ComboBoxArrow, self,
        )
        # 清除当前文字，只绘制背景+边框+箭头
        option.currentText = ""
        painter.drawComplexControl(self.style().CC_ComboBox, option)

        # 在可用区域手动居中绘制当前文字
        text_rect = QRect(4, 0, arrow_rect.left() - 4, self.height())
        painter.drawText(text_rect, Qt.AlignCenter | Qt.AlignVCenter, self.currentText())


class ComboAlignedLabel(QLabel):
    """与 CenteredComboBox 文字区域一致的固定选项标签。

    主要作用：当表格列只有一个可选值时，不显示无意义的下拉箭头，但文本位置仍要和
    同列可切换的 CenteredComboBox 对齐。
    输入参数：text 是显示文字；parent 是 Qt 父控件。
    返回值：普通 QLabel 子类，可像 QLabel 一样设置 tooltip、objectName 和尺寸策略。
    副作用：只影响自身绘制，不修改表格数据或配置。
    """

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._choice_text = str(text or "")
        # QLabel 仍负责绘制 QSS 背景，文字改由 paintEvent 放进与下拉框相同的可用区域。
        super().setText("")

    def setText(self, text):
        """更新固定选项文字，并触发重绘。"""

        self._choice_text = str(text or "")
        self.update()

    def text(self):
        """返回调用方设置的固定选项文字。"""

        return self._choice_text

    def paintEvent(self, event):
        """保留右侧下拉箭头的空间，以匹配 CenteredComboBox 的文字中心点。"""

        super().paintEvent(event)
        option = QStyleOptionComboBox()
        option.initFrom(self)
        arrow_rect = self.style().subControlRect(
            QStyle.CC_ComboBox,
            option,
            QStyle.SC_ComboBoxArrow,
            self,
        )
        text_rect = QRect(4, 0, max(0, arrow_rect.left() - 4), self.height())
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        painter.drawText(text_rect, Qt.AlignCenter | Qt.AlignVCenter, self._choice_text)
        painter.end()
