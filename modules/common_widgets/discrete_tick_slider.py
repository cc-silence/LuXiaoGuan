"""带离散刻度的水平滑条。

主要作用：为缩放、字号、密度等有限档位设置提供直观刻度，同时保留 QSlider
的键盘操作、鼠标拖动和 valueChanged 信号。
调用方式：设置范围和步进后调用 set_tick_configuration() 传入全部档位。
副作用：只绘制控件，不修改配置；调用 set_light_theme() 会改变绘制颜色。
限制：当前只支持水平滑条；当前值应由调用方配合右侧输入框或标签展示。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class DiscreteTickSlider(QSlider):
    """具有吸附行为和离散刻度的水平滑条。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dragging = False
        self._tick_values = []
        self._light_theme = False
        self.setTickPosition(QSlider.NoTicks)
        self.setMinimumHeight(44)
        self.setMaximumHeight(44)

    def set_tick_configuration(self, tick_values):
        """配置全部离散刻度。

        参数：tick_values 是所有离散值。
        返回值：无。
        副作用：更新内部绘制数据并请求重绘，不修改当前 value。
        """
        values = sorted({int(value) for value in tick_values})
        self._tick_values = [
            value for value in values
            if self.minimum() <= value <= self.maximum()
        ]
        self.update()

    def set_light_theme(self, enabled):
        """设置刻度的亮暗主题颜色。

        参数：enabled 为 True 使用亮色配色，为 False 使用暗色配色。
        返回值：无。副作用：仅触发控件重绘。
        """
        enabled = bool(enabled)
        if self._light_theme != enabled:
            self._light_theme = enabled
            self.update()

    def mousePressEvent(self, event):
        """记录左键拖动状态，交给父类处理点击和焦点。"""
        if event.button() == Qt.LeftButton:
            self._dragging = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """结束拖动状态，交给父类提交最终滑条值。"""
        self._dragging = False
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        """拖动时吸附到 singleStep 对齐的离散数值。"""
        if self._dragging and self.orientation() == Qt.Horizontal:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            groove = self.style().subControlRect(
                QStyle.CC_Slider, option, QStyle.SC_SliderGroove, self
            )
            handle = self.style().subControlRect(
                QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self
            )
            available = max(1, groove.width() - handle.width())
            position = int(event.position().x() - groove.x() - handle.width() / 2)
            raw = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), position, available, option.upsideDown
            )
            if self._tick_values:
                snapped = min(self._tick_values, key=lambda value: abs(value - raw))
            else:
                step = max(1, self.singleStep())
                snapped = round((raw - self.minimum()) / step) * step + self.minimum()
                snapped = max(self.minimum(), min(self.maximum(), snapped))
            if snapped != self.value():
                self.setValue(snapped)
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        """先交给 QSS 绘制轨道和圆点，再叠加离散刻度线。"""
        super().paintEvent(event)
        if self.orientation() != Qt.Horizontal or not self._tick_values:
            return

        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderGroove, self
        )
        handle = self.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self
        )
        available = max(1, groove.width() - handle.width())
        start_x = groove.x() + handle.width() / 2
        tick_y = groove.bottom() + 5

        if self._light_theme:
            minor_color = QColor("#8fa0b5")
        else:
            minor_color = QColor("#607083")
        active_color = QColor("#0078d4")

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for value in self._tick_values:
            position = QStyle.sliderPositionFromValue(
                self.minimum(), self.maximum(), value, available, option.upsideDown
            )
            x = round(start_x + position)
            is_current = value == self.value()
            color = active_color if is_current else minor_color
            tick_height = 8 if is_current else 5
            painter.setPen(QPen(color, 1))
            painter.drawLine(x, tick_y, x, tick_y + tick_height)
        painter.end()
