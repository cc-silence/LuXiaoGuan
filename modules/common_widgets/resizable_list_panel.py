"""列表与表格的紧凑高度容器；只保存显式偏好，不读取隐藏页几何。"""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget


class ResizableListPanel(QWidget):
    """接管 view 的父对象；set_panel_height 回显，heightChanged 在拖动结束发出。"""

    heightChanged = Signal(int)

    def __init__(self, view, parent=None, *, height=240, minimum_height=240):
        super().__init__(parent)
        self.view = view
        self.minimum_panel_height = max(1, min(2000, int(minimum_height)))
        self.setObjectName("resizableListPanel")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(view)
        self.handle = _ListResizeHandle(self)
        layout.addWidget(self.handle)
        self.set_panel_height(height)

    @property
    def panel_height(self):
        """返回显式高度（不含手柄）；None 表示随布局伸展。"""
        return self._panel_height

    def set_panel_height(self, height):
        """按本实例最小高度到 2000 静默回显；None 随布局填充。"""
        if height is None:
            self._panel_height = None
            self.view.setMinimumHeight(self.minimum_panel_height)
            self.view.setMaximumHeight(16777215)
            self.setMinimumHeight(self.minimum_panel_height + self.handle.height())
            self.setMaximumHeight(16777215)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            return
        try:
            height = int(height)
        except (TypeError, ValueError, OverflowError):
            height = 240
        self._panel_height = max(self.minimum_panel_height, min(2000, height))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.view.setFixedHeight(self._panel_height)
        self.setFixedHeight(self._panel_height + self.handle.height())


class _ListResizeHandle(QWidget):
    """由原字典手柄提取；全局鼠标坐标避免布局移动造成拖动跳变。"""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self._origin = None
        self.setObjectName("resizableListHandle")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(7)
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip("上下拖动调整列表高度")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        grip = QWidget(self)
        grip.setObjectName("resizableListGrip")
        grip.setAttribute(Qt.WA_StyledBackground, True)
        grip.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        grip.setFixedSize(18, 2)
        for index in range(5):
            dot = QWidget(grip)
            dot.setObjectName("resizableListGripDot")
            dot.setAttribute(Qt.WA_StyledBackground, True)
            dot.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            dot.setGeometry(index * 4, 0, 2, 2)
        layout.addWidget(grip, 0, Qt.AlignCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            preference = self.panel.panel_height
            self._origin = (event.globalPosition().y(), self.panel.view.height() if preference is None else preference, preference)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._origin is not None:
            y, height, _preference = self._origin
            self.panel.set_panel_height(height + event.globalPosition().y() - y)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._origin is not None:
            _, _height, preference = self._origin
            self._origin = None
            if preference != self.panel.panel_height:
                self.panel.heightChanged.emit(self.panel.panel_height)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def event(self, event):
        if event.type() in (QEvent.Hide, QEvent.EnabledChange, QEvent.UngrabMouse,
                            QEvent.WindowDeactivate) and self._origin is not None:
            _, _height, preference = self._origin
            self._origin = None
            self.panel.set_panel_height(preference)
        result = super().event(event)
        if event.type() in (QEvent.Enter, QEvent.Leave):
            # Qt 不会随祖先的 hover 状态自动刷新子控件的 QSS。
            for dot in self.findChildren(QWidget, "resizableListGripDot"):
                dot.setProperty("gripHovered", event.type() == QEvent.Enter)
                dot.style().unpolish(dot)
                dot.style().polish(dot)
                dot.update()
        return result
