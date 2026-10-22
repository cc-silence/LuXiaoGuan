"""多文件复选选择器。

主要作用：为下载页等需要从多个文件中选择任意子集的场景提供统一的三态“全选”开关
和文件复选列表。组件只维护内存中的索引选择，不保存配置、不发起下载。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout


def multi_file_selector_stylesheet(light_theme):
    """返回多文件选择器在下拉菜单和弹窗共用的紧凑主题样式。

    参数：light_theme 表示是否亮色主题。返回值：组件局部 QSS。
    副作用：无。颜色使用下载主表格背景，复选框沿用设置窗口的圆形视觉语言，但在紧凑
    菜单中缩小尺寸，避免文件列表显得拥挤。
    """

    if light_theme:
        return """
        QFrame#multiFileSelector { background: #f3f6fb; border: 1px solid #c5cfde; border-radius: 8px; }
        QFrame#multiFileSelectorList, QScrollArea#multiFileSelectorScroll { background: transparent; border: none; }
        QCheckBox#multiFileSelectorCheck { color: #172033; spacing: 7px; min-height: 24px; padding: 1px 8px; border-radius: 4px; }
        QCheckBox#multiFileSelectorCheck:hover { background: #dce9f7; }
        QCheckBox#multiFileSelectorCheck::indicator { width: 14px; height: 14px; border: 1px solid #aeb7c2; border-radius: 7px; background: #ffffff; }
        QCheckBox#multiFileSelectorCheck::indicator:hover { border-color: #7f8b99; }
        QCheckBox#multiFileSelectorCheck::indicator:checked { background: #8fc5ee; border-color: #7eaed2; background-image: url(:/icons/images/icons/cil-check-alt.png); background-position: center; background-repeat: no-repeat; }
        QCheckBox#multiFileSelectorCheck::indicator:indeterminate { background: #b8d4f0; border-color: #7eaed2; }
        """
    return """
    QFrame#multiFileSelector { background: #1b1f24; border: 1px solid #343b45; border-radius: 8px; }
    QFrame#multiFileSelectorList, QScrollArea#multiFileSelectorScroll { background: transparent; border: none; }
    QCheckBox#multiFileSelectorCheck { color: #f4f6f8; spacing: 7px; min-height: 24px; padding: 1px 8px; border-radius: 4px; }
    QCheckBox#multiFileSelectorCheck:hover { background: #26303a; }
    QCheckBox#multiFileSelectorCheck::indicator { width: 14px; height: 14px; border: 1px solid rgb(62, 70, 84); border-radius: 7px; background: rgb(44, 49, 60); }
    QCheckBox#multiFileSelectorCheck::indicator:hover { border-color: rgb(58, 66, 81); }
    QCheckBox#multiFileSelectorCheck::indicator:checked { background: #4a8db7; border-color: #3d7da8; background-image: url(:/icons/images/icons/cil-check-alt.png); background-position: center; background-repeat: no-repeat; }
    QCheckBox#multiFileSelectorCheck::indicator:indeterminate { background: #3d7da8; border-color: #4a8db7; }
    """


def display_name(entry):
    """返回下载条目适合界面展示的文件名。

    参数：entry 是脚本返回的单个 downloads 字典。返回值：优先 filename/name，最后从 URL
    推导的短文件名。副作用：无；用于 UI 显示，不能作为磁盘路径直接使用。
    """

    if not isinstance(entry, dict):
        return "未命名文件"
    # filename 是实际保存到磁盘的 .xpi 文件名，name 才是下载脚本提供给用户看的名称。
    # 优先使用 name，否则 Firefox 扩展的中文说明会被实际文件名覆盖。
    name = str(entry.get("name") or entry.get("filename") or "").strip()
    if name:
        return name
    url = str(entry.get("url") or "").strip()
    return url.rsplit("/", 1)[-1] if url else "未命名文件"


class MultiFileSelector(QFrame):
    """支持全选和部分选择的文件复选列表。

    参数：entries 是下载条目列表；selected_indexes 是初始选中索引；parent 是 Qt 父控件。
    返回值：selected_indexes() 返回当前选中条目的索引集合。
    副作用：用户点击会修改控件内选择并发射 selectionChanged，不修改下载行或配置文件。
    """

    selectionChanged = Signal(set)

    def __init__(self, entries, selected_indexes=None, completed_filenames=None, parent=None):
        super().__init__(parent)
        self.setObjectName("multiFileSelector")
        self._entries = list(entries or [])
        self._completed_filenames = {str(name) for name in (completed_filenames or set())}
        self._checks = []
        self._syncing = False
        self._light_theme = None
        self._build_ui(selected_indexes or set())

    def set_light_theme(self, light_theme):
        """应用多文件选择器的明暗局部样式。

        参数：light_theme 为 True 时应用亮色样式，否则应用暗色样式。返回值：无。
        副作用：替换组件局部 QSS，不修改用户已勾选的文件。调用方必须在全局主题切换后
        调用此方法，因为局部 QSS 会覆盖父窗口的新主题规则。
        """

        light_theme = bool(light_theme)
        if self._light_theme == light_theme:
            return
        self._light_theme = light_theme
        self.setStyleSheet(multi_file_selector_stylesheet(light_theme))

    def _build_ui(self, selected_indexes):
        """创建全选开关和可滚动文件复选列表。"""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll_area = QScrollArea(self)
        scroll_area.setObjectName("multiFileSelectorScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setMaximumHeight(260)
        list_frame = QFrame(scroll_area)
        list_frame.setObjectName("multiFileSelectorList")
        list_layout = QVBoxLayout(list_frame)
        # 全选和文件项放在同一个布局中，彻底消除独立横条与滚动视口造成的对齐偏差。
        list_layout.setContentsMargins(8, 3, 8, 4)
        list_layout.setSpacing(1)
        self.select_all_check = QCheckBox("全选", list_frame)
        self.select_all_check.setObjectName("multiFileSelectorCheck")
        self.select_all_check.setTristate(True)
        self.select_all_check.setToolTip("勾选可选择全部文件；取消勾选可取消全部文件。")
        # 只响应用户真实点击。三态控件从未选点击时会先经过 PartiallyChecked；若监听
        # stateChanged 会把这个中间展示状态当成“取消全选”，导致用户永远无法一次全选。
        self.select_all_check.clicked.connect(self._on_select_all_clicked)
        list_layout.addWidget(self.select_all_check)
        for index, entry in enumerate(self._entries):
            item_name = display_name(entry)
            filename = str(entry.get("filename", "")) if isinstance(entry, dict) else ""
            if filename in self._completed_filenames:
                item_name = f"已完成 {item_name}"
            check = QCheckBox(item_name, list_frame)
            check.setObjectName("multiFileSelectorCheck")
            check.setChecked(index in selected_indexes)
            check.setToolTip(display_name(entry))
            check.toggled.connect(self._on_item_toggled)
            list_layout.addWidget(check)
            self._checks.append(check)
        list_layout.addStretch(1)
        scroll_area.setWidget(list_frame)
        layout.addWidget(scroll_area, 1)
        self._sync_select_all_state()

    def selected_indexes(self):
        """返回当前被勾选文件的索引集合。"""

        return {index for index, check in enumerate(self._checks) if check.isChecked()}

    def set_selected_indexes(self, indexes):
        """按索引集合回显选择状态，并同步三态全选开关。"""

        selected = {int(index) for index in (indexes or set()) if isinstance(index, int)}
        self._syncing = True
        for index, check in enumerate(self._checks):
            check.setChecked(index in selected)
        self._syncing = False
        self._sync_select_all_state()

    def _on_select_all_clicked(self, checked):
        """用户操作全选开关时统一勾选或取消所有文件。"""

        if self._syncing:
            return
        self._syncing = True
        for check in self._checks:
            check.setChecked(bool(checked))
        self._syncing = False
        self._sync_select_all_state()
        self.selectionChanged.emit(self.selected_indexes())

    def _on_item_toggled(self):
        """单个文件切换后同步全选三态，并通知调用方。"""

        if self._syncing:
            return
        self._sync_select_all_state()
        self.selectionChanged.emit(self.selected_indexes())

    def _sync_select_all_state(self):
        """根据当前文件勾选状态更新全选复选框的选中、未选或半选状态。"""

        selected_count = len(self.selected_indexes())
        total = len(self._checks)
        self._syncing = True
        if selected_count == 0:
            self.select_all_check.setCheckState(Qt.Unchecked)
        elif selected_count == total:
            self.select_all_check.setCheckState(Qt.Checked)
        else:
            self.select_all_check.setCheckState(Qt.PartiallyChecked)
        self._syncing = False
