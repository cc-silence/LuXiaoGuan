"""下载页多文件选择界面与运行时选择状态。

主要作用：把多文件下载结果显示为文件名列下拉菜单，并在用户未选择文件就下载或入队时
显示同一套选择器弹窗。选择仅保存在当前页面 row_data 中，重新检测会重置为空。
"""

from __future__ import annotations

from .download_size_cell import refresh_download_size

from PySide6.QtCore import QPoint, Qt, QSize, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from modules.common_widgets.common_dialogs import apply_common_dialog_theme
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.multi_file_selector import MultiFileSelector
from modules.icon_utils import get_theme_icon
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons


class MultiFileSelectionPopup(QWidget):
    """文件名列展开的轻量多选弹窗。

    主要作用：使用透明 Qt.Popup 外壳承载一个圆角 MultiFileSelector，避免 QMenu 的
    系统方形容器与内部圆角面板叠在一起。输入参数和返回选择通过 selector 处理。
    副作用：显示时会临时取得焦点；点击窗口外部后由 Qt.Popup 自动关闭。
    """

    selectionChanged = Signal(set)

    def __init__(self, downloads, selected_indexes, light_theme, completed_filenames=None, parent=None):
        super().__init__(parent)
        self.setObjectName("multiFileSelectionPopup")
        self._light_theme = None
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("QWidget#multiFileSelectionPopup { background: transparent; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.selector = MultiFileSelector(
            downloads,
            selected_indexes,
            completed_filenames=completed_filenames,
            parent=self,
        )
        self.selector.setMinimumWidth(340)
        self.set_light_theme(light_theme)
        self.selector.selectionChanged.connect(self.selectionChanged)
        layout.addWidget(self.selector)

    def set_light_theme(self, light_theme):
        """同步弹出选择器的局部主题，不重建文件选择状态。"""

        self._light_theme = bool(light_theme)
        self.selector.set_light_theme(self._light_theme)

    def show_below(self, anchor):
        """将选择面板贴在文件名单元格下方展开。"""

        # 弹窗会被单元格长期持有。主题切换后再次展开前必须读取下载页当前主题，不能使用
        # 创建时缓存的颜色，否则会出现主界面已变暗、选择器仍是亮色的混合状态。
        page = self.parentWidget()
        if page is not None and hasattr(page, "light_theme"):
            self.set_light_theme(page.light_theme)
        self.adjustSize()
        self.move(anchor.mapToGlobal(QPoint(0, anchor.height())))
        self.show()
        self.raise_()


def is_multi_file_result(result):
    """判断检测结果是否包含两个及以上可供用户选择的文件。"""

    downloads = result.get("downloads") if isinstance(result, dict) else None
    return isinstance(downloads, list) and len(downloads) > 1


def selected_multi_downloads(row_data, result):
    """按当前行保存的索引返回用户勾选的多文件下载条目。

    参数：row_data 是下载行；result 是最新检测结果。
    返回值：用户选中的 dict 列表；没有选择、索引过期或不是多文件结果时返回空列表。
    副作用：无。重新检测后调用方必须清空 row_data 的选择索引，避免旧索引误指向新文件。
    """

    downloads = result.get("downloads") if isinstance(result, dict) else None
    if not isinstance(downloads, list) or len(downloads) <= 1:
        return []
    indexes = row_data.get("_selected_multi_download_indexes", set())
    if not isinstance(indexes, (set, list, tuple)):
        return []
    return [downloads[index] for index in sorted(set(indexes)) if isinstance(index, int) and 0 <= index < len(downloads) and isinstance(downloads[index], dict)]


def multi_file_cell_text(downloads, selected_indexes):
    """返回文件名列的多文件选择状态文字。

    参数：downloads 是本次检测出的文件列表；selected_indexes 是当前内存选择索引。
    返回值：已选择文件时返回“已选择 N 个文件”，未选择时返回总文件数。
    副作用：无。此函数只负责界面文案，不能据此判断下载任务是否有效。
    """

    selected_count = len(set(selected_indexes or set()))
    if selected_count:
        return f"已选择 {selected_count} 个文件"
    return f"{len(downloads)}个文件"


