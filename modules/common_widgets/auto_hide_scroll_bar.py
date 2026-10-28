"""自动隐藏滚动条公共控件。

主要作用：提供类似 Windows 临时滚动条的行为，平时不绘制滚动条，鼠标移入或内容滚动时
短暂显示，停止操作后自动隐藏。
输入参数：沿用 QScrollBar 的方向和父控件参数。
返回值：可直接交给 QAbstractScrollArea.setVerticalScrollBar() 或 setHorizontalScrollBar()。
副作用：默认隐藏时只降低滑块透明度；reserve_space=True 会保留滚动条槽位，避免网格或
表格在滑块显示/隐藏时重新排版；reserve_space=False 会在隐藏时释放槽位。
限制：保留槽位模式依赖透明轨道样式，否则宿主背景会在预留区域显示出来。
"""

from functools import lru_cache

from PySide6.QtCore import QEvent, QEasingCurve, QPropertyAnimation, Qt, QTimer, Slot
from PySide6.QtWidgets import QAbstractScrollArea, QGraphicsOpacityEffect, QScrollBar


@lru_cache(maxsize=1)
def _inset_stylesheet():
    """所有需要端部留白的滚动条共享几何样式，配色仍由当前主题提供。"""
    from modules.config_manager import THEMES_DIR
    return (THEMES_DIR / "scrollbar_inset.qss").read_text(encoding="utf-8")


class AutoHideScrollBar(QScrollBar):
    """默认不绘制、操作时短暂显示的滚动条。"""

    def __init__(self, *args, **kwargs):
        # 表格和网格需要稳定宽度，列表和文本输入框则可在隐藏时释放轨道区域。
        self._scroll_viewport = None
        self._scroll_area = None
        self._reserve_space = bool(kwargs.pop("reserve_space", True))
        inset_track = bool(kwargs.pop("inset_track", False))
        super().__init__(*args, **kwargs)
        self.setObjectName("autoHideScrollBar")
        if inset_track:
            self.setStyleSheet(_inset_stylesheet())
        self._revealed = False
        self._mouse_inside = False
        self._dragging = False
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade_animation = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(900)
        self._hide_timer.timeout.connect(self._hide_if_idle)
        self.valueChanged.connect(self._reveal_temporarily)
        self.sliderMoved.connect(self._reveal_temporarily)
        self.rangeChanged.connect(self._handle_range_changed)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        self._scroll_area = self._find_scroll_area(args, kwargs)
        self._scroll_viewport = None
        if self._scroll_area is not None:
            self._scroll_viewport = self._scroll_area.viewport()
            self._scroll_viewport.installEventFilter(self)
            self._scroll_area.destroyed.connect(self._clear_scroll_area)
            self._set_scrollbar_slot_visible(self._reserve_space)

    @Slot()
    @Slot(object)
    def _clear_scroll_area(self, *_args):
        """宿主销毁后清空引用，避免页面切换时事件过滤器访问失效 QObject。"""
        self._scroll_area = None
        self._scroll_viewport = None

    @staticmethod
    def _find_scroll_area(args, kwargs):
        """从 QScrollBar 的常规构造参数中取得宿主滚动区域。"""
        parent = kwargs.get("parent")
        if parent is None and len(args) >= 2:
            parent = args[1]
        return parent if isinstance(parent, QAbstractScrollArea) else None

    def _set_revealed(self, revealed):
        revealed = bool(revealed)
        if self._revealed == revealed:
            return
        self._revealed = revealed
        self._set_scrollbar_slot_visible(revealed)
        # 用透明度渐变替代直接 hide/show，避免滚动时滚动条突然闪现或消失。
        self._fade_animation.stop()
        self._fade_animation.setDuration(160 if revealed else 240)
        self._fade_animation.setStartValue(self._opacity_effect.opacity())
        # 预留空间和显示滑块是两套状态；没有滚动范围时，鼠标进入也不能显示滚动条。
        can_scroll = self.maximum() > self.minimum()
        self._fade_animation.setEndValue(1.0 if revealed and can_scroll else 0.0)
        self._fade_animation.start()

    def _set_scrollbar_slot_visible(self, visible):
        """根据显示状态决定是否为滚动条预留布局空间。"""
        if self._scroll_area is None:
            return
        # 保留布局的控件始终占用轨道槽位；列表等不保留的控件仅在显示滑块时占用槽位。
        policy = Qt.ScrollBarAlwaysOn if self._reserve_space or visible else Qt.ScrollBarAlwaysOff
        if self.orientation() == Qt.Vertical:
            self._scroll_area.setVerticalScrollBarPolicy(policy)
        else:
            self._scroll_area.setHorizontalScrollBarPolicy(policy)

    def _handle_range_changed(self, minimum, maximum):
        """滚动范围改变时同步可见状态，避免内容不足时显示空滚动条。"""
        if maximum <= minimum:
            self._set_revealed(False)
        elif self._mouse_inside:
            self._set_revealed(True)

    def _reveal_temporarily(self, *_args):
        if self.maximum() <= self.minimum():
            return
        self._set_revealed(True)
        self._hide_timer.start()

    def _hide_if_idle(self):
        if not self._mouse_inside and not self._dragging:
            self._set_revealed(False)

    def enterEvent(self, event):
        """鼠标移到滚动条上时显示滚动条。"""
        self._mouse_inside = True
        self._hide_timer.stop()
        self._set_revealed(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        """鼠标离开滚动条后延迟隐藏，避免边缘移动造成闪烁。"""
        self._mouse_inside = False
        self._hide_timer.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        """拖动滚动条时保持显示。"""
        self._dragging = event.button() == Qt.LeftButton
        self._reveal_temporarily()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """结束拖动后恢复自动隐藏计时。"""
        self._dragging = False
        super().mouseReleaseEvent(event)
        self._reveal_temporarily()

    def eventFilter(self, watched, event):
        """监听宿主内容区操作，解决滚动条隐藏后无法再次触发显示的问题。"""
        viewport = getattr(self, "_scroll_viewport", None)
        if viewport is not None and watched is viewport and event.type() in (QEvent.Enter, QEvent.MouseMove, QEvent.Wheel):
            self._reveal_temporarily()
        return super().eventFilter(watched, event)
