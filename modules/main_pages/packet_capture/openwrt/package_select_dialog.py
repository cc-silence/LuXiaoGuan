"""卸载包多选弹窗；只收集用户勾选，不执行安装或卸载。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QVBoxLayout

from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme


class PackageSelectDialog(FramelessDialog):
    """勾选要卸载的软件包；默认全选，至少保留一个才能确认。

    参数：title 是标题；message 是说明正文；packages 是包名或 (包名, 用途)
    列表，用途显示在括号里，返回仍是干净包名；parent 是父窗口。
    返回值：selected_packages() 返回确认时的勾选包名；
    空选择时确认按钮禁用。副作用：只显示模态弹窗。
    """

    def __init__(self, title, message, packages, parent=None,
                 yes_text="确认卸载", no_text="取消"):
        super().__init__(title, parent, inherit_parent_stylesheet=False)
        apply_common_dialog_theme(self, parent)
        self.setMinimumSize(460, 280)
        self._packages = []
        for item in packages or []:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                self._packages.append((str(item[0]), str(item[1] or "")))
            else:
                self._packages.append((str(item), ""))
        hint = QLabel(str(message or ""), self)
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint, 1)
        body = QVBoxLayout()
        body.setContentsMargins(0, 4, 0, 4)
        body.setSpacing(2)
        self.select_all = QCheckBox("全选", self)
        self.select_all.setChecked(True)
        self.select_all.toggled.connect(self._toggle_all)
        body.addWidget(self.select_all)
        self.checks = []
        for name, purpose in self._packages:
            box = QCheckBox(f"{name}（{purpose}）" if purpose else name, self)
            box.setProperty("packageName", name)
            box.setChecked(True)
            box.toggled.connect(self._refresh_state)
            body.addWidget(box)
            self.checks.append(box)
        self.content_layout.addLayout(body, 1)
        self.yes_button = QPushButton(str(yes_text), self)
        self.yes_button.setObjectName("settingsPrimaryButton")
        self.yes_button.setMinimumSize(88, 32)
        self.yes_button.setCursor(Qt.PointingHandCursor)
        self.yes_button.setAutoDefault(False)
        self.no_button = QPushButton(str(no_text), self)
        self.no_button.setObjectName("settingsSecondaryButton")
        self.no_button.setMinimumSize(88, 32)
        self.no_button.setCursor(Qt.PointingHandCursor)
        self.no_button.setAutoDefault(False)
        self.yes_button.clicked.connect(self.accept)
        self.no_button.clicked.connect(self.reject)
        self.button_layout.addWidget(self.yes_button)
        self.button_layout.addSpacing(10)
        self.button_layout.addWidget(self.no_button)
        self._refresh_state()

    def _toggle_all(self, checked):
        """全选框联动所有包；逐个设置会触发状态刷新。"""
        for box in self.checks:
            box.setChecked(bool(checked))

    def _refresh_state(self):
        """空选择时禁用确认，避免发空卸载请求。"""
        self.yes_button.setEnabled(bool(self.selected_packages()))
        if self.select_all is not None:
            blocked = bool(self.select_all.signalsBlocked())
            self.select_all.blockSignals(True)
            try:
                self.select_all.setChecked(all(box.isChecked() for box in self.checks))
            finally:
                self.select_all.blockSignals(blocked)

    def selected_packages(self):
        """返回当前勾选的干净包名，保持传入顺序。"""
        return [str(box.property("packageName") or box.text()) for box in self.checks
                if box.isChecked()]
