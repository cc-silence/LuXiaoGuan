"""
带边框扫光效果的按钮。

用法：
    btn = SweepButton("全部检测")
    btn.setObjectName("downloadActionButton")
    btn.set_light_theme(True)

模式：
  - 悬停扫光（默认）：鼠标悬停时扫光，移出即停。
    用 btn.set_hover_sweep(False) 禁用。
  - 常驻扫光：btn.set_always_sweep(True)，用于"检测中""等待队列中"等状态指示。
  - 引导扫光：btn.set_guided_mode(True)，按钮使用 hover 样式 + 循环扫光，
    首次点击后调 set_guided_mode(False) 停用，适合"全部检测"引导点击。
"""

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRectF
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QPen, QBrush
from PySide6.QtWidgets import QPushButton, QStyleOptionButton, QStyle, QGraphicsOpacityEffect


class SweepButton(QPushButton):
    """QPushButton 子类，带边框扫光效果。"""

    _MS_PER_PX = 8
    _GLOW_WIDTH = 0.30
    _FADE_EDGE = 0.12
    _LOOP_MIN_WIDTH = 130

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._sweep_pos = -1.0
        self._light_theme = False
        self._always_sweep = False
        self._hover_sweep = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._breathing = False
        self._breath_effect = None
        self._breath_anim = None
        self.setMouseTracking(True)

    def set_light_theme(self, is_light):
        self._light_theme = bool(is_light)
        self.update()

    def set_hover_sweep(self, active):
        """禁用后鼠标悬停不再触发扫光，不影响常驻/引导扫光。"""
        self._hover_sweep = bool(active)
        if not active and not self._always_sweep:
            self._sweep_pos = -1.0
            self._timer.stop()
            self.update()

    def set_always_sweep(self, active):
        """常驻扫光，不依赖悬停持续循环。起始位置跳过渐隐段，保证可见。"""
        self._always_sweep = bool(active)
        if active:
            self._sweep_pos = self._FADE_EDGE
            if self.isVisible():
                self._timer.start(16)
        else:
            self._sweep_pos = -1.0
            self._timer.stop()
            self.update()

    def set_guided_mode(self, active):
        """
        引导模式：按钮显示 hover 样式 + 循环扫光，用于引导用户首次点击。
        传入 False 恢复常态：去掉 hover 样式、停止扫光、恢复悬停扫光。
        """
        self.setProperty("guided", active)
        self.style().unpolish(self)
        self.style().polish(self)
        if active:
            self._always_sweep = True
            self._hover_sweep = False
            self._sweep_pos = self._FADE_EDGE
            if self.isVisible():
                self._timer.start(16)
        else:
            self._always_sweep = False
            self._hover_sweep = True
            self._sweep_pos = -1.0
            self._timer.stop()
            self.update()

    def set_breathing(self, active):
        """呼吸模式：按钮在 0.6↔1.0 透明度之间循环脉动，引导用户点击关键按钮。
        调用 set_breathing(False) 停止并清理效果，避免多余渲染开销。
        """
        if active == self._breathing:
            return
        self._breathing = bool(active)
        if active:
            if self._breath_anim is None:
                self._breath_effect = QGraphicsOpacityEffect(self)
                self._breath_anim = QPropertyAnimation(self._breath_effect, b"opacity", self)
                self._breath_anim.setDuration(1500)
                self._breath_anim.setStartValue(0.6)
                self._breath_anim.setEndValue(1.0)
                self._breath_anim.setLoopCount(-1)
                self._breath_anim.setEasingCurve(QEasingCurve.SineCurve)
            self.setGraphicsEffect(self._breath_effect)
            if self.isVisible():
                self._breath_anim.start()
        else:
            if self._breath_anim:
                self._breath_anim.stop()
                self._breath_anim.deleteLater()
                self._breath_anim = None
            self.setGraphicsEffect(None)
            self._breath_effect = None

    # ---------- 事件 ----------
    def hideEvent(self, event):
        """不可见时暂停已有动画，不消耗后台刷新资源。"""
        self._timer.stop()
        if not self._always_sweep:
            self._sweep_pos = -1.0
        if self._breath_anim is not None:
            self._breath_anim.pause()
        super().hideEvent(event)

    def showEvent(self, event):
        """再次显示时恢复原来的引导或呼吸动画。"""
        super().showEvent(event)
        if self._always_sweep:
            self._timer.start(16)
        if self._breathing and self._breath_anim is not None:
            if self._breath_anim.state() == QPropertyAnimation.Paused:
                self._breath_anim.resume()
            else:
                self._breath_anim.start()

    def enterEvent(self, event):
        if self._hover_sweep and not self._always_sweep:
            self._sweep_pos = 0.0
            self._timer.start(16)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._always_sweep:
            self._sweep_pos = -1.0
            self._timer.stop()
            self.update()
        super().leaveEvent(event)

    # ---------- 动画 ----------
    def _duration(self):
        w = max(self.width(), 1)
        return max(250, int(w * self._MS_PER_PX))

    def _should_loop(self):
        return self._always_sweep or self.width() > self._LOOP_MIN_WIDTH

    def _tick(self):
        duration = self._duration()
        self._sweep_pos += 16.0 / duration
        if self._sweep_pos > 1.0:
            if self._should_loop():
                self._sweep_pos = 0.0
            else:
                self._timer.stop()
                self._sweep_pos = 1.0
        self.update()

    # ---------- 绘制 ----------
    def paintEvent(self, event):
        if self._sweep_pos < -0.5:
            super().paintEvent(event)
            return

        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self.style().drawControl(QStyle.CE_PushButton, opt, painter, self)

        if self._light_theme:
            base_alpha = 160
            glow_color = QColor(255, 255, 255, base_alpha)
        else:
            glow_color = QColor(255, 255, 255, 75)

        w = self.width()
        h = self.height()
        sp = self._sweep_pos

        edge = self._FADE_EDGE
        if sp < edge:
            envelope = sp / edge
        elif sp > 1.0 - edge:
            envelope = (1.0 - sp) / edge
        else:
            envelope = 1.0
        effective_alpha = int(glow_color.alpha() * envelope)

        grad = QLinearGradient(0, 0, w, 0)
        half = self._GLOW_WIDTH * 0.5
        dim = QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 0)
        peak = QColor(glow_color.red(), glow_color.green(), glow_color.blue(), effective_alpha)
        grad.setColorAt(max(0.0, sp - half), dim)
        grad.setColorAt(sp, peak)
        grad.setColorAt(min(1.0, sp + half), dim)

        painter.setPen(QPen(QBrush(grad), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(1.5, 1.5, w - 3, h - 3), 5, 5)
