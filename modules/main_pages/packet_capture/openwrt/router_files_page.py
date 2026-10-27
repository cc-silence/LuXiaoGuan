"""路由器文件管理页，复用已有卡片和操作按钮。"""
import os
import posixpath
import stat
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt, QItemSelectionModel, Signal
from PySide6.QtWidgets import QWidget,QHBoxLayout,QVBoxLayout,QLineEdit,QTreeWidgetItem,QSplitter,QHeaderView,QFileDialog,QApplication,QFileIconProvider,QStackedWidget,QLabel,QAbstractItemView
from PySide6.QtGui import QShortcut,QKeySequence,QIcon
from modules.dialog_theme import create_themed_menu,detect_light_theme
from modules.ui_scale_manager import UiScaleManager
from modules.display_privacy import controller_for,PrivacyLabel
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import ConfirmDialog,MessageDialog,apply_common_dialog_theme
from modules.common_widgets.icon_button import create_themed_icon_button,refresh_tool_button_icons
from modules.config_manager import BASE_DIR
from modules.icon_utils import get_theme_icon
from modules.platform_services.windows_user_paths import current_user_desktop
from modules.platform_services.file_metadata import same_local_path
from .router_file_widgets import FileTree,FileIcons,FileTransferDialog
from modules.main_pages.packet_capture.ui_helpers import create_capture_card
from modules.settings_pages.settings_widgets import create_settings_button,setup_name_input

class FileItem(QTreeWidgetItem):
    """按原始文件属性排序，隐私切换不改变文件目标。"""
    def __lt__(self, other):
        column=self.treeWidget().sortColumn()
        left=self.data(0,Qt.UserRole)
        right=other.data(0,Qt.UserRole)
        if not left or not right: return super().__lt__(other)
        folder_left=stat.S_ISDIR(left['mode'])
        folder_right=stat.S_ISDIR(right['mode'])
        if folder_left != folder_right:
            ascending=self.treeWidget().header().sortIndicatorOrder()==Qt.AscendingOrder
            return folder_left if ascending else folder_right
        if column==0:return left['name'].casefold() < right['name'].casefold()
        if column==1: return (left['size'],left['name']) < (right['size'],right['name'])
        if column==3: return (left['modified'],left['name']) < (right['modified'],right['name'])
        return (self.text(column).casefold(),left['name'].casefold()) < (other.text(column).casefold(),right['name'].casefold())


def readable_size(size):
    """将文件字节数显示为便于阅读的单位，排序仍使用原始数值。"""
    value=float(size)
    for unit in ('B','KB','MB','GB','TB'):
        if value<1024 or unit=='TB':
            return f'{int(value)} B' if unit=='B' else f'{value:.1f} {unit}'
        value/=1024


def file_type(entry):
    """使用元数据区分文件夹、链接和文件，不访问远程路径。"""
    if stat.S_ISDIR(entry['mode']):return '文件夹'
    if stat.S_ISLNK(entry['mode']):return '符号链接'
    suffix=Path(entry['name']).suffix
    return suffix[1:].upper()+' 文件' if suffix else '文件'

def runnable_entry(entry):
    """只认普通文件的执行权限或 shell 脚本，不运行目录和链接。"""
    return stat.S_ISREG(entry['mode']) and bool(entry['mode'] & 0o111 or entry['name'].lower().endswith('.sh'))


class ValueDialog(FramelessDialog):
    """使用公共主题输入名称或权限。"""
    def __init__(self, title, value, parent):
        super().__init__(title, parent, inherit_parent_stylesheet=False)
        apply_common_dialog_theme(self,parent)
        self.edit = QLineEdit(value,self)
        self.content_layout.addWidget(self.edit)
        setup_name_input(self.edit)
        button = create_settings_button('确定',self)
        button.clicked.connect(self.accept)
        self.button_layout.addWidget(button)
        cancel=create_settings_button("取消",self,role="secondary")
        cancel.clicked.connect(self.reject)
        self.button_layout.addWidget(cancel)
        self.set_scaled_size((400,180))
        self.edit.setFocus(Qt.OtherFocusReason)

    def showEvent(self,event):
        """显示时把编辑焦点交给输入框，选中默认值供用户直接输入。"""
        super().showEvent(event)
        self.edit.setFocus(Qt.OtherFocusReason)
        self.edit.selectAll()

