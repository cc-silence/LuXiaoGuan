"""Compact modeless records viewer with bounded, scroll-driven background reads."""
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QAbstractItemView, QApplication, QHBoxLayout, QLabel, QTabWidget, QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme, ConfirmDialog
from modules.common_widgets.auto_hide_scroll_bar import AutoHideScrollBar
from modules.common_widgets.spaced_item_delegate import SpacedItemDelegate
from modules.common_widgets.floating_feedback import FloatingFeedback
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.dialog_theme import detect_light_theme
from modules.capture_services.password_results_reader import PasswordResultsReader
from modules.ui_scale_manager import UiScaleManager
from .password_status_display import LABELS, password_state_color


class PasswordResultsDialog(FramelessDialog):
    def __init__(self, service, directory, parent=None):
        super().__init__('\u731c\u5bc6\u7801\u7ed3\u679c', parent, inherit_parent_stylesheet=False)
        self.service = service
        self.scale_manager = UiScaleManager.for_widget(parent)
        self.light_theme = bool(getattr(parent, 'light_theme', detect_light_theme(parent)))
        self._closed = self._loading = self._found = False
        self._next_page = 0
        self._has_more = False
        self._seen = set()
        self.copy_feedback = FloatingFeedback(self)
        apply_common_dialog_theme(self, parent, theme_name='light.qss' if self.light_theme else 'dark.qss')
        from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
        self.setStyleSheet(self.styleSheet()+'\n'+CapturePageStyleManager(self)._capture_stylesheet(self.light_theme))
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowModality(Qt.NonModal)
        self.setModal(False)
        self.set_scaled_size((680,360),(900,520))
        self.scale_manager.bind_layout(self.content_layout, margins=(16,12,16,12), spacing=6)
        toolbar = QHBoxLayout()
        self.clear_button = create_page_text_button(self,'\u6e05\u7406\u8bb0\u5f55','\u6e05\u7406\u5df2\u8bd5\u8bb0\u5f55\u548c\u5df2\u731c\u51fa\u5bc6\u7801\u8bb0\u5f55')
        self.clear_button.setProperty('dangerAction',True)
        self.clear_button.setProperty('compactAction',True)
        self.refresh = create_page_text_button(self,'\u5237\u65b0','\u91cd\u65b0\u8bfb\u53d6\u5f53\u524d\u5206\u7c7b\u7684\u8bb0\u5f55')
        self.refresh.setProperty('compactAction',True)
        self.clear_button.setFixedSize(self.scale_manager.px(110),self.scale_manager.px(30))
        self.refresh.setFixedSize(self.scale_manager.px(70),self.scale_manager.px(30))
        self.counter = QLabel(self)
        self.counter.setObjectName('captureDescription')
        toolbar.addWidget(self.counter)
        toolbar.addStretch(1)
        toolbar.addWidget(self.clear_button)
        toolbar.addWidget(self.refresh)
        self.content_layout.addLayout(toolbar)
        self.tabs = QTabWidget(self)
        # Same shared tab component and theme as automatic capture.
        self.tabs.setObjectName('openwrtCaptureTabs')
        self.tables = []
        for found,text in ((False,'\u5df2\u8bd5\u8bb0\u5f55'),(True,'\u5df2\u731c\u51fa\u5bc6\u7801')):
            page = QWidget(self.tabs)
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0,0,0,0)
            table = self._make_table(page,found)
            self.tables.append(table)
            layout.addWidget(table)
            self.tabs.addTab(page,text)
        self.content_layout.addWidget(self.tabs,1)
        self.hint = QLabel(self)
        self.hint.setObjectName('captureDescription')
        self.content_layout.addWidget(self.hint)
        from modules.display_privacy import controller_for
        self._privacy = controller_for(parent)
        if self._privacy is not None:
            self._privacy.changed.connect(self._refresh_privacy)
        self.reader = PasswordResultsReader(service,directory,service.openwrt_snapshot.access_points)
        self.reader.completed.connect(self._loaded)
        self.reader.failed.connect(self._failed)
        self.reader.finished.connect(self._finished)
        self.tabs.currentChanged.connect(self._switch)
        self.clear_button.clicked.connect(self._clear)
        self.refresh.clicked.connect(lambda: self._load(reset=True))
        self.finished.connect(self._close)
        self.destroyed.connect(self.reader.close)
        service.openwrtSnapshotChanged.connect(self._sync_buttons)
        service.password_history.recordsCleared.connect(self._reload)
        self._load(reset=True)

    @property
    def table(self):
        return self.tables[int(self._found)]

    def _make_table(self,parent,found):
        headers = ('\u4fe1\u53f7\u540d\u79f0','MAC','\u6700\u7ec8\u5bc6\u7801') if found else ('\u4fe1\u53f7\u540d\u79f0','MAC','\u7ed3\u679c','\u5df2\u8bd5/\u603b\u6570')
        table = QTableWidget(0,len(headers),parent)
        table.setObjectName('captureAccessPointTable')
        table.setProperty('passwordRecords',True)
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectItems)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setItemDelegate(SpacedItemDelegate(table, show_focus_rect=False, minimum_height=False))
        table.setSortingEnabled(True)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(self.scale_manager.px(30))
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(len(headers)):
            stretch = column in ((0,2) if found else (0,))
            header.setSectionResizeMode(column,QHeaderView.Stretch if stretch else QHeaderView.Interactive)
        table.setColumnWidth(1,table.fontMetrics().horizontalAdvance('AA:BB:CC:DD:EE:FF')+self.scale_manager.px(24))
        if not found:
            table.setColumnWidth(2,self.scale_manager.px(90))
            table.setColumnWidth(3,self.scale_manager.px(110))
        table.setVerticalScrollBar(AutoHideScrollBar(Qt.Vertical,table))
        table.setHorizontalScrollBar(AutoHideScrollBar(Qt.Horizontal,table))
        table.verticalScrollBar().valueChanged.connect(self._maybe_more)
        table.cellClicked.connect(self._copy)
        return table

    def _switch(self,index):
        self._has_more = False
        self.table.clearContents()
        self.table.setRowCount(0)
        self._found = index == 1
        self._load(reset=True)

    def _load(self,*,reset=False):
        if self._closed or self._loading:
            return
        self._loading = True
        if reset:
            self._next_page = 0
            self._has_more = False
            self._seen.clear()
            self.table.clearContents()
            self.table.setRowCount(0)
            self.hint.setText('\u70b9\u51fb\u6700\u7ec8\u5bc6\u7801\u53ef\u590d\u5236\u3002' if self._found else '\u5404\u5b57\u5178\u8fdb\u5ea6\u5408\u8ba1\uff1b\u672a\u786e\u8ba4\u4e0d\u8ba1\u5165\u5df2\u8bd5\uff1b? \u8868\u793a\u65e7\u8bb0\u5f55\u7f3a\u5c11\u603b\u6570\u3002')
        self._loading = self.reader.load(self._found,self._next_page)
        self.counter.setText('\u6b63\u5728\u8bfb\u53d6\u2026')
        self._sync_buttons()

    @Slot(object)
    def _loaded(self,data):
        if self._closed:
            for row in data['rows']:row['password']=''
            return
        self._next_page = data['page']+1
        if self._privacy is not None:
            self._privacy.register_values(value for row in data['rows'] for value in (row['ssid'],row['bssid']))
        self.table.setSortingEnabled(False)
        added = 0
        for row in data['rows']:
            key = (row['ssid'],row['bssid'])
            if key in self._seen:
                row['password']=''
                continue
            self._seen.add(key)
            index = self.table.rowCount()
            self.table.insertRow(index)
            added += 1
            values = (row['ssid'],row['bssid'],row['password'] or '\u7ed3\u679c\u6587\u4ef6\u4e0d\u53ef\u7528') if self._found else (row['ssid'],row['bssid'],LABELS.get(row['state'],(row['state'],''))[0],row['progress'])
            for column,value in enumerate(values):
                raw = value
                if column<2 and self._privacy is not None:value=self._privacy.text(value)
                item = QTableWidgetItem(value)
                if column<2:item.setData(Qt.UserRole+1,raw)
                if column==2:
                    color=password_state_color(row['state'],self.light_theme)
                    if color is not None:item.setForeground(color)
                item.setToolTip('\u70b9\u51fb\u590d\u5236\u5bc6\u7801' if self._found and column==2 and row['password'] else value)
                if self._found and column==2:item.setData(Qt.UserRole,bool(row['password']))
                self.table.setItem(index,column,item)
            self.table.resizeRowToContents(index)
            self.table.setRowHeight(index,min(self.scale_manager.px(90),max(self.scale_manager.px(30),self.table.rowHeight(index))))
            row['password']=''
        self.table.setSortingEnabled(True)
        count=self.table.rowCount()
        self._has_more=bool(added and count<data['total'])
        self.counter.setText(f"\u5df2\u663e\u793a {count}/{data['total']}" if self._has_more else f"\u5171 {count} \u4e2a\u4fe1\u53f7")
        self.counter.setMinimumWidth(self.counter.fontMetrics().horizontalAdvance(self.counter.text())+self.scale_manager.px(12))

    def _maybe_more(self,*_args):
        if self._closed or self._loading or not self._has_more:return
        bar=self.table.verticalScrollBar()
        if self.table.isVisible() and bar.maximum()>0 and bar.value()>=bar.maximum()-3:self._load()

    @Slot(str)
    def _failed(self,message):
        if not self._closed:
            self._has_more=False
            self.counter.setText(message)

    @Slot()
    def _finished(self):
        self._loading=False
        if not self._closed:
            self._sync_buttons()

    def _sync_buttons(self,*_args):
        idle=not self._loading
        self.clear_button.setEnabled(idle and not self.service.task_controller.busy)
        self.refresh.setEnabled(idle)
        self.tabs.tabBar().setEnabled(idle)

    def _refresh_privacy(self):
        if self._closed or self._privacy is None:return
        for row in range(self.table.rowCount()):
            for column in (0,1):
                item=self.table.item(row,column)
                if item is not None:
                    text=self._privacy.text(item.data(Qt.UserRole+1) or '')
                    item.setText(text)
                    item.setToolTip(text)

    def _reload(self):
        if not self._closed:self._load(reset=True)

    def _clear(self):
        if self.service.task_controller.busy:return
        if ConfirmDialog('\u6e05\u7406\u8bb0\u5f55','\u6e05\u7a7a\u5df2\u8bd5\u8bb0\u5f55\u548c\u5df2\u731c\u51fa\u5bc6\u7801\u8bb0\u5f55\u3002\n\u4e0d\u5220\u9664\u5b57\u5178\u548c\u5df2\u5bfc\u51fa\u7684\u5bc6\u7801\u6587\u672c\u6587\u4ef6\u3002',self).exec()==ConfirmDialog.Accepted:
            self.service.password_history.clear_async()

    def _copy(self,row,column):
        item=self.table.item(row,column)
        if self._found and column==2 and item is not None and item.data(Qt.UserRole):
            QApplication.clipboard().setText(item.text())
            viewport = self.table.viewport()
            rect = self.table.visualItemRect(item)
            position = viewport.mapFromGlobal(QCursor.pos())
            if not rect.contains(position):
                position = rect.center()
            self.copy_feedback.show_feedback('\u5bc6\u7801\u5df2\u590d\u5236', 'success',
                                             self.scale_manager, anchor=viewport.mapTo(self, position))

    def _close(self,*_args):
        if self._closed:return
        self._closed=True
        self.copy_feedback.dismiss()
        for table in self.tables:table.clearContents()
        self._seen.clear()
        self.reader.close()
        self.service.openwrtSnapshotChanged.disconnect(self._sync_buttons)
        self.service.password_history.recordsCleared.disconnect(self._reload)


def show_password_results(page):
    service = page.service
    window = getattr(service, '_password_results_dialog', None)
    if window is not None:
        window.show()
        window.raise_()
        window.activateWindow()
        return
    previous = getattr(service, "_password_results_reader", None)
    if previous is not None and previous.busy and previous.closed:
        service.messageGenerated.emit("\u4e0a\u6b21\u8bb0\u5f55\u8bfb\u53d6\u6b63\u5728\u6536\u5c3e\uff0c\u8bf7\u7a0d\u540e\u6253\u5f00\u3002", "info")
        return
    from modules.config_services.capture_settings_manager import CaptureSettingsManager
    directory = CaptureSettingsManager.normalized_output_dir(page.settings.get('output_dir')) / '\u6700\u7ec8\u7ed3\u679c'
    window = PasswordResultsDialog(service, directory, page.window())
    service._password_results_dialog = window
    window.destroyed.connect(lambda: setattr(service, '_password_results_dialog', None))
    window.show()
