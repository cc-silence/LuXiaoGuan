"""更新页面上下文操作控制器。

主要作用：集中管理更新页面里的文件目录操作、右键菜单和编辑更新方案弹窗。
包括创建/打开下载目录和日志目录、删除安装包、启动更新后的 EXE、右键菜单、编辑方案弹窗。

使用方式：SoftwareUpdatePage 创建 UpdateContextActions(page) 实例，
原页面方法保留为委托。
"""

import subprocess
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication

from modules.main_pages.base_page_styles import create_themed_menu

from modules.common_widgets.common_dialogs import ConfirmDialog, create_themed_confirm_dialog
from modules.download_services.download_paths import software_download_directory
from modules.dialog_theme import build_current_base_stylesheet
from modules.main_pages.software_item_edit_coordinator import (
    STATUS_CANCELLED,
    STATUS_MISSING_FACTORY,
    STATUS_MISSING_ID,
    STATUS_MISSING_NODE,
    STATUS_REPLACE_FAILED,
    STATUS_SAVED,
    edit_software_item,
)


class UpdateContextActions:
    """更新页面上下文操作控制器。

    输入参数：page 是 SoftwareUpdatePage 实例。
    副作用：会创建 QMenu、ConfirmDialog、SoftwareItemDialog 等模态弹窗，
    会删除安装包文件、创建目录、打开资源管理器、修改配置文件等。
    """

    def __init__(self, page, software_item_dialog_factory=None):
        self._page = page
        self._software_item_dialog_factory = software_item_dialog_factory

    def launch_restart_exe(self, row_data):
        """用户点击「更新完成重启」后，启动配置的 EXE 文件。"""
        restart_exe = row_data.get("node", {}).get("update", {}).get("restart_exe", "")
        if not restart_exe:
            return
        exe_path = self._page.software_manager.resolve_path(restart_exe)
        if not exe_path.is_file():
            self._page._append_info(f"重启程序不存在：{exe_path}", level="error")
            return
        try:
            subprocess.Popen([str(exe_path)])
            self._page._append_info(f"已启动：{exe_path.name}")
        except Exception as exc:
            self._page._append_info(f"启动失败：{exc}", level="error")

    def delete_package(self, row_data):
        """删除指定安装包文件。"""
        package_path = Path(row_data.get("package_path", ""))
        if not package_path.is_file():
            self._page._append_info(f"安装包不存在：{package_path}", level="warning")
            self._page.scan_packages()
            return
        dialog = create_themed_confirm_dialog(
            "删除安装包",
            f"确认删除这个安装包？\n\n{package_path}",
            self._page,
            build_current_base_stylesheet(self._page.config_manager, self._page.light_theme, self._page.scale_manager),
        )
        if dialog.exec() != ConfirmDialog.Accepted:
            return
        try:
            package_path.unlink()
            self._page._append_info(f"已删除安装包：{package_path.name}")
            self._page.scan_packages()
        except OSError as exc:
            self._page._append_info(f"删除安装包失败：{exc}", level="error")

    def create_directories(self):
        """为所有启用的软件项创建对应的下载/离线更新目录。"""
        messages = self._page.scanner.create_software_directories()
        if not messages:
            self._page._append_info("没有启用的软件项需要创建目录。", level="warning")
            return
        for item in messages:
            prefix = "已创建" if item["status"] == "created" else "已存在"
            self._page._append_info(f"{prefix}：{item['name']} -> {item['directory']}")
            if item.get("renamed"):
                self._page._append_info(f"原名称：{item['name']}，实际目录：{item['safe_name']}", level="warning")

    def open_download_root(self):
        """在资源管理器中打开统一下载根目录。"""
        from modules.platform_services.windows_shortcuts import open_folder_in_explorer
        root = self._page.scanner.download_root()
        open_folder_in_explorer(root)

    def open_software_dir(self, row_data):
        """在资源管理器中打开软件下载目录。"""
        try:
            from modules.platform_services.windows_shortcuts import open_folder_in_explorer
            node = row_data.get("node", {})
            name = node.get("name", "未命名软件")
            directory = software_download_directory(self._page.download_manager, name)
            open_folder_in_explorer(directory)
            self._page._append_info(f"已打开目录：{directory}")
        except Exception as exc:
            self._page._append_info(f"打开目录失败：{exc}", level="error")

    def open_update_log_folder(self, row_data):
        """打开当前软件最近一次更新日志所在的文件夹。

        参数：row_data 是右键菜单对应的软件行数据。返回值：无。
        副作用：委托更新页面启动资源管理器；日志不存在时由页面显示可读错误提示。
        """
        self._page.open_update_log_folder(row_data)

    def edit_update_plan(self, row_data):
        """打开软件下载项编辑窗口，默认展开更新方案栏。"""
        node = row_data.get("node", {})
        software_id = node.get("id", "")
        status, result_data = edit_software_item(
            self._page.software_manager,
            software_id,
            self._software_item_dialog_factory,
            self._page,
            build_current_base_stylesheet(
                self._page.config_manager,
                self._page.light_theme,
                self._page.scale_manager,
            ),
            self._expand_update_section,
        )
        if status == STATUS_MISSING_ID:
            self._page._append_info("无法编辑：当前软件缺少 ID。", level="error")
            return
        if status == STATUS_MISSING_NODE:
            self._page._append_info("无法编辑：配置文件里找不到这个下载项。", level="error")
            return
        if status == STATUS_MISSING_FACTORY:
            self._page._append_info("无法编辑：未配置下载项编辑窗口。", level="error")
            return
        if status == STATUS_CANCELLED:
            return
        if status == STATUS_REPLACE_FAILED:
            self._page._append_info("保存失败：配置文件里找不到这个下载项。", level="error")
            return
        if status != STATUS_SAVED:
            self._page._append_info("保存失败：下载项编辑流程异常。", level="error")
            return
        self._page.scan_packages()
        self._page._append_info(f"{result_data.get('name', '下载项')}：更新方案已保存。", level="success")

    @staticmethod
    def _expand_update_section(dialog):
        """将下载项编辑弹窗切换到更新方案区块。

        参数：dialog 是由主窗口工厂创建的软件下载项编辑弹窗。
        返回值：无。
        副作用：仅修改弹窗折叠区块的显示状态，不保存配置。
        """
        dialog.basic_section.set_expanded(False)
        dialog.update_section.set_expanded(True)
        dialog.version_section.set_expanded(False)

    def show_context_menu(self, table, pos):
        """显示更新包右键菜单。"""
        row = table.rowAt(pos.y())
        if row < 0:
            return
        row_data = None
        for data in self._page.rows:
            if data.get("table") is table and data.get("row") == row:
                row_data = data
                break
        if row_data is None:
            return
        menu = create_themed_menu(self._page, self._page.light_theme)
        open_dir = QAction("打开软件目录", menu)
        open_log = QAction("打开日志文件夹", menu)
        copy_path = QAction("复制更新包路径", menu)
        delete_pkg = QAction("删除下载包", menu)
        edit_plan = QAction("编辑更新方案", menu)
        rescan = QAction("重新扫描", menu)
        open_dir.triggered.connect(lambda: self.open_software_dir(row_data))
        open_log.triggered.connect(lambda: self.open_update_log_folder(row_data))
        copy_path.triggered.connect(lambda: QApplication.clipboard().setText(str(row_data.get("package_path", ""))))
        delete_pkg.triggered.connect(lambda: self.delete_package(row_data))
        edit_plan.triggered.connect(lambda: self.edit_update_plan(row_data))
        rescan.triggered.connect(self._page.scan_packages)
        menu.addAction(open_dir)
        menu.addAction(open_log)
        menu.addAction(copy_path)
        menu.addAction(delete_pkg)
        menu.addSeparator()
        menu.addAction(edit_plan)
        menu.addSeparator()
        menu.addAction(rescan)
        menu.exec(table.viewport().mapToGlobal(pos))
