"""OpenWrt 抓包页面的 Windows 本地资源选择协作者。"""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QLineEdit

from modules.config_services.capture_settings_manager import CaptureSettingsManager


class OpenWrtResourcePicker:
    """集中处理设置页和高级 Pin 弹窗的本地路径选择。

    参数：parent 是文件对话框父窗口；settings_manager 用于解析便携路径。
    返回值：choose_* 成功时回填 QLineEdit 并返回路径，取消时返回空字符串。
    副作用：显示系统文件或目录选择器；不创建目录、不打开资源管理器。
    调用限制：只处理 Windows 本地路径，不能用于 OpenWrt POSIX 远端目录。
    """

    def __init__(self, parent, settings_manager=None):
        self.parent = parent
        self.settings_manager = settings_manager or CaptureSettingsManager()

    def choose_output_dir(self, line_edit):
        """选择基础目录并回填自动追加 `\u8def\u5c0f\u7ba1\u6293\u5305` 后的绝对路径。"""

        start = self._start_path(line_edit, directory=True)
        selected = QFileDialog.getExistingDirectory(self.parent, "选择抓包结果目录", start)
        if not selected:
            return ""
        normalized = self.settings_manager.normalized_output_dir(selected)
        return self._apply_selection(line_edit, str(normalized))

    def choose_private_key(self, line_edit):
        """选择 SSH 私钥文件。"""

        start = self._start_path(line_edit)
        selected, _ = QFileDialog.getOpenFileName(
            self.parent,
            "选择 SSH 私钥",
            start,
            "SSH 私钥 (*.pem *.key);;所有文件 (*.*)",
        )
        return self._apply_selection(line_edit, selected)

    def choose_password_dictionary(self, line_edit):
        """选择每行一个候选密码的本地字典文件。"""

        start = self._start_path(line_edit)
        selected, _ = QFileDialog.getOpenFileName(
            self.parent,
            "选择密码字典",
            start,
            "文本字典 (*.txt *.lst *.dic);;所有文件 (*.*)",
        )
        return self._apply_selection(line_edit, selected)

    def choose_wpc_file(self, line_edit):
        """选择 Reaver `.wpc` 进度文件。"""

        start = self._start_path(line_edit)
        selected, _ = QFileDialog.getOpenFileName(
            self.parent,
            "选择 Pin 进度恢复文件",
            start,
            "Pin 进度恢复文件 (*.wpc)",
        )
        return self._apply_selection(line_edit, selected)

    def _start_path(self, line_edit, directory=False):
        text = line_edit.text().strip()
        if text:
            path = self.settings_manager.resolve_path(text)
            if path.exists():
                return str(path if directory or path.is_dir() else path.parent)
            if path.parent.exists():
                return str(path.parent)
        return str(Path.home())

    @staticmethod
    def _apply_selection(line_edit, selected):
        if not selected:
            return ""
        line_edit.setText(str(selected))
        line_edit.editingFinished.emit()
        return str(selected)
