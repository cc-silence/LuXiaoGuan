"""更新页表格控件工厂。

主要作用：集中创建软件更新页的 QTableWidget，避免页面类直接维护列设置和基础交互细节。
输入参数：更新页 page，用作父控件并提供右键菜单处理器。
返回值：配置完成的 QTableWidget。
副作用：只创建控件并连接右键菜单信号，不扫描目录、不修改配置、不执行更新。
调用限制：这里只负责表格骨架；行内容由 update_row_builder.py 负责。
"""

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget

from modules.common_widgets.left_resize_header import LeftResizeHeaderView
from modules.common_widgets.auto_hide_scroll_bar import AutoHideScrollBar
from modules.main_pages.base_page_styles import font_family, font_points


def create_update_table(page):
    """创建软件更新页表格。

    参数：page 是 SoftwareUpdatePage 实例。
    返回值：QTableWidget。
    副作用：连接 customContextMenuRequested 到页面的更新上下文操作控制器。
    """
    table = QTableWidget(page)
    table.setObjectName("softwareUpdateTable")
    table.setAttribute(Qt.WA_StyledBackground, True)
    table.setColumnCount(4)
    table.setHorizontalHeaderLabels(["软件", "更新包文件名", "文件时间", "状态"])
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setFocusPolicy(Qt.NoFocus)
    table.setVerticalScrollBar(AutoHideScrollBar(Qt.Vertical, table))
    table.setHorizontalScrollBar(AutoHideScrollBar(Qt.Horizontal, table))
    table.setContextMenuPolicy(Qt.CustomContextMenu)
    # 更新页软件图标使用 QTableWidgetItem 图标；适当放大，视觉上接近下载页 20px QLabel 图标。
    table.setIconSize(QSize(26, 26))
    table.customContextMenuRequested.connect(
        lambda pos, tbl=table: page._update_actions.show_context_menu(tbl, pos)
    )

    # 软件和更新包文件名可单独调整；状态列调整时两列共同重新分配空间。
    header = LeftResizeHeaderView(
        Qt.Horizontal,
        3,
        (0, 1),
        {0: 160, 1: 160, 3: 220},
        table,
    )
    table.setHorizontalHeader(header)
    header.setMinimumSectionSize(60)
    header.setSectionsMovable(False)
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.Interactive)
    header.setSectionResizeMode(1, QHeaderView.Interactive)
    header.setSectionResizeMode(2, QHeaderView.Fixed)
    header.setSectionResizeMode(3, QHeaderView.Interactive)
    table.setWordWrap(True)
    table.setColumnWidth(0, 170)
    table.setColumnWidth(1, 300)
    table.setColumnWidth(2, 90)
    table.setColumnWidth(3, 220)

    def refit_status_column(_logical_index, _old_size, _new_size):
        """普通列拖动结束后，让状态列重新贴住表格右边缘。

        自动贴边自身也会触发 sectionResized，因此 fitting 期间必须跳过，避免一次
        用户拖动递归排队多次重算。QTimer 保证 Qt 已完成本轮表头布局后再读取视口宽度。
        """
        if not header.is_fitting_viewport:
            QTimer.singleShot(0, header.fit_to_viewport)

    header.sectionResized.connect(refit_status_column)
    page._table_layout.configure_table(table)
    # 表格刚被加入 QStackedWidget 时尚未获得最终宽度；延迟一轮事件循环后首次贴边。
    QTimer.singleShot(0, header.fit_to_viewport)
    # 显式设置表格字体，避免不同标签页切换时 Qt 字体继承不一致导致字号差异。
    _apply_table_font(table, page)
    return table


def _apply_table_font(table, page):
    """为更新页表格设置显式字体，确保所有标签页字号一致。

    不设置字体时，Qt 会通过 QWidget 继承链获取默认字体；但 QStackedWidget
    中隐藏的表格在首次显示时可能继承到不一致的字体，导致不同标签页字号不同。
    """
    scale = page.scale_manager
    family = font_family(scale)
    _, _, body_pt = font_points(scale)
    table_font = QFont(family)
    table_font.setPointSize(body_pt)
    table.setFont(table_font)
