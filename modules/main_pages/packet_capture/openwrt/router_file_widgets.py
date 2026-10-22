"""文件浏览视图和传输弹窗；复用公共主题、委托和后台取消入口。"""
import stat
from uuid import uuid4
from PySide6.QtCore import Qt,QMimeData,QSize,QPoint
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QTreeWidget,QListView,QAbstractItemView
from modules.common_widgets.spaced_item_delegate import SpacedItemDelegate
from modules.common_widgets.task_progress_dialog import TaskProgressDialog
from modules.ui_scale_manager import UiScaleManager


class FileDragMixin:
    """同栏拖入文件夹移动；跨栏上传下载，拒绝外部拖放。"""
    MIME='application/x-winbox-file-copy'

    def setup_transfer(self,page,remote):
        self.file_page=page
        self.remote=remote
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)

    def startDrag(self,actions):
        entries=self.file_page._selected_entries(self.remote)
        if not entries or any(not (stat.S_ISREG(item['mode']) or stat.S_ISDIR(item['mode'])) for item in entries):return
        token=uuid4().hex
        self.file_page._drag_file=(token,[dict(entry) for entry in entries],self)
        mime=QMimeData()
        mime.setData(self.MIME,token.encode('ascii'))
        drag=QDrag(self)
        drag.setMimeData(mime)
        try:drag.exec(Qt.CopyAction|Qt.MoveAction,Qt.MoveAction)
        finally:self.file_page._drag_file=None

    def can_drop(self,event):
        value=getattr(self.file_page,'_drag_file',None)
        return bool(value and event.source() is value[2]
                    and event.mimeData().hasFormat(self.MIME)
                    and bytes(event.mimeData().data(self.MIME))==value[0].encode('ascii')
                    and self.file_page.service.manager.ssh.connected)

    def _drop_folder(self,event):
        position=event.position() if hasattr(event,'position') else event.pos()
        if hasattr(position,'toPoint'):position=position.toPoint()
        if not isinstance(position,QPoint):return None
        index=self.indexAt(position)
        entry=index.siblingAtColumn(0).data(Qt.UserRole) if index.isValid() else None
        return entry if isinstance(entry,dict) and stat.S_ISDIR(entry['mode']) else None

    def dragEnterEvent(self,event):
        if self.can_drop(event):
            source=self.file_page._drag_file[2]
            event.setDropAction(Qt.MoveAction if source.remote==self.remote else Qt.CopyAction)
            event.accept()
        else:event.ignore()

    def dragMoveEvent(self,event):
        self.dragEnterEvent(event)
        if self.can_drop(event) and self.file_page._drag_file[2].remote==self.remote and self._drop_folder(event) is None:
            event.ignore()

    def dropEvent(self,event):
        if not self.can_drop(event):
            event.ignore()
            return
        source=self.file_page._drag_file[2]
        folder=self._drop_folder(event)
        if source.remote==self.remote and folder is None:
            event.ignore()
            return
        entries=self.file_page._drag_file[1]
        event.setDropAction(Qt.MoveAction if source.remote==self.remote else Qt.CopyAction)
        event.accept()
        from .file_drop_actions import handle_file_drop
        handle_file_drop(self.file_page,source.remote,self.remote,entries,folder)


class FileTree(FileDragMixin,QTreeWidget):
    """详细列表按整行选择，保留键盘焦点但不绘制虚线框。"""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setItemDelegate(SpacedItemDelegate(self,show_focus_rect=False))
        self._fitting_name=False

    def fit_name_column(self):
        """名称列至少保留可读宽度；窄窗口允许横向滚动，不挤成几个字。"""
        if self._fitting_name:return
        self._fitting_name=True
        try:
            other=sum(self.columnWidth(column) for column in range(1,self.columnCount()) if not self.isColumnHidden(column))
            width=max(UiScaleManager.for_widget(self).px(200),self.viewport().width()-other)
            if self.columnWidth(0)!=width:self.setColumnWidth(0,width)
        finally:self._fitting_name=False

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.fit_name_column()


class FileIcons(FileDragMixin,QListView):
    """图标视图直接共享详细列表模型与选择模型，不复制文件元数据。"""
    def __init__(self,tree,parent=None):
        super().__init__(parent)
        self.setObjectName('routerFileIcons')
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setModel(tree.model())
        self.setModelColumn(0)
        self.setSelectionModel(tree.selectionModel())
        self.setItemDelegate(SpacedItemDelegate(self,show_focus_rect=False))
        UiScaleManager.for_widget(self).bind_metrics(self,icon_size=48)
        self.refresh_grid()

    def refresh_grid(self):
        scale=UiScaleManager.for_widget(self)
        self.setGridSize(QSize(scale.px(120),max(scale.px(96),self.fontMetrics().lineSpacing()*2+scale.px(56))))

    def resizeEvent(self,event):
        self.refresh_grid()
        super().resizeEvent(event)


class FileTransferDialog(TaskProgressDialog):
    """文件传输适配公共进度弹窗；快速小文件保持静默。"""
    LARGE_FILE=8*1024*1024

    def __init__(self,title,path,total,parent):
        queued=getattr(getattr(parent.service,'owner',None),'is_busy',False)
        super().__init__(title,path,parent,show_immediately=total>=self.LARGE_FILE or queued,
                         cancel_text='取消文件任务',progress_formatter=self._format_progress)

    @staticmethod
    def _format_progress(completed,total):
        return f'{min(100,round(completed*100/total)) if total else 0}% · {completed/1048576:.1f} / {total/1048576:.1f} MB'

    def update_progress(self,value):
        super().update_progress(value)
        if self.running and int(value['total'])>=self.LARGE_FILE:self.show()