def install_multi_file_cell(page, row_data, result):
    """在文件名列安装多文件下拉菜单。

    参数：page 是软件下载页；row_data 是目标行；result 是脚本检测结果。
    返回值：无。副作用：替换文件名列控件；菜单选择会更新 row_data 的运行时索引集合。
    """

    downloads = result.get("downloads", [])
    row = page._row_for_data(row_data)
    if row is None:
        return
    # 文件名列在检测前使用 QTableWidgetItem("--") 占位。cell widget 背景为透明时仍会透出
    # 该底层文本，造成下拉入口下面出现多余的 "--"，安装控件前必须清空它。
    item = page.table.item(row, 3)
    if item is not None:
        item.setText("")
        item.setToolTip("")
    container = QWidget(page.table)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(4, 2, 4, 2)
    layout.setSpacing(4)
    # 文件名列还要为下拉箭头预留空间，入口文字必须允许换行，否则“已选择 N 个文件”
    # 在窄列会被 QLabel 直接裁切，用户看不到实际选择数量。
    text = multi_file_cell_text(
        downloads,
        row_data.get("_selected_multi_download_indexes", set()),
    )
    name_label = QLabel(text, container)
    name_label.setWordWrap(True)
    name_label.setAlignment(Qt.AlignCenter)
    name_label.setMinimumHeight(32)
    name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    name_label.setCursor(Qt.PointingHandCursor)
    name_label.setToolTip("点击选择要下载的文件。")
    arrow_btn = QToolButton(container)
    arrow_btn.setObjectName("multiFileArrowBtn")
    arrow_btn.setIcon(get_theme_icon(":/icons/images/icons/cil-arrow-bottom.png", page.light_theme))
    arrow_btn.setIconSize(QSize(18, 18))
    arrow_btn.setAutoRaise(True)
    arrow_btn.setFixedWidth(22)
    arrow_btn.setCursor(Qt.PointingHandCursor)

    popup = MultiFileSelectionPopup(
        downloads,
        row_data.get("_selected_multi_download_indexes", set()),
        page.light_theme,
        completed_filenames=row_data.get("_multi_completed_filenames", set()),
        parent=page,
    )

    def show_popup(event=None, target_popup=popup, anchor=container):
        target_popup.show_below(anchor)

    def save_selection(indexes, rd=row_data, label=name_label, items=downloads):
        rd["_selected_multi_download_indexes"] = set(indexes)
        rd["_multi_selection_confirmed"] = bool(indexes)
        # 下拉面板不会重建表格单元格；选择变更后直接更新入口文字，用户关闭面板时即可
        # 看见当前已选数量，不必重新检测或重启软件才能确认选择是否生效。
        label.setText(multi_file_cell_text(items, indexes))
        refresh_download_size(page, rd, result=result)
        page.table.setRowHeight(row, max(page.table.rowHeight(row), 54))

    name_label.mousePressEvent = show_popup
    arrow_btn.clicked.connect(show_popup)
    popup.selectionChanged.connect(save_selection)
    layout.addWidget(name_label, 1)
    layout.addWidget(arrow_btn)
    page.table.setCellWidget(row, 3, container)
    refresh_download_size(page, row_data, result=result)
    # 允许两行入口文案完整显示；固定最小行高避免主题、字体或选择数量变化时布局跳动。
    page.table.setRowHeight(row, max(page.table.rowHeight(row), 54))
    # Qt.Popup 没有父级引用时可能在 cell widget 重建后被提前回收，显式由容器持有。
    container.multi_file_selection_popup = popup


class MultiFileSelectionDialog(FramelessDialog):
    """下载或入队前要求用户选择多文件下载条目的弹窗。

    参数：downloads 是检测结果文件列表；selected_indexes 是当前选择；parent 是下载页。
    返回值：确认后读取 selected_indexes 属性。
    副作用：仅在内存中维护选择；调用方确认后负责写回 row_data，不保存软件配置。
    """

    def __init__(self, downloads, selected_indexes=None, completed_filenames=None, parent=None):
        # 使用和 ConfirmDialog 相同的公共主题路径，禁止继承下载页局部 QSS；否则无边框外壳
        # 的 settingsWindowFrame 没有背景定义，会直接透出桌面。
        super().__init__("选择要下载的文件", parent, inherit_parent_stylesheet=False)
        self.selected_indexes = set(selected_indexes or set())
        self.completed_filenames = set(completed_filenames or set())
        apply_common_dialog_theme(self, parent)
        # FramelessDialog 默认内容区顶部为 0。选择器带边框时会紧贴标题栏分隔线，
        # 单独留出上方呼吸空间，避免内部面板看起来和标题栏粘在一起。
        self.content_layout.setContentsMargins(16, 12, 16, 12)
        self.setMinimumSize(480, 330)
        self.resize(560, 440)
        self._setup_ui(downloads)

    def _setup_ui(self, downloads):
        """创建复用的多文件选择器和确认/取消按钮。"""

        self.selector = MultiFileSelector(
            downloads,
            self.selected_indexes,
            completed_filenames=self.completed_filenames,
            parent=self,
        )
        self.selector.set_light_theme(bool(getattr(self.parentWidget(), "light_theme", False)))
        self.selector.selectionChanged.connect(self._update_ok_enabled)
        self.content_layout.addWidget(self.selector, 1)
        self.ok_button, self.cancel_button = create_dialog_ok_cancel_buttons(self)
        self.ok_button.clicked.connect(self._accept)
        self.cancel_button.clicked.connect(self.reject)
        self.button_layout.addWidget(self.ok_button)
        self.button_layout.addSpacing(10)
        self.button_layout.addWidget(self.cancel_button)
        self._update_ok_enabled(self.selector.selected_indexes())

    def _update_ok_enabled(self, indexes):
        """至少选择一个文件后才允许确认，避免产生空下载任务。"""

        self.ok_button.setEnabled(bool(indexes))

    def _accept(self):
        """保存本次选择到属性并关闭弹窗。"""

        self.selected_indexes = self.selector.selected_indexes()
        if not self.selected_indexes:
            return
        self.accept()


def choose_multi_downloads(page, row_data, result):
    """确保多文件下载项至少选中一个文件。

    参数：page 是下载页；row_data 是目标行；result 是最新检测结果。
    返回值：True 表示无需选择或用户已选至少一个文件；False 表示用户取消。
    副作用：必要时显示选择弹窗，并将选择索引写入 row_data，随后刷新文件名列下拉控件。
    """

    if not is_multi_file_result(result):
        return True
    if selected_multi_downloads(row_data, result):
        return True
    downloads = result.get("downloads", [])
    dialog = MultiFileSelectionDialog(
        downloads,
        set(),
        completed_filenames=row_data.get("_multi_completed_filenames", set()),
        parent=page,
    )
    if dialog.exec() != MultiFileSelectionDialog.Accepted:
        return False
    row_data["_selected_multi_download_indexes"] = set(dialog.selected_indexes)
    install_multi_file_cell(page, row_data, result)
    return bool(dialog.selected_indexes)
