"""下载页右键菜单处理器

主要作用：集中管理软件下载页的右键菜单、双击编辑和从主页面编辑下载项配置，
让 SoftwareDownloadPage 不继续承载菜单构建和配置树节点替换细节。

使用方式：页面创建 DownloadContextMenuHandler(page) 实例，页面保留同名委托方法。
此模块会在用户保存下载项编辑窗口后写入 software_items.json，并刷新下载页列表。
"""

from PySide6.QtCore import QEvent, QObject, Qt
from modules.main_pages.base_page_styles import create_themed_menu
from modules.main_pages.download.download_page_utils import network_channels
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
from .download_status import STATUS_NO_UPDATE


class CategoryArrowClickFilter(QObject):
    """只处理下载分类行左侧展开箭头的单击。

    参数：page 是 SoftwareDownloadPage 实例，用于读取表格、分类行数据和切换展开状态。
    返回值：无。命中箭头时直接切换分类，并拦截该鼠标事件；其他区域保持表格默认行为。
    副作用：会修改分类的展开状态，但不会修改配置文件。
    调用限制：分类组合图标中箭头位于左侧 10/50 区域，热区宽度必须小于文件夹图标起点。
    """

    _ARROW_WIDTH_RATIO = 10 / 50
    _ARROW_HIT_PADDING = 6

    def __init__(self, page):
        super().__init__(page.table.viewport())
        self._page = page
        # 保留视口包装对象，销毁阶段无需重新访问可能已释放的 table。
        self._viewport = page.table.viewport()

    def eventFilter(self, watched, event):
        """拦截箭头热区的单击，避免分类整行单击都触发展开。"""
        if watched is not self._viewport or event.type() not in (
            QEvent.MouseButtonPress,
            QEvent.MouseButtonDblClick,
        ):
            return super().eventFilter(watched, event)
        if event.button() != Qt.LeftButton:
            return super().eventFilter(watched, event)

        page = self._page
        position = event.position().toPoint()
        index = page.table.indexAt(position)
        if not index.isValid():
            return super().eventFilter(watched, event)
        item = page.table.item(index.row(), 0)
        data = item.data(Qt.UserRole) if item else None
        if not isinstance(data, dict) or data.get("type") != "category":
            return super().eventFilter(watched, event)
        if not self._is_arrow_hit(index, position.x()):
            return super().eventFilter(watched, event)

        # 双击箭头时首个按下事件已经切换，继续拦截双击事件，避免又切换回原状态。
        if event.type() == QEvent.MouseButtonPress:
            page.toggle_category(data.get("category", ""))
        return True

    def _is_arrow_hit(self, index, click_x):
        """判断横坐标是否落在分类组合图标左侧的箭头热区。

        图标画布为 50px 宽，文件夹从第 22px 开始绘制。这里按当前 iconSize
        换算箭头宽度并留出少量点击余量，确保 DPI 缩放后仍不会误命中文件夹图标。
        """
        icon_width = self._page.table.iconSize().width()
        arrow_width = round(icon_width * self._ARROW_WIDTH_RATIO) + self._ARROW_HIT_PADDING
        cell_left = self._page.table.visualRect(index).left()
        return cell_left <= click_x < cell_left + arrow_width


