"""仅在文字溢出时自动横向滚动的标签。

主要作用：在较窄的表格单元格中完整展示版本号等短文字，优先使用两行，
两行仍放不下时再横向滚动。
输入参数：构造时可传入文字和父控件；setText() 更新显示内容。
返回值：text() 返回完整原文。
副作用：文字超出可用宽度且控件可见时启动轻量绘制定时器；不修改配置或文件。
调用限制：适合短文本，不支持富文本、手动换行或用户选择复制。
"""

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


class OverflowMarqueeLabel(QWidget):
    """文字优先用一至两行完整显示，两行仍放不下时循环向左滚动。"""

    _SIDE_MARGIN = 4
    _TEXT_GAP = 28
    _TICK_MS = 40
    _START_PAUSE_TICKS = 25

    def __init__(self, text="", parent=None):
        """创建单行滚动标签；构造本身不会读写任何外部数据。"""

        super().__init__(parent)
        self._text = str(text or "")
        self._layout_key = None
        self._layout_lines = None
        self._text_color = None
        self._offset = 0
        self._pause_ticks = self._START_PAUSE_TICKS
        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._advance)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._refresh_overflow_state()

    def text(self):
        """返回未裁剪、未滚动处理的完整文字。"""

        return self._text

    def setText(self, text):
        """更新完整文字，并从起点重新判断是否需要滚动。"""

        text = str(text or "")
        if text == self._text:
            return
        self._text = text
        self._offset = 0
        self._pause_ticks = self._START_PAUSE_TICKS
        self._refresh_overflow_state()
        self.update()

    def setTextColor(self, color=None):
        """设置可选文字颜色；传入 None 时恢复当前主题默认文字色。"""

        self._text_color = QColor(color) if color is not None else None
        self.update()

    def isTextOverflowing(self):
        """返回当前文字是否连两行也无法完整显示，需要横向滚动。"""

        return self._display_lines() is None

    def _display_lines(self):
        """返回可完整静态显示的一行或两行文字；放不下时返回 None。

        两行拆分会尝试所有字符边界，选择两行宽度最接近的切分点。版本号、中文
        状态说明通常没有空格，不能只依赖按单词换行。
        """

        key = (self._text, self.width(), self.font().toString())
        if key != self._layout_key:
            self._layout_key = key
            self._layout_lines = self._calculate_lines()
        return self._layout_lines

    def _calculate_lines(self):
        """只在文字、宽度或字体变化时重算换行，避免每帧重复测量。"""
        available = max(0, self.width() - self._SIDE_MARGIN * 2)
        if available <= 0:
            return None
        metrics = self.fontMetrics()
        if metrics.horizontalAdvance(self._text) <= available:
            return [self._text]
        best_lines = None
        best_balance = None
        for index in range(1, len(self._text)):
            first = self._text[:index].rstrip()
            second = self._text[index:].lstrip()
            if not first or not second:
                continue
            first_width = metrics.horizontalAdvance(first)
            second_width = metrics.horizontalAdvance(second)
            if first_width > available or second_width > available:
                continue
            balance = abs(first_width - second_width)
            if best_balance is None or balance < best_balance:
                best_lines = [first, second]
                best_balance = balance
        return best_lines

    def _refresh_overflow_state(self):
        """仅在两行仍放不下时启停计时器，并同步完整文字提示。"""

        overflowing = self.isTextOverflowing()
        self.setToolTip(self._text if overflowing else "")
        if overflowing and self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._offset = 0

    def _advance(self):
        """每帧推进一个像素，首尾短暂停顿避免文字一出现就移动。"""

        if not self.isTextOverflowing():
            self._refresh_overflow_state()
            self.update()
            return
        if self._pause_ticks > 0:
            self._pause_ticks -= 1
            return
        text_width = self.fontMetrics().horizontalAdvance(self._text)
        self._offset += 1
        if self._offset >= text_width + self._TEXT_GAP:
            self._offset = 0
            self._pause_ticks = self._START_PAUSE_TICKS
        self.update()

    def paintEvent(self, event):
        """绘制一至两行居中文字，或两份首尾衔接的滚动文字。"""

        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setClipRect(self.rect())
        painter.setPen(self._text_color or self.palette().color(self.foregroundRole()))
        metrics = self.fontMetrics()
        baseline = (self.height() - metrics.height()) // 2 + metrics.ascent()
        display_lines = self._display_lines()
        if display_lines is not None:
            total_height = metrics.height() * len(display_lines)
            first_baseline = (self.height() - total_height) // 2 + metrics.ascent()
            for index, line in enumerate(display_lines):
                line_width = metrics.horizontalAdvance(line)
                x = max(self._SIDE_MARGIN, (self.width() - line_width) // 2)
                painter.drawText(x, first_baseline + index * metrics.height(), line)
            return
        text_width = metrics.horizontalAdvance(self._text)
        first_x = self._SIDE_MARGIN - self._offset
        painter.drawText(first_x, baseline, self._text)
        painter.drawText(first_x + text_width + self._TEXT_GAP, baseline, self._text)

    def changeEvent(self, event):
        """字体变化后重新计算换行和滚动状态。"""
        super().changeEvent(event)
        if event.type() == QEvent.FontChange and hasattr(self, "_timer"):
            self._refresh_overflow_state()
            self.update()

    def resizeEvent(self, event):
        """列宽变化后重新判断是否溢出。"""

        super().resizeEvent(event)
        self._refresh_overflow_state()

    def showEvent(self, event):
        """分类展开或页面显示后按实际宽度恢复必要的滚动。"""

        super().showEvent(event)
        self._refresh_overflow_state()

    def hideEvent(self, event):
        """控件不可见时停止计时器，避免折叠分类仍持续刷新。"""

        self._timer.stop()
        super().hideEvent(event)
