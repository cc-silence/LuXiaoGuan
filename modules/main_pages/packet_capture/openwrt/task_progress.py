"""OpenWrt 抓包任务进度组件。"""

from modules.display_privacy import PrivacyLabel, PrivacyProgressBar
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QWidget


class CaptureTaskProgress(QWidget):
    """把 CaptureProgress 模型紧凑显示为单行标题和进度条。

    详细进度说明保存在进度条 tooltip 中，避免单独说明标签长期占用垂直空间。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("captureTaskProgress")
        # 纵向固定：外层 QVBoxLayout 不会拉伸本行；中间弹性空白把它顶在表格区
        # 最下方，剩余空间留在表格与进度条之间而不是把进度条自身拉高。
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        # 空闲时整行隐藏，避免没有真实任务却长期显示无意义的 0%。
        self.hide()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.title_label = PrivacyLabel("", self)
        self.title_label.setObjectName("captureProgressTitle")
        self.title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.title_label.hide()
        self.progress_bar = PrivacyProgressBar(self)
        self.progress_bar.setObjectName("captureProgressBar")
        # 扫描不报告可靠百分比，使用细状态条表达运行中，避免大进度条制造虚假的完成感。
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.title_label)
        layout.addWidget(self.progress_bar, 1)

    def apply_progress(self, progress, task_state="idle"):
        """显示服务快照中的进度，不启动定时器或任务。"""

        active = task_state not in {"idle", "failed"}
        self.setVisible(active)
        if not active:
            self.title_label.clear()
            self.title_label.hide()
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            self.progress_bar.setToolTip("")
            return
        title = str(progress.title or "")
        if not title:
            title = "正在处理"
        self.title_label.setText(title)
        self.title_label.setVisible(bool(title))
        detail = str(progress.detail or "")
        self.title_label.setToolTip(detail)
        self.progress_bar.setToolTip(detail)
        if progress.indeterminate:
            self.progress_bar.setRange(0, 0)
        else:
            total = max(1, int(progress.total or 0))
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(max(0, min(int(progress.current or 0), total)))
