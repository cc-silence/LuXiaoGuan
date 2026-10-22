"""主页面双栏工作区布局。

主要作用：为下载页和更新页创建一致的根布局、左右面板和内容比例。
输入参数：page 是承载工作区的 QWidget。
返回值：MainPageWorkspace，包含根布局、左右面板布局和右侧面板控件。
副作用：会修改 page 的布局结构，但不创建业务控件、不读取配置、不处理主题或业务状态。
调用限制：调用方负责向 left_layout/right_layout 添加工具栏、表格和 InfoPanel。
"""

from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from modules.ui_layout import apply_page_content_margins


@dataclass
class MainPageWorkspace:
    """主页面双栏工作区的布局部件集合。

    root_layout 是页面根布局；left_layout 和 right_layout 分别承载两侧内容；
    right_panel 是右侧面板控件，页面样式管理器可以继续按页面需要调整它的宽度。
    """

    root_layout: QVBoxLayout
    left_layout: QVBoxLayout
    right_layout: QVBoxLayout
    right_panel: QWidget
    splitter: QSplitter


class _WorkspaceSplitter(QSplitter):
    """根据窗口状态切换信息区宽度，保留手动拖动结果。"""

    def __init__(self, parent):
        super().__init__(Qt.Horizontal, parent)
        self._expanded = None
        self._normal_info_width = 280
        self._expanded_info_width = None
        self._observed_window = None
        self.splitterMoved.connect(self._remember_user_width)

    def _remember_user_width(self, _position, _index):
        """只记录拖动手柄的宽度，自动布局不覆盖用户选择。"""
        if self._expanded:
            self._expanded_info_width = self.sizes()[1]
        else:
            self._normal_info_width = self.sizes()[1]
            self._expanded_info_width = None

    def _sync_window_state(self):
        """最大化或全屏时加宽三分之一，还原时恢复原宽度。"""
        if self.count() != 2:
            return
        expanded = self.window().isMaximized() or self.window().isFullScreen()
        if expanded == self._expanded:
            return
        normal = max(self._normal_info_width, self.widget(1).minimumWidth())
        if expanded:
            target = self._expanded_info_width or round(normal * 4 / 3)
        elif self._expanded is None:
            self._normal_info_width = max(normal, self.sizes()[1])
            self._expanded = False
            return
        else:
            target = normal
        self._expanded = expanded
        available = max(0, self.width() - self.handleWidth())
        self.setSizes([max(0, available - target), target])

    def showEvent(self, event):
        super().showEvent(event)
        window = self.window()
        if window is not self._observed_window:
            if self._observed_window is not None:
                self._observed_window.removeEventFilter(self)
            self._observed_window = window
            window.installEventFilter(self)
        self._sync_window_state()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_window_state()

    def eventFilter(self, watched, event):
        if watched is self._observed_window and event.type() == QEvent.WindowStateChange:
            self._sync_window_state()
        return super().eventFilter(watched, event)


def create_main_page_workspace(page):
    """创建下载页和更新页共用的双栏布局骨架。

    参数：page 是工作区页面 QWidget，作为左右面板的父控件。
    返回值：MainPageWorkspace，供页面继续添加自己的工具栏和内容控件。
    副作用：向 page 设置 QVBoxLayout，并创建左右 QWidget 面板。
    """
    root_layout = QVBoxLayout(page)
    apply_page_content_margins(root_layout)
    root_layout.setSpacing(5)

    content_area = _WorkspaceSplitter(page)
    content_area.setObjectName("mainPageWorkspace")
    content_area.setChildrenCollapsible(False)
    content_area.setHandleWidth(5)

    left_panel = QWidget(page)
    left_layout = QVBoxLayout(left_panel)
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(5)

    right_panel = QWidget(page)
    right_layout = QVBoxLayout(right_panel)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(5)

    left_panel.setMinimumWidth(240)
    right_panel.setMinimumWidth(220)
    content_area.addWidget(left_panel)
    content_area.addWidget(right_panel)
    content_area.setStretchFactor(0, 1)
    content_area.setStretchFactor(1, 0)
    content_area.setSizes([1120, 280])
    content_area.handle(1).setCursor(Qt.SplitHCursor)
    root_layout.addWidget(content_area, 1)

    return MainPageWorkspace(root_layout, left_layout, right_layout, right_panel, content_area)
