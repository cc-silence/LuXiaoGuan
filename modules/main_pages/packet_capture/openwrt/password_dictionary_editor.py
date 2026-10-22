"""猜密码字典列表，参照 WiKey 的三列结构，复用 \u8def\u5c0f\u7ba1 公共按钮和委托。"""
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView, QFileDialog
from PySide6.QtGui import QShortcut, QKeySequence
from modules.common_widgets.spaced_item_delegate import SpacedItemDelegate
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.dialog_theme import detect_light_theme
from modules.ui_scale_manager import UiScaleManager
from modules.config_services.capture_settings_manager import CaptureSettingsManager


class PasswordDictionaryEditor(QWidget):
    """路径只存放在行数据；列表顺序就是执行顺序，不读取字典正文。"""
    pathsChanged = Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.light_theme=detect_light_theme(self)
        layout=QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(6)
        self.tree=QTreeWidget(self)
        self.tree.setObjectName("captureDictionaryList")
        self.tree.setHeaderLabels(["字典名称","大小","所在位置"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setItemDelegate(SpacedItemDelegate(self.tree,show_focus_rect=False))
        self.tree.header().setSectionResizeMode(0,QHeaderView.Interactive)
        self.tree.header().setSectionResizeMode(1,QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.setToolTip("按列表顺序使用字典。Ctrl/Shift 多选；Delete 只从列表移除，不删除原文件。")
        UiScaleManager.for_widget(self).bind_metrics(self.tree,fixed_height=160)
        self.tree.setColumnWidth(0,UiScaleManager.for_widget(self).px(240))
        layout.addWidget(self.tree)
        row=QHBoxLayout()
        self.add_button=self._button(row,"添加文件","一次选择多个文本字典，自动追加并去重。",self.choose_files)
        self.remove_button=self._button(row,"移除选中","只从列表移除，不删除字典原文件。",self.remove_selected)
        self.up_button=self._button(row,"上移","将选中的字典向前移动一位。",lambda:self.move_selected(-1))
        self.down_button=self._button(row,"下移","将选中的字典向后移动一位。",lambda:self.move_selected(1))
        row.addStretch(1)
        layout.addLayout(row)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        shortcut=QShortcut(QKeySequence("Delete"),self.tree)
        shortcut.activated.connect(self.remove_selected)
        self._update_buttons()

    def _button(self,row,text,tooltip,callback):
        button=create_page_text_button(self,text,tooltip)
        button.set_light_theme(self.light_theme)
        button.clicked.connect(callback)
        row.addWidget(button)
        return button

    def paths(self):
        return tuple(self.tree.topLevelItem(i).data(0,Qt.UserRole) for i in range(self.tree.topLevelItemCount()))

    def setPlainText(self,text):
        self.tree.clear()
        self.append_paths(str(text or "").splitlines(),emit=False)

    def toPlainText(self):
        return "\n".join(self.paths())

    def append_paths(self,paths,*,emit=True):
        seen={path.casefold() for path in self.paths()}
        changed=False
        for value in paths:
            if not str(value).strip():continue
            path=str(CaptureSettingsManager.resolve_path(str(value).strip()))
            if path.casefold() in seen:continue
            seen.add(path.casefold())
            item=QTreeWidgetItem([Path(path).name,"待检查",str(Path(path).parent)])
            item.setData(0,Qt.UserRole,path)
            for column in (0,2):item.setToolTip(column,path)
            self.tree.addTopLevelItem(item)
            changed=True
        self._update_buttons()
        if changed and emit:self.pathsChanged.emit()

    def choose_files(self):
        paths=self.paths()
        start=str(Path(paths[-1]).parent) if paths else str(Path.home())
        files,_=QFileDialog.getOpenFileNames(self,"添加密码字典",start,"文本字典 (*.txt *.dic);;所有文件 (*)")
        self.append_paths(files)

    def remove_selected(self):
        items=self.tree.selectedItems()
        for item in items:self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        self._update_buttons()
        if items:self.pathsChanged.emit()

    def move_selected(self,direction):
        items=self.tree.selectedItems()
        if not items:return
        rows=sorted(self.tree.indexOfTopLevelItem(item) for item in items)
        count=self.tree.topLevelItemCount()
        if rows[0]==0 and direction<0 or rows[-1]==count-1 and direction>0:return
        for index in rows if direction<0 else reversed(rows):
            item=self.tree.takeTopLevelItem(index)
            self.tree.insertTopLevelItem(index+direction,item)
            item.setSelected(True)
        self._update_buttons()
        self.pathsChanged.emit()

    def _update_buttons(self):
        rows=[self.tree.indexOfTopLevelItem(item) for item in self.tree.selectedItems()]
        self.remove_button.setEnabled(bool(rows))
        self.up_button.setEnabled(bool(rows) and min(rows)>0)
        self.down_button.setEnabled(bool(rows) and max(rows)<self.tree.topLevelItemCount()-1)

    def apply_metadata(self,metadata):
        """仅显示后台冻结的大小或缺失状态，不在主线程访问文件。"""
        for index in range(self.tree.topLevelItemCount()):
            item=self.tree.topLevelItem(index)
            size=metadata.get(item.data(0,Qt.UserRole))
            if size is None:item.setText(1,"不可读取")
            elif size<1024:item.setText(1,f"{size} B")
            else:
                value=float(size)
                for unit in ("KB","MB","GB","TB"):
                    value/=1024
                    if value<1024 or unit=="TB":break
                item.setText(1,f"{value:.1f} {unit}")
