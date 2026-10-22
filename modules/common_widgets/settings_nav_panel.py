"""设置窗口通用侧边栏导航面板。

提供分组标题 + 图标按钮的导航栏，可复用于任何需要左侧分类导航的窗口。
参考图风格：小字灰色 section header + 圆角矩形高亮选中按钮。

典型用法::

    nav = SettingsNavPanel(parent)
    nav.add_section("软件设置")
    btn_global = nav.add_button("全局设置", ":/icons/images/icons/cil-settings.png")
    btn_proxy = nav.add_button("代理设置", ":/icons/images/icons/cil-link-alt.png")
    nav.add_spacing()
    nav.add_section("用户设置")
    btn_list = nav.add_button("软件配置列表", ":/icons/images/icons/cil-description.png")

    # 外部连接信号
    btn_global.clicked.connect(lambda: ...)

    # 选中管理
    nav.set_checked(btn_global)
    nav.uncheck_all()
"""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from modules.common_widgets.navigation_button import NavigationButton
from modules.icon_utils import get_theme_icon
from modules.ui_scale_manager import UiScaleManager


class SettingsNavPanel(QFrame):
    """带分组标题和图标按钮的侧边栏导航面板。

    属性：
        objectName 默认为 "settingsCategoryPanel"，QSS 可直接通过
        #settingsCategoryPanel 控制面板背景和边框。

    公开属性：
        buttons -- 按添加顺序存储的所有导航按钮（不含 section header）。
    """

    def __init__(self, parent: QWidget | None = None, object_name: str = "settingsCategoryPanel"):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.buttons: list[QPushButton] = []

        self._layout = QVBoxLayout(self)
        # 让选中背景与侧栏边缘保留距离，避免看起来像独立色块或边框。
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(2)

    def add_section(self, title: str) -> QLabel:
        """添加分组标题（如"软件设置"、"用户设置"）。

        参数：title 是标题文字。
        返回值：创建的 QLabel，可进一步调整样式。
        副作用：插入到面板布局中。
        """
        label = QLabel(title, self)
        label.setObjectName("settingsCategorySectionTitle")
        self._layout.addWidget(label)
        return label

    def add_title(self, title: str) -> QLabel:
        """添加导航栏一级标题（如"设置"）。

        参数：title 是一级标题文字。
        返回值：创建的 QLabel。
        副作用：插入到所有分组标题之前，用于明确侧栏是一级导航。
        """
        label = QLabel(title, self)
        label.setObjectName("settingsCategoryTitle")
        self._layout.addWidget(label)
        return label

    def add_button(self, text: str, icon_path: str = "") -> QPushButton:
        """添加带图标的分类按钮。

        参数：text 是按钮文字；icon_path 是图标资源路径（可选）。
        返回值：创建的 QPushButton，供外部连接 clicked 信号。
        副作用：按钮加入 self.buttons 列表，插入到面板布局中。
        """
        button = NavigationButton(text, self)
        button.setObjectName("settingsCategoryButton")
        button.setCheckable(True)
        if icon_path:
            # 保存资源路径，主题切换时统一重绘为深色或浅色图标。
            button.setProperty("settingsNavIconPath", icon_path)
            button.setIcon(get_theme_icon(icon_path, True, light_color=QColor("#343434")))
            UiScaleManager.for_widget(self).bind_metrics(button, icon_size=16)
        self.buttons.append(button)
        self._layout.addWidget(button)
        return button

    def add_spacing(self, size: int = 10):
        """在分组之间添加间距。"""
        self._layout.addSpacing(size)

    def add_stretch(self):
        """在底部添加弹性空间，将导航项推到顶部。"""
        self._layout.addStretch()

    def set_light_theme(self, light_theme: bool):
        """按主题刷新所有导航图标颜色。

        参数：light_theme 为 True 时使用深灰图标，False 时使用接近白色的图标。
        返回值：无。
        副作用：替换已创建按钮的 QIcon，不会改变按钮文字或选中状态。
        """
        for button in self.buttons:
            icon_path = button.property("settingsNavIconPath")
            if not icon_path:
                continue
            button.setIcon(
                get_theme_icon(
                    str(icon_path),
                    light_theme,
                    light_color=QColor("#343434"),
                    dark_color=QColor("#f1f1f1"),
                )
            )

    def set_checked(self, target: QPushButton):
        """选中指定按钮，取消其他所有按钮的选中态。"""
        for btn in self.buttons:
            btn.setChecked(btn is target)

    def uncheck_all(self):
        """清除所有按钮的选中态。"""
        for btn in self.buttons:
            btn.setChecked(False)
