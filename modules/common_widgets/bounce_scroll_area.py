# ///////////////////////////////////////////////////////////////
#
# BounceScrollArea —— 带 iOS 风格橡皮筋回弹效果的 QScrollArea
#
# ///////////////////////////////////////////////////////////////
from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import QScrollArea

from .auto_hide_scroll_bar import AutoHideScrollBar


class BounceScrollArea(QScrollArea):
    """带 iOS 风格橡皮筋回弹的滚动区域。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._overscroll_val = 0.0        # 累计超界偏移像素（内部存储）
        self._original_y = None            # 内容 widget 的原始 y
        self._animation = None
        self._bounce_timer = QTimer(self)
        self._bounce_timer.setSingleShot(True)
        self._bounce_timer.setInterval(150)
        self._bounce_timer.timeout.connect(self._bounce_back)
        self.setVerticalScrollBar(AutoHideScrollBar(Qt.Vertical, self))
        self.setHorizontalScrollBar(AutoHideScrollBar(Qt.Horizontal, self))

    # ---- 可动画属性 ----

    def _get_overscroll(self):
        return self._overscroll_val

    def _set_overscroll(self, value):
        self._overscroll_val = value
        w = self.widget()
        if not w:
            return
        if self._original_y is None:
            self._original_y = w.y()
        w.move(w.x(), self._original_y + int(value))

    _overscroll = Property(float, _get_overscroll, _set_overscroll)

    # ---- 滚轮事件 ----

    def wheelEvent(self, event):
        vbar = self.verticalScrollBar()
        if not vbar or vbar.maximum() <= 0:
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta == 0:
            return

        at_top = vbar.value() <= vbar.minimum()
        at_bottom = vbar.value() >= vbar.maximum()

        if (delta > 0 and at_top) or (delta < 0 and at_bottom):
            self._cancel_animation()
            w = self.widget()
            if not w:
                return
            if self._original_y is None:
                self._original_y = w.y()

            # iOS 非线性阻尼
            sign = 1.0 if delta > 0 else -1.0
            overshoot = sign * (abs(delta) * 0.5 / (1.0 + abs(delta) * 0.00055))
            self._overscroll_val += overshoot
            self._overscroll_val = max(-300.0, min(300.0, self._overscroll_val))
            w.move(w.x(), self._original_y + int(self._overscroll_val))
            event.accept()
            self._bounce_timer.start()
            return

        if self._overscroll_val != 0.0:
            self._instant_reset()
        super().wheelEvent(event)

    # ---- 回弹 ----

    def _bounce_back(self):
        if self._overscroll_val == 0.0:
            return
        self._cancel_animation()
        w = self.widget()
        if not w or self._original_y is None:
            self._clean_state()
            return
        anim = QPropertyAnimation(self, b"_overscroll")
        anim.setDuration(300)
        anim.setStartValue(self._overscroll_val)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self._on_bounce_finished)
        anim.start()
        self._animation = anim

    def _on_bounce_finished(self):
        self._animation = None
        self._clean_state()

    # ---- 辅助 ----

    def _clean_state(self):
        self._overscroll_val = 0.0
        self._original_y = None

    def _instant_reset(self):
        self._cancel_animation()
        w = self.widget()
        if w and self._original_y is not None:
            w.move(w.x(), self._original_y)
        self._clean_state()

    def _cancel_animation(self):
        if self._animation:
            self._animation.stop()
            self._animation.deleteLater()
            self._animation = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overscroll_val != 0.0:
            self._instant_reset()