class DownloadContextMenuHandler:
    """下载页右键菜单处理器。

    输入参数：page 是 SoftwareDownloadPage 实例，处理器通过它调用检测、下载、队列、
    暂停、取消、打开目录和写信息框等页面入口。
    副作用：edit_download_item 会保存配置文件并触发页面 reload_items。
    """

    def __init__(self, page, software_item_dialog_factory=None):
        self._page = page
        self._software_item_dialog_factory = software_item_dialog_factory

    def show_context_menu(self, pos):
        """按当前点击位置显示分类、多选或单个软件下载项菜单。"""
        page = self._page
        item = page.table.itemAt(pos)
        if item is None:
            return
        data = item.data(Qt.UserRole) or {}
        selected = self.selected_software_rows()
        menu = create_themed_menu(page, page.light_theme)

        if data.get("type") == "category":
            category = data.get("category", "")
            menu.addAction("展开/收起分类", lambda: page.toggle_category(category))
            menu.addSeparator()
            menu.addAction("检测本分类", lambda cat=category: page._download_coordinator.check_category(cat))
            menu.addAction("下载本分类", lambda cat=category: page._download_coordinator.download_category(cat))
            menu.exec(page.table.viewport().mapToGlobal(pos))
            return

        if len(selected) > 1:
            menu.addAction("检测选中软件", lambda sel=selected: page._download_coordinator.check_selected(sel))
            menu.addAction("下载选中软件", lambda sel=selected: page._download_coordinator.download_selected(sel))
            menu.addSeparator()
            menu.addAction("暂停选中下载", lambda sel=selected: page._download_coordinator.pause_selected(sel))
            menu.addAction("继续选中下载", lambda sel=selected: page._download_coordinator.resume_selected(sel))
            menu.addAction("取消选中下载", lambda sel=selected: page._download_coordinator.cancel_selected(sel))
            menu.exec(page.table.viewport().mapToGlobal(pos))
            return

        row_data = data if data.get("type") == "software" else None
        if row_data is None:
            return
        self.add_single_actions(menu, row_data)
        menu.exec(page.table.viewport().mapToGlobal(pos))

    def add_single_actions(self, menu, row_data):
        """为单个软件下载项追加检测、下载、复制链接和编辑动作。"""
        page = self._page
        sid = row_data["node"].get("id", "")
        network_allowed = bool(network_channels(row_data, page.config_manager))
        check_action = menu.addAction("检测更新")
        check_action.setEnabled(network_allowed)
        if network_allowed:
            check_action.triggered.connect(lambda checked=False, sid=sid: page.check_single(sid))
        task = page._download_tasks.get(sid)
        if task and task.status in ("active", "paused", "waiting"):
            if task.status == "paused":
                menu.addAction("继续下载", lambda sid=sid: page.resume_single(sid))
            else:
                menu.addAction("暂停下载", lambda sid=sid: page.pause_single(sid))
            menu.addAction("取消下载", lambda sid=sid: page.cancel_single(sid))
        else:
            download_action = menu.addAction("下载")
            download_action.setEnabled(network_allowed)
            if network_allowed:
                download_action.triggered.connect(lambda checked=False, sid=sid: page.download_single(sid))
        if row_data.get("status") == STATUS_NO_UPDATE:
            force_action = menu.addAction("忽略检测结果，直接下载")
            force_action.setEnabled(network_allowed)
            if network_allowed:
                force_action.triggered.connect(lambda checked=False, sid=sid: page.download_single(sid))
        menu.addSeparator()
        check_result = page._check_results.get(sid, {})
        has_url = bool(check_result.get("download_url"))
        copy_link_action = menu.addAction("复制下载链接")
        copy_link_action.setEnabled(has_url)
        if has_url:
            copy_link_action.triggered.connect(lambda checked=False, sid=sid: page._copy_download_link(sid))
        menu.addSeparator()
        menu.addAction("编辑下载设置", lambda checked=False, data=row_data: self.edit_download_item(data))
        menu.addAction("打开下载文件夹", lambda: page.open_software_dir(row_data))
        menu.addAction("查看下载信息", lambda: page.show_download_info(row_data))

    def edit_download_item(self, row_data):
        """从主页面右键菜单直接编辑当前软件的下载项配置。"""
        page = self._page
        node = row_data.get("node", {})
        software_id = node.get("id", "")
        status, result_data = edit_software_item(
            page.software_manager,
            software_id,
            self._software_item_dialog_factory,
            page,
            build_current_base_stylesheet(page.config_manager, page.light_theme, page.scale_manager),
        )
        if status == STATUS_MISSING_ID:
            page._append_info("无法编辑：当前软件缺少 ID。", level="error")
            return
        if status == STATUS_MISSING_NODE:
            page._append_info("无法编辑：配置文件里找不到这个下载项。", level="error")
            return
        if status == STATUS_MISSING_FACTORY:
            page._append_info("无法编辑：未配置下载项编辑窗口。", level="error")
            return
        if status == STATUS_CANCELLED:
            return
        if status == STATUS_REPLACE_FAILED:
            page._append_info("保存失败：配置文件里找不到这个下载项。", level="error")
            return
        if status != STATUS_SAVED:
            page._append_info("保存失败：下载项编辑流程异常。", level="error")
            return
        if page.version_checker:
            page.version_checker.invalidate_cache(software_id)
        page.reload_items()
        page._append_info(f"{result_data.get('name', '下载项')}：下载设置已保存。", level="success")

    def selected_software_rows(self):
        """返回表格当前选中的软件下载行数据，去重后供批量菜单使用。"""
        page = self._page
        selected = []
        seen = set()
        for index in page.table.selectionModel().selectedRows():
            item = page.table.item(index.row(), 0)
            data = item.data(Qt.UserRole) if item else None
            if isinstance(data, dict) and data.get("type") == "software" and id(data) not in seen:
                selected.append(data)
                seen.add(id(data))
        return selected

    def handle_cell_double_clicked(self, row, column):
        """双击软件名称列时打开下载项编辑窗口。"""
        page = self._page
        item = page.table.item(row, 0)
        if not item:
            return
        data = item.data(Qt.UserRole) or {}
        if isinstance(data, dict) and data.get("type") == "category":
            page.toggle_category(data.get("category", ""))
        elif isinstance(data, dict) and data.get("type") == "software" and column == 0:
            self.edit_download_item(data)
