"""更新主线程预检配置同步函数。

主要作用：把签名确认、目标目录确认产生的临时配置修改同步回 row_data，供 UpdateWorker 读取。
输入参数：更新页行数据 row_data 和标准化后的 update_config。
返回值：同步成功返回 True，row_data 中没有有效 node 字典时返回 False。
副作用：会修改内存中的 row_data["node"]["update"]；不会保存 software_items.json。
调用限制：只同步本次更新运行时配置，不能用它代替用户在设置窗口中的持久化保存。
"""


def sync_preflight_update_config(row_data, update_config):
    """把主线程预检后的更新配置写回当前行节点。

    参数：row_data 是更新页行数据；update_config 是本次更新使用的配置字典。
    返回值：写回成功返回 True，否则返回 False。
    副作用：原地修改 row_data 中的软件节点，使 worker 能读取本次确认结果。
    """
    node = row_data.get("node", {})
    if not isinstance(node, dict):
        return False
    node["update"] = update_config
    return True
