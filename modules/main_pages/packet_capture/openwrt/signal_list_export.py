"""信号列表导出的页面控制器，文件写入交给后台。"""

from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.config_manager import user_downloads_dir


class _ExportSignals(QObject):
    finished = Signal(bool, str, int)


class _ExportJob(QRunnable):
    """只持有信号快照和取消标记，不在后台访问界面或路由器。"""

    def __init__(self, points, path):
        super().__init__()
        self.points = points
        self.path = path
        self.cancel = Event()
        self.signals = _ExportSignals()

    def run(self):
        try:
            from modules.capture_services.signal_list_export import export_signal_list
            count = export_signal_list(self.points, self.path, self.cancel)
            self.signals.finished.emit(True, self.path, count)
        except InterruptedError:
            self.signals.finished.emit(False, '', 0)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception('Signal list export failed')
            self.signals.finished.emit(False, str(exc), 0)
        finally:
            self.points = ()


class SignalListExportController(QObject):
    """主线程选路径和反馈结果；页面释放时取消属于自己的导出。"""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self._job = None
        self._disposed = False
        self._has_points = False

    def set_has_points(self, value):
        self._has_points = bool(value)
        self.page.tools.export_signals_button.setEnabled(self._has_points and self._job is None)

    def export(self):
        """冻结点击时的全部信号，用系统保存窗口选择格式并开始后台导出。"""
        if self._disposed or self._job is not None:
            return
        points = tuple(self.page.service.openwrt_snapshot.access_points)
        if not points:
            MessageDialog('导出信号列表', '请先扫描信号，有结果后再导出。', self.page).exec()
            return
        filename = '信号列表_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.xlsx'
        path, selected_filter = QFileDialog.getSaveFileName(
            self.page, '导出信号列表', str(user_downloads_dir() / filename),
            'Excel (*.xlsx);;文本文件 (*.txt)',
        )
        if not path or self._disposed:
            return
        if Path(path).suffix.lower() not in {'.xlsx', '.txt'}:
            path += '.txt' if '*.txt' in selected_filter else '.xlsx'
            if Path(path).exists() and ConfirmDialog(
                '确认覆盖', f'文件已存在：{path}\n是否覆盖？', self.page,
                yes_text='覆盖', no_text='取消',
            ).exec() != ConfirmDialog.Accepted:
                return
        job = _ExportJob(points, path)
        self._job = job
        job.signals.finished.connect(self._finished)
        self.page.tools.export_signals_button.setText('正在导出…')
        self.set_has_points(self._has_points)
        QThreadPool.globalInstance().start(job)

    @Slot(bool, str, int)
    def _finished(self, success, detail, count):
        self._job = None
        if self._disposed:
            return
        self.page.tools.export_signals_button.setText('导出信号列表')
        self.set_has_points(self._has_points)
        if success:
            message = f'已导出 {count} 个信号（按信号由强到弱）：{detail}'
            if self.page.info_panel is not None:
                self.page.info_panel.append_info(message, 'success')
            else:
                MessageDialog('导出完成', message, self.page).exec()
        elif detail:
            MessageDialog('导出失败', f'无法保存信号列表。\n原因：{detail}\n请检查目录权限，并关闭占用文件的程序后重试。', self.page).exec()

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        if self._job is not None:
            self._job.cancel.set()
            self._job.signals.finished.disconnect(self._finished)
