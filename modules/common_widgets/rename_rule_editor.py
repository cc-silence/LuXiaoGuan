"""更新后重命名规则编辑器（文本编辑模式）。

主要作用：类似 PathListEditor 的多行文本编辑器，
每行格式为 "源文件路径 => 新文件名"。
浏览按钮行为：选文件 → 弹输入框填新文件名 → 追加一行。

使用方式：
  editor = RenameRuleEditor(parent)
  editor.set_rules([{"source": "...", "name": "..."}])
  rules = editor.get_rules()

输入参数：set_rules 接受 list[dict]，每项含 source 和 name。
返回值：get_rules 返回同样结构的列表。
副作用：只修改界面显示，不读写文件、不修改配置。
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
)

from ..config_manager import (
    APP_DIR_TOKEN,
    USER_DOWNLOADS_TOKEN,
    USER_HOME_TOKEN,
)
from ..dialog_theme import create_themed_menu, detect_light_theme
class RenameRuleEditor(QFrame):
    """更新后重命名规则编辑器。

    QTextEdit 多行文本编辑，每行格式 "源路径 => 新名称"。
    浏览按钮弹出文件+名称对话框后自动追加一行。
    支持右键插入可移植路径令牌（{APP_DIR} 等）。
    """

    def __init__(self, parent=None, text_input_dialog_cls=None):
        super().__init__(parent)
        # 公共控件不能反向依赖 settings_pages；需要主题化输入弹窗时由调用方注入。
        self.text_input_dialog_cls = text_input_dialog_cls
        self.setObjectName("renameRuleEditor")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(4)

        # 编辑行（按钮 + 输入框）
        self.editor_layout = QHBoxLayout()
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_layout.setSpacing(8)

        self.add_button = QPushButton("添加", self)
        self.add_button.setObjectName("settingsToolbarButton")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setMinimumHeight(28)
        self.add_button.setToolTip("选择文件 → 输入新名称 → 追加一条重命名规则")
        self.add_button.clicked.connect(self._add_rule)

        self.button_column = QVBoxLayout()
        self.button_column.setContentsMargins(0, 0, 0, 0)
        self.button_column.setSpacing(0)
        self.button_column.addStretch()
        self.button_column.addWidget(self.add_button)
        self.button_column.addStretch()
        self.editor_layout.addLayout(self.button_column)

        self.text_edit = QTextEdit(self)
        self.text_edit.setPlaceholderText("一行一条，格式：源文件路径 => 新文件名\n例如：C:\\software\\app\\old.dll => new.dll")
        self.text_edit.setMinimumHeight(100)
        self.text_edit.setToolTip(
            "一行一条重命名规则。\n"
            "格式：源文件完整路径 => 新文件名\n"
            "点击 [添加] 按钮选择文件后输入新名称，会自动追加一行。\n"
            "也支持手动编辑。源文件路径支持 {APP_DIR} 等可移植路径令牌。"
        )
        self.text_edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.text_edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.text_edit.customContextMenuRequested.connect(self._show_token_menu)
        self.editor_layout.addWidget(self.text_edit, 1)

        self.root_layout.addLayout(self.editor_layout)

    def _add_rule(self):
        """浏览文件 → 输入新名称 → 追加一行。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择需要改名的文件",
            str(Path.home()),
            "所有文件 (*.*)",
        )
        if not file_path:
            return
        file_path = file_path.replace("/", "\\")
        # 弹出无边框输入框让用户填新文件名
        default_name = Path(file_path).name
        if self.text_input_dialog_cls is None:
            raise RuntimeError("RenameRuleEditor 需要调用方传入 text_input_dialog_cls。")
        dialog = self.text_input_dialog_cls(
            "新文件名",
            f"将 {default_name} 改为（仅文件名，不含路径）：",
            self,
            default=default_name,
            placeholder="请输入新文件名（仅文件名，不含路径）",
        )
        if dialog.exec() != self.text_input_dialog_cls.Accepted:
            return
        new_name = dialog.result_text
        if not new_name:
            return
        existing = self.text_edit.toPlainText().strip()
        line = f"{file_path} => {new_name}"
        if existing:
            self.text_edit.setPlainText(existing + "\n" + line)
        else:
            self.text_edit.setPlainText(line)

    def _show_token_menu(self, pos):
        """右键菜单：在光标位置插入可移植路径令牌。"""
        menu = create_themed_menu(self, detect_light_theme(self))
        menu.addAction(APP_DIR_TOKEN, lambda: self._insert_token(APP_DIR_TOKEN))
        menu.addAction(USER_DOWNLOADS_TOKEN, lambda: self._insert_token(USER_DOWNLOADS_TOKEN))
        menu.addAction(USER_HOME_TOKEN, lambda: self._insert_token(USER_HOME_TOKEN))
        menu.exec(self.text_edit.mapToGlobal(pos))

    def _insert_token(self, token):
        """在 QTextEdit 光标位置插入令牌。"""
        cursor = self.text_edit.textCursor()
        cursor.insertText(token)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.setFocus()

    def set_rules(self, rules):
        """加载重命名规则列表。

        参数：rules 是 [{"source": "...", "name": "..."}] 格式。
        副作用：清空并重建文本内容。
        """
        lines = []
        for rule in rules:
            if isinstance(rule, dict):
                source = str(rule.get("source", "") or "").strip()
                name = str(rule.get("name", "") or "").strip()
                if source and name:
                    lines.append(f"{source} => {name}")
        self.text_edit.setPlainText("\n".join(lines))

    def get_rules(self):
        """解析文本内容，返回重命名规则列表。

        返回值：[{"source": "...", "name": "..."}, ...]
        空行和格式不正确的行会被过滤。
        """
        rules = []
        for line in self.text_edit.toPlainText().splitlines():
            line = line.strip()
            if not line or "=>" not in line:
                continue
            source_raw, name_raw = line.split("=>", 1)
            source = source_raw.strip()
            name = name_raw.strip()
            if source and name:
                rules.append({"source": source, "name": name})
        return rules

    def clear(self):
        """清空文本。"""
        self.text_edit.clear()

    def setPlainText(self, text):
        """兼容 QTextEdit 接口。"""
        self.text_edit.setPlainText(text)

    def toPlainText(self):
        """兼容 QTextEdit 接口。"""
        return self.text_edit.toPlainText()

    def setPlaceholderText(self, text):
        self.text_edit.setPlaceholderText(text)

    def setToolTip(self, text):
        super().setToolTip(text)
        self.text_edit.setToolTip(text)

    def setMinimumHeight(self, height):
        self.text_edit.setMinimumHeight(height)

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self.text_edit.setEnabled(enabled)
        self.add_button.setEnabled(enabled)
