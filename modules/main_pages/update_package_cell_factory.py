"""更新页安装包文件名单元格工厂。

主要作用：把 SoftwareUpdatePage 中“更新包文件名/文件时间”两列的控件创建逻辑拆出。
输入参数：调用方传入更新页 page 和单行 row_data。
返回值：无；函数会直接写入表格第 2、3 列。
副作用：只修改表格单元格和 row_data["package_path"]，不读写配置、不删除文件、不执行更新。
调用限制：本模块只负责表格展示和用户选择哪个已下载更新包；不要加入扫描、下载或更新执行逻辑。
"""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTableWidgetItem, QToolButton, QWidget

from modules.icon_utils import get_theme_icon
from modules.main_pages.base_page_styles import context_menu_stylesheet, create_themed_menu


def install_update_package_cells(page, row_data):
    """安装更新包文件名和文件时间单元格。

    参数：page 是 SoftwareUpdatePage 实例；row_data 是当前更新包行数据。
    返回值：无。
    副作用：会更新表格第 2、3 列；多包菜单选择后会同步 row_data["package_path"]。
    """
    table = row_data.get("table")
    row = row_data.get("row")
    packages = row_data.get("packages", [])
    package_count = row_data.get("package_count", 0)

    if package_count > 1 and len(packages) > 1:
        _install_multi_package_cell(page, table, row, row_data, packages)
        return

    package_path = Path(row_data.get("package_path", ""))
    _set_center_item(table, row, 1, package_path.name, tooltip=package_path.name)
    _set_package_time_item(table, row, package_path)


def _install_multi_package_cell(page, table, row, row_data, packages):
    """创建多更新包下拉选择单元格。

    参数：page 提供主题状态；table/row 定位目标单元格；row_data 保存当前选择；packages 是候选包路径列表。
    返回值：无。
    副作用：用户从菜单选择文件后，会更新 row_data["package_path"] 和文件时间列。
    """
    current_name = Path(row_data.get("package_path", "")).name
    menu = create_themed_menu(page, page.light_theme)
    # 这个菜单会挂在表格单元格按钮上长期存在；主题切换后，弹出前必须重新套当前主题 QSS。
    menu.aboutToShow.connect(lambda target_menu=menu, target_page=page: target_menu.setStyleSheet(context_menu_stylesheet(target_page.light_theme)))
    action_group = QActionGroup(menu)
    action_group.setExclusive(True)
    for pkg in packages:
        p = Path(pkg)
        act = menu.addAction(p.name)
        act.setData(str(p))
        act.setCheckable(True)
        act.setChecked(p.name == current_name)
        action_group.addAction(act)

    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(4, 2, 4, 2)
    layout.setSpacing(4)

    name_label = QLabel(current_name)
    name_label.setWordWrap(True)
    name_label.setAlignment(Qt.AlignCenter)
    name_label.setCursor(Qt.PointingHandCursor)
    name_label.setToolTip(current_name)

    arrow_btn = QToolButton()
    arrow_btn.setObjectName("multiFileArrowBtn")
    arrow_btn.setIcon(get_theme_icon(":/icons/images/icons/cil-arrow-bottom.png", page.light_theme))
    arrow_btn.setIconSize(QSize(18, 18))
    arrow_btn.setPopupMode(QToolButton.InstantPopup)
    arrow_btn.setMenu(menu)
    arrow_btn.setAutoRaise(True)
    arrow_btn.setFixedWidth(22)
    arrow_btn.setCursor(Qt.PointingHandCursor)

    # 文件名本身也可点击，扩大可操作区域，保持原有交互。
    def show_menu(evt, target_menu=menu):
        target_menu.exec(evt.globalPos())

    name_label.mousePressEvent = show_menu
    layout.addWidget(name_label, 1)
    layout.addWidget(arrow_btn, 0)
    table.setCellWidget(row, 1, container)

    package_path = Path(row_data.get("package_path", ""))
    _set_package_time_item(table, row, package_path)

    def _on_pkg_chosen(action, rd=row_data, lbl=name_label, tbl=table, rw=row, target_page=page):
        pkg_str = action.data()
        if not pkg_str or pkg_str == rd.get("package_path"):
            return
        action.setChecked(True)
        rd["package_path"] = pkg_str
        lbl.setText(action.text())
        lbl.setToolTip(action.text())
        _set_package_time_item(tbl, rw, Path(pkg_str))
        # 切换更新包时把状态栏重置为就绪，不保留上一次更新的"成功/失败"显示
        if rd.get("status") not in ("ready", "no_plan"):
            rd["status"] = "ready"
            target_page._install_status_cell(rd)

    menu.triggered.connect(_on_pkg_chosen)


def _set_package_time_item(table, row, package_path):
    """设置更新包文件时间列。

    参数：table/row 定位表格行；package_path 是更新包路径。
    返回值：无。
    副作用：只更新第 3 列文本；文件不存在时显示 --。
    """
    mtime = package_path.stat().st_mtime if package_path.exists() else 0
    time_text = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M") if mtime else "--"
    # 直接替换 item，避免旧 item 的状态或绘制缓存导致切换后显示不刷新。
    _set_center_item(table, row, 2, time_text)


def _set_center_item(table, row, column, text, tooltip=""):
    """设置居中文本单元格。

    参数：table/row/column 定位单元格；text 是显示文本；tooltip 是可选完整提示文本。
    返回值：无。
    副作用：会替换指定单元格的 QTableWidgetItem。
    """
    item = QTableWidgetItem(str(text))
    if tooltip:
        item.setToolTip(str(tooltip))
    item.setTextAlignment(Qt.AlignCenter)
    table.setItem(row, column, item)
