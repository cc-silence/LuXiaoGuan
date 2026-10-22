"""原生复选框拖动涂选；只过滤 viewport，不接管普通行拖动。"""

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QPersistentModelIndex, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QAbstractItemView, QStyle, QStyledItemDelegate, QStyleOptionViewItem


class CheckboxPaintController(QObject):
    """附加到 view；can_edit(index) 每次命中调用，on_finished() 每手势调用一次。

    active 期间适配器应延后快照重建/保存，结束回调统一提交。cancel() 结束
    手势并保留已经涂选的值。QObject 和 view._checkbox_paint 共同持有生命周期。
    """

    def __init__(self, view, column=0, can_edit=None, on_finished=None):
        super().__init__(view)
        self.view = view
        self.column = column
        self.can_edit = can_edit
        self.on_finished = on_finished
        self.active = False
        self._seen = set()
        self._edge = False
        self._anchor = QPersistentModelIndex()
        self._model = None
        view._checkbox_paint = self
        view.viewport().installEventFilter(self)
        view.installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self._scrolled)

    def indicator_rect(self, index):
        """使用实际代理和控件样式计算主题/DPI 对应的复选框命中区域。"""
        delegate = self.view.itemDelegateForIndex(index)
        if not isinstance(delegate, QStyledItemDelegate):
            return QRect()
        option = QStyleOptionViewItem()
        self.view.initViewItemOption(option)
        option.widget = self.view
        option.rect = self.view.visualRect(index)
        delegate.initStyleOption(option, index)
        widget = option.widget or self.view
        return widget.style().subElementRect(
            QStyle.SE_ItemViewItemCheckIndicator, option, widget)

    def _allowed(self, index):
        flags = index.flags()
        return (self.view.isEnabled() and index.isValid()
                and bool(flags & Qt.ItemIsEnabled) and bool(flags & Qt.ItemIsUserCheckable)
                and index.data(Qt.CheckStateRole) is not None
                and (self.can_edit is None or self.can_edit(index)))

    def paint_index(self, index):
        """按本手势目标值涂选一次；树适配器可用此入口展开分组成员。"""
        if not self.active:
            return
        persistent = QPersistentModelIndex(index)
        if persistent in self._seen or not self._allowed(index):
            return
        self._seen.add(persistent)
        if Qt.CheckState(index.data(Qt.CheckStateRole)) != Qt.CheckState(self._target):
            index.model().setData(index, self._target, Qt.CheckStateRole)
        if self.active:
            self._anchor = persistent

    def _segment(self, pos):
        """沿可见线段补齐快速跨行及原生滚动新露出的行。"""
        start = self._last
        if self._anchor.isValid():
            start = QPoint(start.x(), self.view.visualRect(QModelIndex(self._anchor)).center().y())
        viewport = self.view.viewport().rect()
        start.setY(max(viewport.top(), min(start.y(), viewport.bottom())))
        end = QPoint(max(viewport.left(), min(pos.x(), viewport.right())),
                     max(viewport.top(), min(pos.y(), viewport.bottom())))
        distance = max(abs(end.x() - start.x()), abs(end.y() - start.y()), 1)
        for step in range(distance + 1):
            hit = start + (end - start) * (step / distance)
            index = self.view.indexAt(hit)
            column = self.column(index) if callable(self.column) else self.column
            if index.column() == column:
                self.paint_index(index)
            if not self.active:
                break
        self._last = pos

    def _scrolled(self, _value):
        if self.active:
            self._segment(self._last)

    def cancel(self, *args):
        """结束当前手势、停止 Qt 原生滚动并恢复原排序和视图状态。"""
        if not self.active:
            return
        self.active = False
        self.view.stopAutoScroll()
        self.view.setState(self._state)
        self._edge = False
        self._seen.clear()
        self._model.modelAboutToBeReset.disconnect(self.cancel)
        self._model.rowsAboutToBeRemoved.disconnect(self.cancel)
        self._model = None
        if self._sorting is not None:
            self.view.setSortingEnabled(self._sorting)
        if self.on_finished is not None:
            self.on_finished()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Hide, QEvent.UngrabMouse, QEvent.EnabledChange,
                    QEvent.WindowDeactivate, QEvent.Leave):
            self.cancel()
        if kind == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.cancel()
        if watched is self.view:
            return False
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            index = self.view.indexAt(event.position().toPoint())
            column = self.column(index) if callable(self.column) else self.column
            if (not index.isValid() or index.column() != column
                    or not self.indicator_rect(index).contains(event.position().toPoint())):
                return False
            if not self._allowed(index):
                return True
            self.active = True
            self._state = self.view.state()
            self._sorting = (self.view.isSortingEnabled()
                             if hasattr(self.view, 'isSortingEnabled') else None)
            self._model = self.view.model()
            self._model.modelAboutToBeReset.connect(self.cancel)
            self._model.rowsAboutToBeRemoved.connect(self.cancel)
            if self._sorting:
                self.view.setSortingEnabled(False)
            self._target = (0 if Qt.CheckState(index.data(Qt.CheckStateRole)) == Qt.Checked else 2)
            self._last = event.position().toPoint()
            self.paint_index(index)
            return True
        if not self.active:
            return False
        if kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self.cancel()
            return True
        if kind == QEvent.MouseMove:
            if not event.buttons() & Qt.LeftButton or self.view.model() is not self._model:
                self.cancel()
                return True
            pos = event.position().toPoint()
            self._segment(pos)
            if not self.active:
                return True
            viewport = self.view.viewport().rect()
            # 无按钮事件只更新 Qt 私有 draggedPosition；真实定时滚动仍由 Qt 执行。
            self.view.setState(QAbstractItemView.DragSelectingState)
            move = QMouseEvent(QEvent.MouseMove, event.position(), event.globalPosition(),
                               Qt.NoButton, Qt.NoButton, event.modifiers())
            QAbstractItemView.mouseMoveEvent(self.view, move)
            margin = self.view.autoScrollMargin()
            edge = self.view.hasAutoScroll() and not viewport.adjusted(margin, margin, -margin, -margin).contains(pos)
            if edge and not self._edge:
                self.view.startAutoScroll()
            elif not edge:
                self.view.stopAutoScroll()
            self._edge = edge
            return True
        if kind == QEvent.MouseButtonDblClick:
            return True
        return False
