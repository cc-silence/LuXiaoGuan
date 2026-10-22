"""通用无边框任务进度弹窗，沿用公共主题和缩放。"""
import time
from PySide6.QtCore import QEvent, Signal
from .rounded_progress_bar import RoundedProgressBar as QProgressBar
from .frameless_dialog import FramelessDialog
from .common_dialogs import apply_common_dialog_theme, _create_dialog_button
from modules.display_privacy import PrivacyLabel
from modules.dialog_theme import detect_light_theme, task_progress_stylesheet
from modules.ui_scale_manager import UiScaleManager


class TaskProgressDialog(FramelessDialog):
    """公共后台任务进度弹窗；主线程更新，取消只发信号，完成后回收。"""
    cancelRequested=Signal()

    def __init__(self,title,detail='',parent=None,*,show_immediately=True,
                 cancel_text='取消任务',progress_formatter=None):
        """调用方连接 cancelRequested，调用 update_progress 与 finish；本类不运行任务。"""
        super().__init__(title,parent,inherit_parent_stylesheet=False)
        self.started=time.monotonic()
        self.running=True
        self.cancelling=False
        self.path_label=PrivacyLabel(detail,self)
        self.path_label.setWordWrap(True)
        self.path_label.setVisible(bool(detail))
        self.progress_formatter=progress_formatter
        self.message=PrivacyLabel('等待任务执行…',self)
        self.message.setWordWrap(True)
        self.bar=QProgressBar(self)
        self.bar.setObjectName('taskDialogProgressBar')
        self.bar.setTextVisible(False)
        self.bar.setRange(0,1000)
        self.bar.setValue(0)
        self.content_layout.addWidget(self.path_label)
        self.content_layout.addWidget(self.message)
        self.content_layout.addWidget(self.bar)
        self.cancel_button=_create_dialog_button(cancel_text,self,'settingsSecondaryButton')
        self.cancel_button.clicked.connect(self.request_cancel)
        self.button_layout.addWidget(self.cancel_button)
        apply_common_dialog_theme(self,parent)
        scale = UiScaleManager.for_widget(self)
        self.bar.setStyleSheet(task_progress_stylesheet(detect_light_theme(self), scale))
        self.set_scaled_size((450, 0))
        self._content_height_ready = True
        if show_immediately:
            self.show()

    def _fit_content_height(self):
        """\u6309\u5f53\u524d\u5bbd\u5ea6\u8ba1\u7b97\u6362\u884c\u540e\u7684\u5185\u5bb9\u9ad8\u5ea6\uff0c\u4e0d\u4fdd\u7559\u7a7a\u767d\u5360\u4f4d\u3002"""
        layout = self.layout()
        height = layout.totalHeightForWidth(self.width())
        if height < 0:
            height = layout.totalSizeHint().height()
        available = self.screen().availableGeometry().height() - 24
        height = min(height, max(1, available))
        if height != self.height():
            self.resize(self.width(), height)

    def showEvent(self, event):
        """\u9996\u5e27\u5b8c\u6210\u4e3b\u9898\u548c\u7f29\u653e\u540e\u7acb\u5373\u6309\u5185\u5bb9\u6536\u7d27\u3002"""
        super().showEvent(event)
        self._fit_content_height()

    def event(self, event):
        """\u6587\u5b57\u6216\u6309\u94ae\u53d8\u5316\u7531 Qt \u5e03\u5c40\u901a\u77e5\u8c03\u6574\uff0c\u65e0\u5b9a\u65f6\u68c0\u67e5\u3002"""
        handled = super().event(event)
        if (event.type() == QEvent.LayoutRequest
                and getattr(self, '_content_height_ready', False)
                and self.isVisible()):
            self._fit_content_height()
        return handled

    def update_stage(self,message):
        """总进度未知时显示忙碌条和实际阶段，不编造完成百分比。"""
        if not self.running:return
        self.bar.setRange(0,0)
        if not self.cancelling:self.message.setText(str(message))

    def update_progress(self,value):
        if not self.running:return
        total=max(0,int(value['total']))
        completed=max(0,int(value['completed']))
        self.bar.setRange(0,1000 if total else 0)
        self.bar.setValue(min(1000,round(completed*1000/total)) if total else 0)
        if not self.cancelling and total:
            self.message.setText(self.progress_formatter(completed,total) if self.progress_formatter else f'{min(100,round(completed*100/total)) if total else 0}%')
        if not self.isVisible() and time.monotonic()-self.started>=1:
            self.show()

    def request_cancel(self):
        if not self.running:
            self.accept()
            return
        if self.cancelling:return
        self.cancelling=True
        self.cancel_button.setEnabled(False)
        self.message.setText('正在取消，请等待任务收尾…')
        self.cancelRequested.emit()

    def finish(self,success,message=''):
        self.running=False
        if success or self.cancelling:
            self.accept()
            self.deleteLater()
        else:
            self.message.setText(message)
            self.cancel_button.setText('关闭')
            self.cancel_button.setEnabled(True)
            self.finished.connect(self.deleteLater)
            self.show()

    def reject(self):
        if self.running:self.request_cancel()
        else:super().reject()

    def closeEvent(self,event):
        if self.running:
            event.ignore()
            self.request_cancel()
        else:super().closeEvent(event)