class RouterFilesPage(QWidget):
    """嵌入顶层标签页；页面只处理显示和确认，不执行网络操作。"""
    terminalRequested=Signal(str,bool)
    def __init__(self,service,info_panel=None,parent=None):
        super().__init__(parent)
        self.service=service
        self.info_panel=info_panel
        self.light_theme=detect_light_theme(self)
        self.buttons=[]
        self.pending={}
        self._browse={}
        self._history={False:[],True:[]}
        self._history_index={False:-1,True:-1}
        self._current_path={False:str(current_user_desktop()),True:'/root'}
        self._nav={}
        self._footers={}
        self._search={}
        self._transfer_buttons={}
        self._delete_buttons={}
        self._remote_actions={}
        self._views={}
        self._view_buttons={}
        self._transfers={}
        self._transfer_destinations={}
        self._show_hidden=False
        self._revealed_local_path=None
        self._drag_file=None
        provider=QFileIconProvider()
        self._folder_icons={False:QIcon(str(BASE_DIR/'images/material_icons/folder-local-yellow.svg')),True:QIcon(str(BASE_DIR/'images/material_icons/folder-blue.png'))}
        self._file_icon=provider.icon(QFileIconProvider.File)
        self._loaded_connection=None
        self._seen_connection=None
        self._privacy=controller_for(self)
        if self._privacy is not None:self._privacy.changed.connect(self._refresh_privacy)
        self.setObjectName('openwrtFilesPage')
        root=QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(8)
        self.connection_notice=QLabel("",self)
        self.connection_notice.setObjectName("captureDescription")
        root.addWidget(self.connection_notice)
        self._disconnect_notified=False
        self._had_connection=False
        files=QSplitter(Qt.Horizontal,self)
        files.setObjectName('routerFilesSplitter')
        files.setHandleWidth(UiScaleManager.for_widget(self).px(5))
        files.setChildrenCollapsible(False)
        self.pane_splitter=files
        row=files
        self.local_path,self.local_tree=self._pane(row,False)
        self.remote_path,self.remote_tree=self._pane(row,True)
        files.setStretchFactor(0,1)
        files.setStretchFactor(1,1)
        if info_panel is not None:
            from modules.common_widgets.info_panel_dock import InfoPanelDock
            self.info_dock = InfoPanelDock(files, info_panel, Qt.Vertical, self, sizes=(560,90))
            self.file_splitter = self.info_dock.splitter
            root.addWidget(self.info_dock,1)
        else:
            splitter=QSplitter(Qt.Vertical,self)
            splitter.setObjectName('routerFilesSplitter')
            splitter.setHandleWidth(UiScaleManager.for_widget(self).px(5))
            splitter.setChildrenCollapsible(False)
            self.file_splitter=splitter
            splitter.addWidget(files)
            root.addWidget(splitter,1)
        service.completed.connect(self._completed)
        service.failed.connect(self._failed)
        service.progress.connect(self._progress)
        owner=getattr(service,'owner',None)
        if owner is not None:owner.openwrtSnapshotChanged.connect(self._apply_connection)
        from .file_dependency_prompt import FileDependencyPrompt
        self._dependency_prompt=FileDependencyPrompt(self)
        self._apply_connection(None)

    def enter(self):
        """首次进入或换连接后读取目录，不在构造时访问路由器。"""
        ssh=self.service.manager.ssh
        if not ssh.connected:return
        connection=getattr(ssh,'_client',None)
        if connection is not self._loaded_connection:
            self._loaded_connection=connection
            self._refresh(False)
            self._refresh(True)

    def _apply_connection(self,snapshot):
        ssh=self.service.manager.ssh
        connected=ssh.connected
        if connected:self._had_connection=True
        note="路由器连接已断开，请到“路由器登录”重新登录。" if self._had_connection else "请先到“路由器登录”登录，再使用文件管理。"
        self.connection_notice.setText("" if connected else note)
        self.connection_notice.setVisible(not connected)
        if connected:self._disconnect_notified=False
        connection=getattr(ssh,'_client',None) if connected else None
        if connection is not self._seen_connection:
            self._seen_connection=connection
            self._loaded_connection=None
            self.remote_tree.clear()
            self.remote_path.setText('/root')
            self._current_path[True]='/root'
            self._history[True]=[]
            self._history_index[True]=-1
            for key in tuple(self._browse):
                if self._browse[key][0]:
                    self.service.cancel(key)
                    self.pending.pop(key,None)
                    self._browse.pop(key,None)
        for button in self.buttons:button.setEnabled(connected)
        for button in self._view_buttons.values():button.setEnabled(True)
        self.hidden_button.setEnabled(True)
        self._update_controls()
        if not connected:
            self._loaded_connection=None
            self._footers[True].setText('请先登录路由器。')

    def set_light_theme(self,enabled):
        self.light_theme=bool(enabled)
        refresh_tool_button_icons(self,self.light_theme)
        for dialog in self._transfers.values():apply_common_dialog_theme(dialog,self)

    def _button(self,layout,text,callback,icon=None):
        # 文字操作同样使用已有图标按钮工厂，仅改变本页工具栏样式。
        action_icon={'上传到路由器':'upload','下载到本机':'download','新建文件夹':'new-folder'}.get(text)
        path=str(BASE_DIR/'images/icons'/('file-browser-'+(icon or action_icon)+'.svg')) if icon or action_icon else ':/icons/images/icons/cil-folder-open.png'
        if icon == 'terminal':path=str(BASE_DIR/'images/icons/cil-terminal.png')
        button=create_themed_icon_button(self,path,text)
        button.setObjectName('routerFileToolButton')
        button.setAutoRaise(True)
        icon_only=bool((icon and icon != 'terminal') or action_icon == 'new-folder')
        button.setAccessibleName(text)
        button.setText('' if icon_only else text)
        button.setToolButtonStyle(Qt.ToolButtonIconOnly if icon_only else Qt.ToolButtonTextBesideIcon)
        UiScaleManager.for_widget(self).bind_metrics(button,min_height=32,icon_size=20)
        if icon_only:UiScaleManager.for_widget(self).bind_metrics(button,fixed_width=32)
        button.clicked.connect(callback)
        self.buttons.append(button)
        layout.addWidget(button)
        return button

    def _notify(self,text,level='info'):
        if self.info_panel is not None:self.info_panel.append_info(text,level)

    def _display(self,text):
        return self._privacy.text(text) if self._privacy is not None else str(text)

    def _refresh_privacy(self):
        for tree in (self.local_tree,self.remote_tree):
            for index in range(tree.topLevelItemCount()):
                item=tree.topLevelItem(index)
                entry=item.data(0,Qt.UserRole)
                item.setText(0,self._display(entry['name']))
                item.setToolTip(0,self._display(entry['path']))
            self._filter(tree is self.remote_tree)

    def _pane(self,row,remote):
        """双栏共用导航和文件列表；操作入口沿用公共按钮与菜单。"""
        pane,layout=create_capture_card(self,'路由器' if remote else '本地')
        title=pane.findChild(QLabel,'captureGroupTitle')
        pane.layout().removeWidget(title)
        title.setObjectName('routerFilePaneTitle')
        navigation=QHBoxLayout()
        navigation.addWidget(title)
        back=self._button(navigation,'后退',lambda:self._history_step(remote,-1),'back')
        back.setToolTip('后退 (Alt+←)')
        forward=self._button(navigation,'前进',lambda:self._history_step(remote,1),'forward')
        forward.setToolTip('前进 (Alt+→)')
        up=self._button(navigation,'上一级',lambda:self._up(remote),'up')
        up.setToolTip('上一级 (Alt+↑)')
        for button in (back,forward,up):
            UiScaleManager.for_widget(self).bind_metrics(button,fixed_width=32)
        path=QLineEdit(self._current_path[remote],pane)
        path.setObjectName('captureLineEdit')
        path.setToolTip('输入目录路径后按 Enter 跳转；Ctrl+L 定位地址栏。')
        navigation.addWidget(path,1)
        if not remote:
            self.hidden_button=self._button(navigation,'显示隐藏文件',self._toggle_hidden,'hidden')
            self.hidden_button.setCheckable(True)
            self.hidden_button.setToolTip('显示或隐藏本地隐藏文件，包括点开头的文件；默认隐藏。')
        refresh=self._button(navigation,'刷新',lambda:self._refresh(remote),'refresh')
        refresh.setToolTip('刷新当前目录 (F5)')
        layout.addLayout(navigation)
        self._nav[remote]=(back,forward,up)
        toolbar=QHBoxLayout()
        self._transfer_buttons[remote]=self._button(toolbar,'下载到本机' if remote else '上传到路由器',lambda:self._transfer(remote))
        if remote:
            self._remote_actions['mkdir']=self._button(toolbar,'新建文件夹',lambda:self._mutate('mkdir'))
        else:
            self._button(toolbar,'选择文件夹',self._choose_local_folder)
            self.local_mkdir_button=self._button(toolbar,'新建文件夹',self._create_local_folder)
        self._delete_buttons[remote]=self._button(toolbar,'删除所选',
            lambda:self._mutate('delete') if remote else self._delete_local(), 'delete')
        if remote:
            self.terminal_button=self._button(toolbar,'在终端中打开当前目录',self._open_current_directory_in_terminal,'terminal')
            self.terminal_button.setToolTip('切换到路由器终端，打开右侧当前浏览的目录，不使用选中的文件或文件夹。')
        toolbar.addStretch(1)
        view_button=self._button(toolbar,'切换为图标视图',lambda:self._toggle_view(remote),'grid')
        self._view_buttons[remote]=view_button
        search=QLineEdit(pane)
        search.setObjectName('captureLineEdit')
        search.setPlaceholderText('筛选当前目录')
        search.setClearButtonEnabled(True)
        UiScaleManager.for_widget(self).bind_metrics(search,min_width=90,max_width=180)
        self._search[remote]=search
        toolbar.addWidget(search)
        layout.addLayout(toolbar)
        tree=FileTree(pane)
        tree.setup_transfer(self,remote)
        if remote:tree.setToolTip('从左侧拖入普通文件，上传到右侧当前目录；不会移动本机原文件。')
        tree.setObjectName('routerFileList')
        tree.setAlternatingRowColors(True)
        tree.setRootIsDecorated(False)
        tree.setUniformRowHeights(True)
        UiScaleManager.for_widget(self).bind_metrics(tree,icon_size=20)
        tree.setHeaderLabels(['名称','大小','类型','修改时间','权限'])
        tree.setColumnHidden(4,not remote)
        tree.header().setDefaultAlignment(Qt.AlignCenter)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0,QHeaderView.Interactive)
        for column in range(1,5):tree.header().setSectionResizeMode(column,QHeaderView.ResizeToContents)
        tree.header().setMinimumSectionSize(60)
        tree.header().sectionResized.connect(lambda column,*args:tree.fit_name_column() if column else None)
        tree.setSortingEnabled(True)
        tree.sortItems(0,Qt.AscendingOrder)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda pos:self._context_menu(remote,pos))
        tree.itemSelectionChanged.connect(self._update_controls)
        stack=QStackedWidget(pane)
        stack.addWidget(tree)
        icons=FileIcons(tree,pane)
        icons.setup_transfer(self,remote)
        if remote:icons.setToolTip(tree.toolTip())
        icons.setContextMenuPolicy(Qt.CustomContextMenu)
        icons.customContextMenuRequested.connect(lambda pos:self._context_menu(remote,pos,icons))
        icons.doubleClicked.connect(lambda index:self._enter(remote,tree.itemFromIndex(index)))
        stack.addWidget(icons)
        self._views[remote]=(stack,tree,icons)
        tree.model().layoutChanged.connect(lambda:self._filter(remote))
        layout.addWidget(stack,1)
        footer=PrivacyLabel('',pane)
        footer.setObjectName('captureDescription')
        self._footers[remote]=footer
        layout.addWidget(footer)
        search.textChanged.connect(lambda text:self._filter(remote))
        path.returnPressed.connect(lambda:self._refresh(remote))
        tree.itemDoubleClicked.connect(lambda item,column:self._enter(remote,item))
        shortcuts=[('F5',lambda:self._refresh(remote)),('Alt+Left',lambda:self._history_step(remote,-1)),('Alt+Right',lambda:self._history_step(remote,1)),('Alt+Up',lambda:self._up(remote)),('Ctrl+L',lambda:self._focus_path(remote))]
        # 删除等快捷键只绑定列表，地址栏仍按普通文本框编辑。
        for sequence,callback in shortcuts:
            shortcut=QShortcut(QKeySequence(sequence),pane)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
        for view in (tree,icons):
            for sequence,callback in [('Return',lambda:self._enter(remote,tree.currentItem())),('Ctrl+C',lambda:self._copy_path(remote))]+([('F2',lambda:self._mutate('rename')),('Delete',lambda:self._mutate('delete')),('Ctrl+Shift+N',lambda:self._mutate('mkdir'))] if remote else [('Delete',self._delete_local),('Ctrl+Shift+N',self._create_local_folder)]):
                shortcut=QShortcut(QKeySequence(sequence),view)
                shortcut.setContext(Qt.WidgetShortcut)
                shortcut.activated.connect(callback)
        row.addWidget(pane)
        return path,tree

    def _terminal_target(self):
        """一个可运行选中文件才运行，其余情况打开已提交的当前目录。"""
        entries=self._selected_entries(True)
        if len(entries)==1 and runnable_entry(entries[0]):return entries[0]['path'],True
        return self._current_path[True],False

    def _open_current_directory_in_terminal(self):
        """按当前选择分派文件执行或目录打开，不使用地址栏草稿。"""
        if self._require_connection():self.terminalRequested.emit(*self._terminal_target())

    def _toggle_view(self,remote):
        """切换共享模型的两个视图，保留当前选择、排序和筛选。"""
        stack,tree,icons=self._views[remote]
        index=1-stack.currentIndex()
        stack.setCurrentIndex(index)
        button=self._view_buttons[remote]
        button.setProperty('iconPath',str(BASE_DIR/'images/icons'/('file-browser-list.svg' if index else 'file-browser-grid.svg')))
        button.setToolTip('切换为详细列表' if index else '切换为图标视图')
        button.setIcon(get_theme_icon(str(button.property('iconPath')),self.light_theme))
        (icons if index else tree).setFocus()
        current=tree.currentIndex()
        if current.isValid():(icons if index else tree).scrollTo(current)

    def _toggle_hidden(self):
        """仅改变本地列表投影，两个视图共用同一份原始条目。"""
        self._show_hidden=self.hidden_button.isChecked()
        self._revealed_local_path=None
        self._filter(False)

    def _choose_local_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'选择本机文件夹',self._current_path[False])
        if folder:self._navigate(False,folder)

    def _focus_path(self,remote):
        edit=self.remote_path if remote else self.local_path
        edit.setFocus()
        edit.selectAll()

    def _copy_path(self,remote):
        entries=self._selected_entries(remote)
        if entries:QApplication.clipboard().setText(self._display("\n".join(entry['path'] for entry in entries)))

    def _filter(self,remote):
        tree=self.remote_tree if remote else self.local_tree
        text=self._search[remote].text().casefold()
        visible=0
        revealed_paths=self._revealed_local_path if isinstance(self._revealed_local_path,(list,tuple)) else [self._revealed_local_path]
        revealed_keys={os.path.normcase(os.path.normpath(str(path))) for path in revealed_paths if path}
        for index in range(tree.topLevelItemCount()):
            item=tree.topLevelItem(index)
            entry=item.data(0,Qt.UserRole)
            attribute_hidden=not remote and not self._show_hidden and entry.get('hidden',entry['name'].startswith('.'))
            revealed=not remote and os.path.normcase(os.path.normpath(entry['path'])) in revealed_keys
            hidden=not revealed and (text not in item.text(0).casefold() or attribute_hidden)
            item.setHidden(hidden)
            self._views[remote][2].setRowHidden(index,hidden)
            visible+=not hidden
        self._footers[remote].setText(f'{visible} 个项目' + (' · 已筛选' if text else ''))
        self._update_controls()

    def _update_controls(self):
        """根据选择和连接启用真实可执行操作，文件夹不冒充可传输文件。"""
        if not hasattr(self,'remote_tree'):return
        connected=self.service.manager.ssh.connected
        for remote in (False,True):
            entries=self._selected_entries(remote)
            self._transfer_buttons[remote].setEnabled(connected and bool(entries) and all(stat.S_ISREG(entry['mode']) or stat.S_ISDIR(entry['mode']) for entry in entries))
            self._delete_buttons[remote].setEnabled(connected and bool(entries))
            delete_hint = ('永久删除所选（含文件夹全部内容，不可恢复）' if remote else '将所选项目移到回收站')
            self._delete_buttons[remote].setToolTip(delete_hint + f'\n已选 {len(entries)} 项；Ctrl 多选，Shift 连选，Delete 删除')
            back,forward,up=self._nav[remote]
            index=self._history_index[remote]
            back.setEnabled(connected and index>0)
            forward.setEnabled(connected and 0<=index<len(self._history[remote])-1)
            current=self._current_path[remote]
            parent=posixpath.dirname(current.rstrip('/')) or '/' if remote else str(Path(current).parent)
            up.setEnabled(connected and parent!=current)
        self.local_mkdir_button.setEnabled(connected)
        path,run=self._terminal_target()
        self.terminal_button.setText('在终端中运行当前文件' if run else '在终端中打开当前目录')
        self.terminal_button.setToolTip('切换到终端，确认后运行选中的文件。' if run else '切换到终端，打开右侧当前正在浏览的目录。')
        self.terminal_button.setEnabled(connected)

    def _context_menu(self,remote,pos,view=None):
        tree=self.remote_tree if remote else self.local_tree
        view=view or tree
        item=tree.itemFromIndex(view.indexAt(pos))
        if item is not None:
            flags=QItemSelectionModel.NoUpdate if item.isSelected() else QItemSelectionModel.ClearAndSelect
            tree.setCurrentItem(item,0,flags)
        menu=create_themed_menu(self,detect_light_theme(self))
        connected=self.service.manager.ssh.connected
        entry=self._selected(remote)
        def add(text,callback,enabled=True):
            action=menu.addAction(text)
            action.setEnabled(connected and enabled)
            action.triggered.connect(callback)
        if entry:
            if remote:add('打开文件夹',lambda:self._enter(remote,item),stat.S_ISDIR(entry['mode']))
            entries=self._selected_entries(remote)
            add('下载所选到本机' if remote else '上传所选到路由器',lambda:self._transfer(remote),all(stat.S_ISREG(value['mode']) or stat.S_ISDIR(value['mode']) for value in entries))
            add('复制路径',lambda:self._copy_path(remote))
            if remote:
                menu.addSeparator()
                add('重命名 (F2)',lambda:self._mutate('rename'),len(entries)==1)
                add('删除 (Delete)',lambda:self._mutate('delete'))
                add('属性 / 权限',lambda:self._mutate('chmod'),len(entries)==1)
                if len(entries)==1:
                    menu.addSeparator()
                    if stat.S_ISDIR(entry['mode']):
                        add('在终端打开此目录',lambda:self.terminalRequested.emit(entry['path'],False))
                    elif runnable_entry(entry):
                        add('在终端运行',lambda:self.terminalRequested.emit(entry['path'],True))
            else:
                menu.addSeparator()
                add('删除 (Delete)',self._delete_local)
            menu.addSeparator()
        add('新建文件夹',lambda:self._mutate('mkdir') if remote else self._create_local_folder())
        add('刷新 (F5)',lambda:self._refresh(remote))
        try:menu.exec(view.viewport().mapToGlobal(pos))
        finally:menu.deleteLater()

    def _require_connection(self):
        """操作前同步真实连接，断连提示每次登录只弹一次。"""
        observer=getattr(getattr(self.service,"owner",None),"router_connection_state",None)
        if observer is not None:observer.reconcile()
        if self.service.manager.ssh.connected:return True
        self._apply_connection(None)
        if not self._disconnect_notified:
            self._disconnect_notified=True
            MessageDialog("路由器已断开","路由器连接已断开，请到“路由器登录”重新登录。",self).exec()
        return False

    def _navigate(self,remote,path,history_index=None,select_path=None):
        """每侧只保留最新目录请求，成功后再提交历史与地址。"""
        if not self._require_connection():return
        if not remote and not select_path:self._revealed_local_path=None
        for key in tuple(self._browse):
            if self._browse[key][0]==remote:
                self.service.cancel(key)
                self.pending.pop(key,None)
                self._browse.pop(key,None)
        key=self._submit('list' if remote else 'local_list','remote' if remote else 'local',path=path)
        if key:self._browse[key]=(remote,history_index,select_path)
        self._update_controls()

    def _history_step(self,remote,step):
        if not self._require_connection():return
        index=self._history_index[remote]+step
        history=self._history[remote]
        if 0<=index<len(history):self._navigate(remote,history[index],index)

    def _submit(self,operation,purpose,**args):
        if not self._require_connection():return
        try:
            key=self.service.submit(operation,**args)
            self.pending[key]=purpose
            self._update_controls()
            if purpose in {'local','remote'}:self._footers[purpose=='remote'].setText('正在读取目录…')
            return key
        except Exception as exc:
            MessageDialog('无法执行',str(exc),self).exec()

    def _refresh(self,remote):
        self._navigate(remote,(self.remote_path if remote else self.local_path).text())

    def _up(self,remote):
        if not self._require_connection():return
        current=self._current_path[remote]
        parent=(posixpath.dirname(current.rstrip('/')) or '/') if remote else str(Path(current).parent)
        if parent!=current:self._navigate(remote,parent)

    def _enter(self,remote,item):
        if item is None:return
        entry=item.data(0,Qt.UserRole)
        if stat.S_ISDIR(entry['mode']):self._navigate(remote,entry['path'])

    def _selected(self,remote=True):
        item=(self.remote_tree if remote else self.local_tree).currentItem()
        return item.data(0,Qt.UserRole) if item and not item.isHidden() else None

    def _selected_entries(self,remote=True):
        tree=self.remote_tree if remote else self.local_tree
        return [item.data(0,Qt.UserRole) for item in tree.selectedItems() if not item.isHidden()]

    def _confirm(self,text):
        ssh=self.service.manager.ssh
        connection=ssh._client
        fingerprint=getattr(ssh,'host_fingerprint',None)
        accepted=ConfirmDialog('确认文件操作',self._display(text),self).exec()==ConfirmDialog.Accepted
        if accepted:
            current=self.service.manager.ssh
            if (current is not ssh or not current.connected or current._client is not connection
                    or getattr(current,'host_fingerprint',None)!=fingerprint):
                self._notify('路由器连接已变化，本次文件操作未提交，请刷新并重新选择。','warning')
                return False
        return accepted

    def _transfer(self,download,entry=None,destination=None):
        entries=entry if isinstance(entry,(list,tuple)) else self._selected_entries(download) if entry is None else [entry]
        if not self._require_connection():return
        if len(entries)>1 or any(stat.S_ISDIR(value["mode"]) for value in entries):
            return self._transfer_batch(download,entries,destination)
        entry=entries[0] if entries else None
        if not entry or not stat.S_ISREG(entry['mode']):
            self._notify('请选择普通文件。','warning')
            return
        if download:
            local=Path(destination or self._current_path[False])/entry['name']
            local_expected=None
            if local.exists():
                if local.is_symlink() or not local.is_file():
                    MessageDialog('目标不可覆盖','目标必须是普通文件。',self).exec()
                    return
                if not self._confirm('覆盖本机文件：'+str(local)): return
                local_expected=self.service.manager.identity(local.lstat())
            key=self._submit('download','transfer',local=str(local),remote=entry['path'],expected=entry['identity'],local_expected=local_expected)
        else:
            remote=posixpath.join(destination or self._current_path[True],entry['name'])
            existing=None
            for index in range(self.remote_tree.topLevelItemCount()):
                candidate=self.remote_tree.topLevelItem(index).data(0,Qt.UserRole)
                if candidate['path']==remote: existing=candidate
            if not existing and remote.startswith(('/etc/','/usr/','/sbin/','/bin/','/lib/')) and not self._confirm('上传到系统目录：'+remote): return
            if existing and not self._confirm('覆盖文件：'+remote): return
            key=self._submit('upload','transfer',local=entry['path'],remote=remote,expected=existing['identity'] if existing else None)

        if key:
            dialog=FileTransferDialog('下载文件' if download else '上传文件',entry['path'],entry['size'],self)
            dialog.cancelRequested.connect(lambda:self.service.cancel(key))
            self._transfers[key]=dialog
            self._transfer_destinations[key]=(not download,str(local) if download else remote,self.service.manager.ssh._client)

    def _transfer_batch(self,download,entries,destination=None):
        if any(not (stat.S_ISREG(entry['mode']) or stat.S_ISDIR(entry['mode'])) for entry in entries):
            self._notify('文件传输不支持符号链接或特殊文件。','warning')
            return
        folders=[entry['path'] for entry in entries if stat.S_ISDIR(entry['mode'])]
        if folders and not self._confirm('复制以下文件夹及其全部内容（链接与特殊文件将拒绝）：\n'+'\n'.join(folders)):return
        jobs=[]
        existing_entries={self.remote_tree.topLevelItem(i).data(0,Qt.UserRole)['path']:
                          self.remote_tree.topLevelItem(i).data(0,Qt.UserRole)
                          for i in range(self.remote_tree.topLevelItemCount())}
        for entry in entries:
            if stat.S_ISDIR(entry['mode']):
                local=Path(destination or self._current_path[False])/entry['name'] if download else Path(entry['path'])
                remote=entry['path'] if download else posixpath.join(destination or self._current_path[True],entry['name'])
                if (download and (local.exists() or local.is_symlink())) or (not download and remote in existing_entries):
                    MessageDialog('目标文件夹已存在','为保留已有内容，本次不合并或覆盖同名文件夹。请换一个目录或先重命名。',self).exec()
                    return
                if not download and remote.startswith(('/etc/','/usr/','/sbin/','/bin/','/lib/')) and not self._confirm('上传文件夹到系统目录：'+remote):return
                args=dict(local=str(local),remote=remote,expected=entry['identity'])
                jobs.append(dict(operation='download_directory' if download else 'upload_directory',arguments=args,size=0,name=entry['name']))
                continue
            if download:
                local=Path(destination or self._current_path[False])/entry['name']
                expected=None
                if local.exists() or local.is_symlink():
                    if local.is_symlink() or not local.is_file():
                        self._notify('目标不是普通文件：'+str(local),'error')
                        return
                    if not self._confirm('覆盖本地文件：'+str(local)):return
                    expected=self.service.manager.identity(local.lstat())
                args=dict(local=str(local),remote=entry['path'],expected=entry['identity'],local_expected=expected)
            else:
                remote=posixpath.join(destination or self._current_path[True],entry['name'])
                existing=existing_entries.get(remote)
                if existing and not stat.S_ISREG(existing['mode']):
                    self._notify('目标不是普通文件：'+remote,'error')
                    return
                if existing and not self._confirm('覆盖路由器文件：'+remote):return
                if not existing and remote.startswith(('/etc/','/usr/','/sbin/','/bin/','/lib/')) and not self._confirm('上传到系统目录：'+remote):return
                args=dict(local=entry['path'],remote=remote,expected=existing['identity'] if existing else None)
            jobs.append(dict(operation='download' if download else 'upload',
                             arguments=args,size=entry['size'],name=entry['name']))
        title=('下载文件夹' if download else '上传文件夹') if len(jobs)==1 and folders else ('批量下载' if download else '批量上传')
        self._submit_batch(jobs,not download,title,destination)

    def _submit_batch(self,jobs,remote,title,destination=None):
        deleting=all(job['operation'] in {'delete','local_delete'} for job in jobs)
        key=self._submit('file_batch',('batch',remote,'delete' if deleting else 'transfer'),jobs=jobs)
        if key:
            if jobs[0]['operation'] in {'delete','local_delete','local_move','remote_move'}:
                from modules.common_widgets.task_progress_dialog import TaskProgressDialog
                dialog=TaskProgressDialog(title,f'共 {len(jobs)} 项',self,
                    progress_formatter=lambda completed,total:f'已完成 {completed}/{total} 项')
            else:
                dialog=FileTransferDialog(title,f'共 {len(jobs)} 项',sum(job['size'] for job in jobs),self)
                if any(job['operation'].endswith('_directory') for job in jobs):
                    dialog.update_stage('正在检查文件夹内容…')
                    dialog.show()
            dialog.cancelRequested.connect(lambda:self.service.cancel(key))
            self._transfers[key]=dialog
            self._transfer_destinations[key]=(remote,destination or self._current_path[remote],self.service.manager.ssh._client)

    def _delete_entries(self,remote):
        entries=self._selected_entries(remote)
        if not entries:return
        text=('永久删除路由器所选项目（含文件夹内全部子文件夹和文件）：' if remote else '将本地所选项目移到回收站（含文件夹内容）：')
        text+='\n'+'\n'.join(entry['path'] for entry in entries[:20])
        if len(entries)>20:text+=f'\n……共 {len(entries)} 项'
        if remote:text+='\n\n删除后无法恢复。符号链接只删除链接本身，不删除链接指向的内容。'
        if not self._confirm(text):return
        if len(entries)==1 and not remote:
            entry=entries[0]
            args=dict(path=entry['path'],expected=entry['identity'],parent=self._current_path[False])
            self._submit('local_delete','local_mutation',**args)
            return
        jobs=[]
        for entry in entries:
            args=dict(path=entry['path'],expected=entry['identity'])
            if not remote:args['parent']=self._current_path[False]
            jobs.append(dict(operation='delete' if remote else 'local_delete',arguments=args,
                             size=1,name=entry['name']))
        self._submit_batch(jobs,remote,'批量删除')

    def _create_local_folder(self):
        """使用公共名称弹窗，在本地当前浏览目录中创建并选中新文件夹。"""
        if not self._require_connection():return
        parent=self._current_path[False]
        dialog=ValueDialog('新建文件夹','新建文件夹',self)
        if dialog.exec()!=dialog.Accepted:return
        self._submit('local_mkdir','local_mkdir',parent=parent,name=dialog.edit.text())

    def _delete_local(self):
        self._delete_entries(False)

    def _mutate(self,action):
        if action=='delete':return self._delete_entries(True)
        if action in {'rename','chmod'} and len(self._selected_entries())!=1:return
        entry=self._selected()
        if action!='mkdir' and not entry: return
        value=None
        if action in {'mkdir','rename','chmod'}:
            title='权限 000-777' if action=='chmod' else '新建文件夹' if action=='mkdir' else '重命名'
            default=format(stat.S_IMODE(entry['mode']),'03o') if action=='chmod' else entry['name'] if action=='rename' else ''
            dialog=ValueDialog(title,default,self)
            if action=="chmod":
                dialog.content_layout.insertWidget(0,PrivacyLabel(entry["path"]+"\n"+str(entry["size"])+" bytes\nUID/GID: "+str(entry["identity"][3])+" / "+str(entry["identity"][4])+"\nmtime: "+str(entry["modified"]),dialog))
            if dialog.exec()!=dialog.Accepted: return
            value=dialog.edit.text().strip()
            if action!='chmod' and (not value or value in {'.','..'} or '/' in value): return
        path=posixpath.join(self._current_path[True],value) if action=='mkdir' else entry['path']
        if action!='mkdir' and not self._confirm({'rename':'重命名','delete':'删除','chmod':'修改权限'}[action]+'\n'+path+'\n系统目录的修改可能影响路由器功能。'): return
        if action=='rename': value=posixpath.join(posixpath.dirname(path),value)
        self._submit(action,'mutation',path=path,expected=entry['identity'] if entry else None,value=value)

    def _completed(self,key,result):
        purpose=self.pending.pop(key,None)
        browse=self._browse.pop(key,None)
        destination=self._transfer_destinations.pop(key,None)
        if purpose is None: return
        if isinstance(purpose,tuple) and purpose[0]=='external_upload':
            from .file_drop_actions import complete_external_upload
            complete_external_upload(self,purpose,result)
            return
        if isinstance(purpose,tuple) and purpose[0]=='batch':
            dialog=self._transfers.pop(key,None)
            completed=len(result['results'])
            deleting=len(purpose)>2 and purpose[2]=='delete'
            if deleting:
                action='已完整删除' if purpose[1] else '已移到回收站'
                message=f"{action} {completed}/{result['count']} 个所选项目。"
            else:
                message=f"已完成 {completed}/{result['count']} 项。"
            if result['cancelled']:
                if deleting and purpose[1]:
                    message+='任务已取消，当前文件夹可能已部分删除；已删除内容不会恢复。'
                elif deleting:
                    message+='任务已取消，已移到回收站的项目可从回收站恢复。'
                else:
                    message+='任务已取消，已完成的文件保留。'
            if result['error']:message+='未完成：'+result['error']
            level='error' if result['error'] else 'warning' if result['cancelled'] else 'success'
            self._notify(message,level)
            if dialog is not None:dialog.finish(not result['error'] and not result['cancelled'],message)
            remote,folder,connection=destination
            if self.service.manager.ssh.connected and self.service.manager.ssh._client is connection:
                transfer=isinstance(dialog,FileTransferDialog)
                self._navigate(remote,folder,select_path=result['results'] if result['results'] and transfer else None)
            return
        dialog=self._transfers.pop(key,None)
        if dialog is not None:dialog.finish(True)
        if self.info_panel is not None and purpose not in {'local','remote'}:
            self.info_panel.append_info('文件操作完成。','success')
        if purpose in {'local','remote'}:
            tree=self.local_tree if purpose=='local' else self.remote_tree
            edit=self.local_path if purpose=='local' else self.remote_path
            selected={entry['path'] for entry in self._selected_entries(purpose=='remote')}
            sorting=tree.isSortingEnabled()
            tree.setSortingEnabled(False)
            tree.setUpdatesEnabled(False)
            try:
                tree.clear()
                edit.setText(result['path'])
                remote=purpose=='remote'
                self._current_path[remote]=result['path']
                history=self._history[remote]
                if browse is not None and browse[1] is not None:
                    self._history_index[remote]=browse[1]
                elif self._history_index[remote]<0 or history[self._history_index[remote]]!=result['path']:
                    del history[self._history_index[remote]+1:]
                    history.append(result['path'])
                    if len(history)>100:history.pop(0)
                    self._history_index[remote]=len(history)-1
                items=[]
                for entry in result['entries']:
                    modified=datetime.fromtimestamp(entry['modified']).strftime('%Y-%m-%d %H:%M') if entry['modified'] else ''
                    item=FileItem([self._display(entry['name']),'' if stat.S_ISDIR(entry['mode']) else readable_size(entry['size']),file_type(entry),modified,stat.filemode(entry['mode']) if remote else ''])
                    item.setIcon(0,self._folder_icons[remote] if stat.S_ISDIR(entry['mode']) else self._file_icon)
                    item.setTextAlignment(1,Qt.AlignRight|Qt.AlignVCenter)
                    item.setData(0,Qt.UserRole,entry)
                    item.setToolTip(0,self._display(entry['path']))
                    items.append(item)
                tree.addTopLevelItems(items)
                if selected:
                    for item in items:
                        if item.data(0,Qt.UserRole)['path'] in selected:
                            item.setSelected(True)
                            tree.setCurrentItem(item,0,QItemSelectionModel.NoUpdate)
            finally:
                tree.setSortingEnabled(sorting)
                tree.setUpdatesEnabled(True)
            self._filter(purpose=='remote')
            if browse and browse[2]:
                self._select_received(purpose=='remote',browse[2])
        elif purpose=='transfer' and destination:
            remote,path,connection=destination
            ssh=self.service.manager.ssh
            if not ssh.connected or ssh._client is not connection:
                self._notify('传输结束时连接已变化，未自动定位到其他设备。','warning')
                return
            path=str(result) if isinstance(result,(str,Path)) else path
            folder=posixpath.dirname(path) if remote else str(Path(path).parent)
            self._navigate(remote,folder,select_path=path)
            self._navigate(not remote,self._current_path[not remote])
        elif purpose=='local_mkdir':
            self._navigate(False,str(Path(result).parent),select_path=str(result))
        elif purpose=='local_mutation':
            self._navigate(False,self._current_path[False])
        else:
            self._navigate(True,self._current_path[True])

    def _select_received(self,remote,path):
        """目录成功刷新后选中全部接收文件，将首个文件滚动到可见区域。"""
        stack,tree,icons=self._views[remote]
        paths=path if isinstance(path,(list,tuple)) else [path]
        self._search[remote].clear()
        if not remote:self._revealed_local_path=paths
        self._filter(remote)
        path_keys=set(paths) if remote else {os.path.normcase(os.path.normpath(str(value))) for value in paths}
        tree.clearSelection()
        first=None
        for index in range(tree.topLevelItemCount()):
            item=tree.topLevelItem(index)
            target=item.data(0,Qt.UserRole)['path']
            if (target if remote else os.path.normcase(os.path.normpath(target))) in path_keys:
                item.setSelected(True)
                if first is None:first=item
        if first is not None:
            tree.setCurrentItem(first,0,QItemSelectionModel.NoUpdate)
            view=stack.currentWidget()
            view.scrollTo(tree.indexFromItem(first),QAbstractItemView.PositionAtCenter)
            view.setFocus(Qt.OtherFocusReason)
        self._update_controls()

    def _failed(self,key,message):
        browse=self._browse.pop(key,None)
        self._transfer_destinations.pop(key,None)
        if self.pending.pop(key,None) is not None:
            if browse is not None:
                remote=browse[0]
                (self.remote_path if remote else self.local_path).setText(self._current_path[remote])
            if not self.service.manager.ssh.connected:self._require_connection()
            elif browse is not None and (not browse[0] or not self._dependency_prompt.handle_failure(message)):MessageDialog("无法打开目录",message,self).exec()
            self._update_controls()
            dialog=self._transfers.pop(key,None)
            if dialog is not None:dialog.finish(False,message)
            if self.info_panel is not None:self.info_panel.append_info(message,'error')

    def _progress(self,key,value):
        if key in self.pending:
            dialog=self._transfers.get(key)
            if dialog is not None:
                dialog.update_progress(value)
                if value.get('stage') and not value['total']:dialog.update_stage(value['stage'])
                if 'count' in value:
                    dialog.path_label.setText(f"第 {value['index']}/{value['count']} 项：{value['name']}")

    def _cancel(self):
        keys=tuple(self.pending)
        self.pending.clear()
        self._browse.clear()
        self._transfer_destinations.clear()
        self.local_path.setText(self._current_path[False])
        self.remote_path.setText(self._current_path[True])
        self._update_controls()
        for key in keys: self.service.cancel(key)
        for dialog in self._transfers.values():dialog.finish(True)
        self._transfers.clear()

    def dispose(self):
        """释放订阅并取消本页请求，不影响其他任务。"""
        self._cancel()
        self._dependency_prompt.dispose()
        for signal,slot in ((self.service.completed,self._completed),(self.service.failed,self._failed),(self.service.progress,self._progress)):
            try:signal.disconnect(slot)
            except (TypeError,RuntimeError):pass
        if self._privacy is not None:
            try:self._privacy.changed.disconnect(self._refresh_privacy)
            except (TypeError,RuntimeError):pass
        owner=getattr(self.service,'owner',None)
        if owner is not None:
            try:owner.openwrtSnapshotChanged.disconnect(self._apply_connection)
            except (TypeError,RuntimeError):pass
