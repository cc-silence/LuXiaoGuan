"""更新页表格行构建器。

主要作用：把 SoftwareUpdatePage 的单行表格组装逻辑拆出，页面类只负责扫描和状态编排。
输入参数：调用方传入更新页 page 和 row_data。
返回值：无；函数会把 row_data 添加到对应表格。
副作用：会修改表格行、写入 row_data["table"] 和 row_data["row"]；不读写配置、不执行更新。
调用限制：这里只负责 UI 行组装，不处理扫描、更新策略、删除安装包或 worker 线程。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

from modules.main_pages.update_icon_helper import themed_update_software_icon
from modules.main_pages.update_package_cell_factory import install_update_package_cells


def append_update_row(page, row_data):
    """把一条更新包记录追加到当前显示位置对应的表格。

    参数：page 是 SoftwareUpdatePage 实例；row_data 是当前更新包行数据。
    返回值：无。
    副作用：插入表格行，并安装软件名、更新包和状态列控件。
    """
    table = page.tables.get(row_data.get("position_id"))
    if table is None:
        return

    row = table.rowCount()
    table.insertRow(row)
    row_data["table"] = table
    row_data["row"] = row

    name_item = QTableWidgetItem(row_data.get("name", ""))
    name_item.setIcon(themed_update_software_icon(row_data.get("node", {}), page.software_manager, page.light_theme))
    name_item.setTextAlignment(Qt.AlignCenter)
    notes = str(row_data.get("node", {}).get("notes", "") or "").strip()
    if notes:
        name_item.setToolTip(notes)
    table.setItem(row, 0, name_item)

    install_update_package_cells(page, row_data)
    page._install_status_cell(row_data)
    table.setRowHeight(row, 54)
