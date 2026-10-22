"""更新页状态列控件工厂。

主要作用：把 SoftwareUpdatePage 的状态列按钮/标签创建逻辑拆出来，降低页面类职责。
输入参数：调用方传入更新页 page 和单行 row_data。
返回值：无；函数会把创建好的状态控件安装到表格第 4 列。
副作用：只修改表格单元格控件，不读写配置、不执行更新、不删除文件。
调用限制：这里仍依赖 page._update_actions、page.start_update 等页面入口；它只负责 UI 组装，
不要在本模块里加入更新执行、文件删除或线程逻辑。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from modules.common_widgets.sweep_button import SweepButton
from modules.main_pages.base_page_styles import mini_button_size


def install_update_status_cell(page, row_data):
    """根据更新状态创建并安装状态列控件。

    参数：page 是 SoftwareUpdatePage 实例；row_data 是当前更新包行数据。
    返回值：无。
    副作用：调用 table.setCellWidget(row, 3, container) 替换状态列控件；不会启动更新。
    """
    table = row_data.get("table")
    row = row_data.get("row")
    status = row_data.get("status")
    container = QFrame(table)
    container.setObjectName("updateStatusCell")
    layout = QHBoxLayout(container)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)

    if status == "no_plan":
        label_btn = _mini_button(page, "无更新方案", None)
        label_btn.setEnabled(False)
        btn = _mini_button(
            page,
            "打开下载目录",
            lambda checked=False, rd=row_data: page._update_actions.open_software_dir(rd),
        )
        layout.addWidget(label_btn)
        layout.addWidget(btn)
    elif status == "queued":
        status_btn = _mini_button(page, "等待中", None)
        status_btn.setEnabled(False)
        layout.addWidget(status_btn, 1)
        layout.addWidget(_mini_button(page, "取消排队", lambda checked=False, rd=row_data: page.cancel_queued_update(rd)))
    elif status == "checking_processes":
        status_btn = _mini_button(page, "正在检查更新环境...", None)
        status_btn.set_guided_mode(True)
        status_btn.setEnabled(False)
        layout.addWidget(status_btn, 1)
    elif status == "preparing":
        status_btn = _mini_button(page, "正在关闭程序...", None)
        status_btn.set_guided_mode(True)
        status_btn.setEnabled(False)
        layout.addWidget(status_btn, 1)
    elif status == "updating":
        status_btn = _mini_button(page, "更新中...", None)
        status_btn.set_guided_mode(True)
        status_btn.setEnabled(False)
        layout.addWidget(status_btn, 1)
        if page.can_cancel_update(row_data):
            stop_btn = _mini_button(
                page,
                "停止更新",
                lambda checked=False, rd=row_data: page.cancel_update(rd),
            )
            stop_btn.setProperty("downloadStatus", "download_failed")
            layout.addWidget(stop_btn)
    elif status == "success":
        restart_exe = row_data.get("node", {}).get("update", {}).get("restart_exe", "")
        restart_path = page.software_manager.resolve_path(restart_exe) if restart_exe else None
        has_restart = restart_path and restart_path.is_file()
        if has_restart:
            btn = _mini_button(
                page,
                "更新完成 重启软件",
                lambda checked=False, rd=row_data: page._update_actions.launch_restart_exe(rd),
            )
            layout.addWidget(btn, 1)
        else:
            layout.addWidget(_status_label(page, "更新成功", status, table))
        btn2 = _mini_button(
            page,
            "删除安装包",
            lambda checked=False, rd=row_data: page._update_actions.delete_package(rd),
        )
        layout.addWidget(btn2)
    elif status == "failed":
        retry_btn = _mini_button(
            page,
            "更新失败，重新尝试",
            lambda checked=False, rd=row_data: page.start_update(rd),
        )
        retry_btn.setProperty("downloadStatus", "download_failed")
        layout.addWidget(retry_btn, 1)
        log_btn = _mini_button(
            page,
            "打开日志",
            lambda checked=False, rd=row_data: page.open_update_log_folder(rd),
        )
        log_btn.setToolTip("打开当前软件最近一次更新日志所在的文件夹。")
        layout.addWidget(log_btn)
    else:
        btn = _mini_button(page, "更新", lambda checked=False, rd=row_data: page.start_update(rd))
        layout.addWidget(btn, 1)

    table.setCellWidget(row, 3, container)


def _status_label(page, text, status, table):
    """创建更新状态文本标签。

    参数：page 提供缩放管理器；text/status 控制显示文字和 QSS 属性；table 是父控件。
    返回值：QLabel。
    副作用：只创建控件，不连接信号。
    """
    label = QLabel(text, table)
    label.setObjectName("updateStatusLabel")
    label.setAlignment(Qt.AlignCenter)
    label.setProperty("downloadStatus", status)
    height = mini_button_size(page.scale_manager)
    label.setMinimumHeight(height)
    label.setMaximumHeight(height)
    return label


def _mini_button(page, text, callback):
    """创建更新状态列里的小文字按钮。

    参数：page 提供主题状态和父控件；text 是按钮文字；callback 是点击回调，可为空。
    返回值：SweepButton。
    副作用：callback 非空时连接 clicked 信号，但不会主动执行回调。
    """
    btn = SweepButton(text, page)
    btn.setObjectName("updateActionButton")
    btn.set_light_theme(page.light_theme)
    btn.set_hover_sweep(False)
    btn.setCursor(Qt.PointingHandCursor)
    if callback:
        btn.clicked.connect(callback)
    return btn
