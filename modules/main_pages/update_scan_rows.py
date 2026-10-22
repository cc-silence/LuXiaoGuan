"""更新页扫描记录到表格行数据的转换函数。

主要作用：把 UpdatePackageScanner 返回的记录转换成 SoftwareUpdatePage 表格使用的 row_data。
输入参数：扫描记录、有效显示位置集合、默认显示位置和状态文本。
返回值：包含显示位置行、全部软件行和主索引行的普通 dict。
副作用：无；不修改表格、不读写配置、不执行更新。
调用限制：这里只做数据转换，不处理 QTableWidget、按钮、菜单或 worker 线程。
"""

from modules.update_services.update_settings_manager import ALWAYS_SOFTWARE_ID


def build_update_scan_rows(record, valid_positions, default_position, ready_status, no_plan_status):
    """把单条扫描记录转换为更新页需要的多行数据。

    参数：record 是 UpdateScanRecord（position_ids 为空表示从未明确选择位置）；
    valid_positions 是当前启用显示位置 id 集合；default_position 只给无明确选择
    的老配置兜底；ready_status/no_plan_status 是页面状态常量值。
    返回值：dict，包含 rows（具体显示位置行）、all_row（全部软件行）、primary_row（row_by_id 主索引行）。
    副作用：无。
    有明确选择但本模式都不可见时，只保留“全部软件”行，不回退到默认标签，
    避免没勾选的位置仍显示该软件。
    """
    explicit_ids = list(record.position_ids or [])
    if not explicit_ids:
        position_ids = [default_position]
    else:
        position_ids = [pid for pid in explicit_ids if pid in valid_positions]
    base_row_data = _base_row_data(record, ready_status, no_plan_status)

    rows = []
    first_row = None
    for position_id in position_ids:
        row_data = dict(base_row_data)
        row_data["position_id"] = position_id
        rows.append(row_data)
        if first_row is None:
            first_row = row_data

    # “全部软件”标签每个软件只追加一行，避免同一软件按多个显示位置重复出现。
    all_row = dict(base_row_data)
    all_row["position_id"] = ALWAYS_SOFTWARE_ID

    return {
        "rows": rows,
        "all_row": all_row,
        "primary_row": first_row or base_row_data,
    }


def _base_row_data(record, ready_status, no_plan_status):
    """创建不含显示位置的基础行数据。

    参数：record 是 UpdateScanRecord；ready_status/no_plan_status 是页面状态常量值。
    返回值：row_data 基础字典。
    副作用：无。
    """
    return {
        "id": record.software_id,
        "name": record.name,
        "node": record.node,
        "directory": record.directory,
        "package_path": record.package_path,
        "packages": record.packages,
        "package_count": record.package_count,
        "has_update_plan": record.has_update_plan,
        "status": ready_status if record.has_update_plan else no_plan_status,
    }
