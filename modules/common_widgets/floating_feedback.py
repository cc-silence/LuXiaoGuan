"""主窗口级短暂浮动反馈组件。"""

from PySide6.QtCore import QEasingCurve, QPoint, QParallelAnimationGroup, QPropertyAnimation, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel


class FloatingFeedback(QLabel):
    """显示不占布局的操作结果文字，并以轻微上浮和淡出结束。

    参数：parent 是承载反馈的主窗口内容容器，通常为 ui.styleSheet。
    返回值：组件实例；调用 show_feedback() 才会显示。
    副作用：会在父控件右下角显示短暂文字，不读写配置、不拦截鼠标操作。
    调用限制：只用于已经完成的结果反馈。保存校验失败、按钮按下等未完成动作不能调用，
    同一时间只显示最新一条反馈。
    """

    _DURATION_MS = 1100
    _MODE_DURATION_MS = 2400

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("floatingFeedback")
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setProperty("feedbackKind", "cancel")
        self.setProperty("feedbackLayout", "corner")
        self.hide()

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._position_animation = QPropertyAnimation(self, b"pos", self)
        self._opacity_animation = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._animation = QParallelAnimationGroup(self)
        self._animation.addAnimation(self._position_animation)
        self._animation.addAnimation(self._opacity_animation)
        self._animation.finished.connect(self.hide)

    def show_feedback(self, text, kind, scale_manager=None):
        """显示一次操作反馈。

        参数：text 是用户可见结果文本；kind 是 success 或 cancel；scale_manager 用于字体和边距缩放。
        返回值：无。
        副作用：停止旧动画、显示新文字并启动上浮淡出动画。
        调用限制：kind 仅支持 success/cancel；调用方应在业务真正成功或取消恢复完成后调用。
        """
        if kind not in {"success", "cancel"}:
            raise ValueError(f"不支持的反馈类型：{kind}")
        self._animation.stop()
        self._position_animation.setKeyValues([])
        self._opacity_animation.setKeyValues([])
        # 普通右下角反馈保持无背景文字，不保留模式横幅的内容边距。
        self.setMargin(0)
        self.setText(str(text))
        self.setProperty("feedbackKind", kind)
        self.setProperty("feedbackLayout", "corner")
        self.style().unpolish(self)
        self.style().polish(self)

        font = QFont(self.font())
        font.setBold(True)
        font.setPointSize(scale_manager.pt(13) if scale_manager is not None else 13)
        self.setFont(font)
        self.adjustSize()

        target = self._target_position(scale_manager)
        start = target + QPoint(0, self._scaled_px(scale_manager, 8))
        end = target - QPoint(0, self._scaled_px(scale_manager, 14))
        self.move(start)
        self._opacity_effect.setOpacity(1.0)
        self.show()
        self.raise_()

        self._position_animation.setDuration(self._DURATION_MS)
        self._position_animation.setStartValue(start)
        self._position_animation.setEndValue(end)
        self._position_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._opacity_animation.setDuration(self._DURATION_MS)
        self._opacity_animation.setStartValue(1.0)
        self._opacity_animation.setKeyValueAt(0.52, 1.0)
        self._opacity_animation.setEndValue(0.0)
        self._opacity_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.start()

    def show_mode_transition(self, title, detail, scale_manager=None):
        """显示主机模式切换的顶部居中横幅。

        参数：title 是模式切换结果；detail 说明本次切换影响的功能。
        返回值：无。
        副作用：显示较普通反馈更长的横幅，并播放下滑淡入、停留和淡出动画。
        调用限制：只用于已完成的模式切换，不能代替普通保存或错误提示。
        """
        self._animation.stop()
        self._position_animation.setKeyValues([])
        self._opacity_animation.setKeyValues([])
        # QLabel 的 QSS padding 在部分父级样式链下不会参与 adjustSize；显式设置内容
        # 边距，保证横幅文字与四边始终保留可见间距。
        self.setMargin(self._scaled_px(scale_manager, 12))
        self.setText(f"{title}\n{detail}")
        self.setProperty("feedbackKind", "mode")
        self.setProperty("feedbackLayout", "banner")
        self.style().unpolish(self)
        self.style().polish(self)

        font = QFont(self.font())
        font.setBold(True)
        font.setPointSize(scale_manager.pt(12) if scale_manager is not None else 12)
        self.setFont(font)
        self.adjustSize()

        target = self._mode_target_position(scale_manager)
        start = target - QPoint(0, self._scaled_px(scale_manager, 10))
        self.move(start)
        self._opacity_effect.setOpacity(0.0)
        self.show()
        self.raise_()

        self._position_animation.setDuration(self._MODE_DURATION_MS)
        self._position_animation.setStartValue(start)
        self._position_animation.setKeyValueAt(0.14, target)
        self._position_animation.setEndValue(target)
        self._position_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._opacity_animation.setDuration(self._MODE_DURATION_MS)
        self._opacity_animation.setStartValue(0.0)
        self._opacity_animation.setKeyValueAt(0.12, 1.0)
        self._opacity_animation.setKeyValueAt(0.72, 1.0)
        self._opacity_animation.setEndValue(0.0)
        self._opacity_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.start()

    def reposition(self, scale_manager=None):
        """父窗口尺寸变化后重新定位当前可见反馈。"""
        if self.isVisible() and self._animation.state() != QParallelAnimationGroup.Running:
            self.move(self._target_position(scale_manager))

    def _target_position(self, scale_manager):
        """计算父容器右下角、底栏上方的文字落点。"""
        parent = self.parentWidget()
        if parent is None:
            return QPoint(0, 0)
        margin_right = self._scaled_px(scale_manager, 28)
        margin_bottom = self._scaled_px(scale_manager, 64)
        return QPoint(
            max(0, parent.width() - self.width() - margin_right),
            max(0, parent.height() - self.height() - margin_bottom),
        )

    def _mode_target_position(self, scale_manager):
        """计算模式切换横幅在主内容顶部居中的落点。"""
        parent = self.parentWidget()
        if parent is None:
            return QPoint(0, 0)
        margin_top = self._scaled_px(scale_manager, 72)
        return QPoint(
            max(0, (parent.width() - self.width()) // 2),
            margin_top,
        )

    @staticmethod
    def _scaled_px(scale_manager, value):
        """返回缩放后的像素值，缺少缩放管理器时使用原始值。"""
        return scale_manager.px(value) if scale_manager is not None else value
