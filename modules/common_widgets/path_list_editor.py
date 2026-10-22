from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
)

from ..config_manager import make_portable_path
from ..dialog_theme import create_themed_menu, detect_light_theme


class PathListEditor(QFrame):
    # 多行路径列表编辑器。
    # 这个控件用于"一个输入框里维护多条路径"的场景，例如保留配置、多个目标目录、复制规则。
    # 调用方仍然通过 setPlainText()/toPlainText() 读写文本，所以不会改变现有配置保存结构。
    FILES_AND_DIRS = "files_and_dirs"
    FILES_ONLY = "files_only"
    EXE_ONLY = "exe_only"
    DIRS_ONLY = "dirs_only"
    FILES_WITH_TARGET = "files_with_target"

    def __init__(
        self,
        parent=None,
        mode=FILES_AND_DIRS,
        placeholder="",
        tooltip="",
        minimum_height=70,
        default_dir="",
        file_caption="选择文件",
        dir_caption="选择文件夹",
        file_filter="所有文件 (*.*)",
        add_button_text="添加",
        auto_create_dirs=True,
    ):
        super().__init__(parent)
        self.mode = mode
        self.default_dir = str(default_dir or "")
        self.file_caption = file_caption
        self.dir_caption = dir_caption
        # EXE_ONLY 只允许用户通过文件选择器添加可执行程序，适合防火墙规则这类场景。
        self.file_filter = "可执行程序 (*.exe)" if mode == self.EXE_ONLY and file_filter == "所有文件 (*.*)" else file_filter

        self.setObjectName("pathListEditor")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # 外层纵向布局：编辑行 + 选项行
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(4)

        # 编辑行（按钮 + 输入框）
        self.editor_layout = QHBoxLayout()
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_layout.setSpacing(8)

        self.add_button = QPushButton(add_button_text, self)
        # 这里使用设置窗口的工具按钮语义名，避免继续复用旧的“确定按钮”兼容样式。
        self.add_button.setObjectName("settingsToolbarButton")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setMinimumHeight(28)
        self.add_button.setToolTip("从文件选择器添加路径，会自动追加到列表末尾并去重。")
        self.add_button.clicked.connect(self._open_add_menu)

        self.button_column = QVBoxLayout()
        self.button_column.setContentsMargins(0, 0, 0, 0)
        self.button_column.setSpacing(0)
        self.button_column.addStretch()
        self.button_column.addWidget(self.add_button)
        self.button_column.addStretch()
        self.editor_layout.addLayout(self.button_column)

        self.text_edit = QTextEdit(self)
        self.text_edit.setPlaceholderText(placeholder)
        self.text_edit.setMinimumHeight(minimum_height)
        self.text_edit.setToolTip(tooltip)
        self.text_edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.editor_layout.addWidget(self.text_edit, 1)

        self.root_layout.addLayout(self.editor_layout)

        # 选项行：目标目录不存在时自动创建（仅 FILES_WITH_TARGET 模式可用）
        self.auto_create_check = QCheckBox("目标目录不存在时自动创建", self)
        self.auto_create_check.setChecked(auto_create_dirs)
        self.auto_create_check.setToolTip("勾选后，如果目标目录不存在则自动创建。\n"
            "不勾选时，目标目录不存在会跳过本次复制，并在更新信息中给出提示。")
        self.auto_create_check.setVisible(mode == self.FILES_WITH_TARGET)
        self.root_layout.addWidget(self.auto_create_check)

    def setAutoCreateDirs(self, enabled):
        self.auto_create_check.setChecked(enabled)

    def isAutoCreateDirs(self):
        return self.auto_create_check.isChecked()

    def setPlainText(self, text):
        # 兼容 QTextEdit 的常用接口，方便替换旧代码。
        lines = [self._display_path_text(line) for line in str(text or "").splitlines()]
        self.text_edit.setPlainText("\n".join(lines))

    def toPlainText(self):
        # 兼容 QTextEdit 的常用接口，保存配置时直接读取这里即可。
        return self.text_edit.toPlainText()

    def setPlaceholderText(self, text):
        self.text_edit.setPlaceholderText(text)

    def setToolTip(self, text):
        # tooltip 同时挂在外层和输入框上，用户鼠标停在任意位置都能看到说明。
        super().setToolTip(text)
        self.text_edit.setToolTip(text)

    def setMinimumHeight(self, height):
        # 调用方以前可能直接设置 QTextEdit 高度。
        # 这里把高度转给内部输入框，外层高度由布局自然撑开。
        self.text_edit.setMinimumHeight(height)

    def setEnabled(self, enabled):
        # 禁用外层时同步禁用内部控件，避免按钮还能打开文件选择器。
        super().setEnabled(enabled)
        self.text_edit.setEnabled(enabled)
        self.add_button.setEnabled(enabled)
        self.auto_create_check.setEnabled(enabled)

    def append_paths(self, paths):
        # 批量追加路径并去重。
        # 去重按字符串精确比较，避免擅自改大小写或解析相对路径导致用户输入被意外改写。
        existing = self._lines()
        seen = set(existing)
        for path in paths:
            text = self._display_path_text(path)
            if not text or text in seen:
                continue
            existing.append(text)
            seen.add(text)
        self.text_edit.setPlainText("\n".join(existing))

    def _display_path_text(self, path):
        """把文件选择器返回的路径统一成 Windows 界面习惯的反斜杠格式。

        参数：path 是 QFileDialog 返回的文件或目录路径。
        返回值：去掉首尾空白后的显示文本。
        副作用：无；只影响界面中新增路径的显示，不会主动解析或创建文件。

        注意：复制规则使用 "源 => 目标" 格式，里面可能同时包含两个路径，保持原文本不改，
        避免误改用户手写规则。
        """

        text = str(path or "").strip()
        if "=>" in text:
            return text
        return text.replace("/", "\\")

    def _open_add_menu(self):
        # 根据模式决定弹出菜单内容。
        # 只有一种添加方式时直接打开对应选择器，减少用户多点一次。
        if self.mode in (self.FILES_ONLY, self.EXE_ONLY):
            self._add_files()
            return
        if self.mode == self.FILES_WITH_TARGET:
            self._add_files_with_target()
            return
        if self.mode == self.DIRS_ONLY:
            self._add_directory()
            return

        menu = create_themed_menu(self, detect_light_theme(self))
        menu.addAction("添加文件", self._add_files)
        menu.addAction("添加文件夹", self._add_directory)
        menu.exec(self.add_button.mapToGlobal(self.add_button.rect().bottomLeft()))

    def _add_files(self):
        # 文件选择允许一次选多个，适合批量添加配置文件或复制源文件。
        files, _ = QFileDialog.getOpenFileNames(
            self,
            self.file_caption,
            self._start_dir(),
            self.file_filter,
        )
        self.append_paths(files)

    def _add_files_with_target(self):
        """选择源文件后，用文件夹选择器指定目标路径，追加为 "源 => 目标" 格式。

        目标路径保存时自动转为可移植路径（如 {USER_HOME}\Desktop），
        跨用户迁移时仍能指向正确位置。
        """
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择源文件",
            self._start_dir(),
            self.file_filter,
        )
        if not files:
            return
        rules = []
        for file_path in files:
            start_dir = str(Path(file_path).parent)
            target_dir = QFileDialog.getExistingDirectory(
                self, f"选择目标文件夹 - {Path(file_path).name}", start_dir,
            )
            if not target_dir:
                continue
            # 转为可移植路径（{APP_DIR}、{USER_DOWNLOADS}、{USER_HOME}）
            portable_target = make_portable_path(target_dir)
            rules.append(f"{file_path} => {portable_target}")
        if rules:
            self.append_paths(rules)

    def _add_directory(self):
        # Qt 原生文件夹选择通常是单选。
        # 如果用户要添加多个文件夹，可以重复点击“添加”，列表会自动去重。
        directory = QFileDialog.getExistingDirectory(
            self,
            self.dir_caption,
            self._start_dir(),
        )
        if directory:
            self.append_paths([directory])

    def _start_dir(self):
        # 优先从已有路径里找一个存在的位置作为起点。
        # 没有可用路径时再使用调用方传入的默认目录，最后回退到用户主目录。
        for line in reversed(self._lines()):
            path = Path(line)
            if path.exists():
                return str(path.parent if path.is_file() else path)
        if self.default_dir:
            return self.default_dir
        return str(Path.home())

    def _lines(self):
        return [line.strip() for line in self.text_edit.toPlainText().splitlines() if line.strip()]
