"""更新页显示位置标签和表格重建辅助函数。

主要作用：集中清理并重建软件更新页的“全部软件”和自定义显示位置标签。
输入参数：更新页 page。
返回值：无。
副作用：会替换 tab_bar/table_stack 中的标签和表格，并更新 page.tables 等界面状态；不写配置。
调用限制：这里只管理标签和空表格骨架，不扫描更新包、不追加行、不执行更新。
"""

from modules.main_pages.update_table_factory import create_update_table
from modules.update_services.update_settings_manager import ALWAYS_SOFTWARE_ID, ALWAYS_SOFTWARE_NAME


def rebuild_update_tabs(page):
    """按当前更新设置重建显示位置标签和对应空表格。

    参数：page 是 SoftwareUpdatePage 实例。
    返回值：无。
    副作用：清理旧表格和标签，更新 position_items/tab_position_ids/tables，并选择首个标签。
    """
    _clear_position_tabs(page)
    page.tab_buttons = []
    page.tab_position_ids = []
    page.tables = {}

    page.position_items = page.update_settings_manager.enabled_positions(host_mode=page.host_mode)
    for item in page.position_items:
        position_id = item.get("id", "host")
        _append_position_table(page, position_id, item.get("name", position_id))

    # 内置“全部软件”汇总标签固定放在最后，避免它抢占用户最常用的模式位置。
    _append_position_table(page, ALWAYS_SOFTWARE_ID, ALWAYS_SOFTWARE_NAME)

    first_id = page.tab_position_ids[0] if page.tab_position_ids else ALWAYS_SOFTWARE_ID
    page._select_position(first_id)


def _append_position_table(page, position_id, label):
    """追加一个显示位置标签和对应空表格。

    参数：page 是更新页；position_id 是显示位置 id；label 是用户可见标签名。
    返回值：无。
    副作用：修改 tab_bar、tab_position_ids、tables 和 table_stack。
    """
    page.tab_bar.addTab(label)
    page.tab_position_ids.append(position_id)
    table = create_update_table(page)
    page.tables[position_id] = table
    page.table_stack.addWidget(table)


def _clear_position_tabs(page):
    """清空更新页旧表格和旧显示位置标签。

    参数：page 是 SoftwareUpdatePage 实例。
    返回值：无。
    副作用：从 table_stack/tab_bar 移除旧控件并安排 deleteLater；不读写配置。
    """
    while page.table_stack.count():
        widget = page.table_stack.widget(0)
        page.table_stack.removeWidget(widget)
        widget.deleteLater()

    page.tab_bar.blockSignals(True)
    while page.tab_bar.count():
        page.tab_bar.removeTab(0)
    page.tab_bar.blockSignals(False)
