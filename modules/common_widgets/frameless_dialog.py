from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QTextEdit,
    QVBoxLayout,
)

from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QStyle, QStyleOption

from modules.widgets import CustomGrip
from modules.ui_scale_manager import UiScaleManager
from .icon_button import WindowCloseButton


class CornerGrip(QFrame):
    # 透明的四角缩放区。
    # Qt 自带 QSizeGrip 主要适合右下角；这里补齐四个角，让无边框弹窗也能像主窗口一样缩放。
    def __init__(self, parent, corner):
        super().__init__(parent)
        self.corner = corner
        self.drag_pos = None
        self.start_geometry = None
        self.setStyleSheet("background: transparent;")
        if corner in ("top_left", "bottom_right"):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.SizeBDiagCursor)

    def mousePressEvent(self, event):
        # 记录鼠标按下时的位置和窗口大小，后续拖动时用它计算新的窗口几何尺寸。
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPos()
            self.start_geometry = self.parent().geometry()
            event.accept()

    def mouseMoveEvent(self, event):
        # 鼠标移动时，根据当前角落方向调整窗口的左、右、上、下边界。
        if not self.drag_pos or not self.start_geometry or not event.buttons() == Qt.LeftButton:
            return

        delta = event.globalPos() - self.drag_pos
        geometry = QRect(self.start_geometry)
        minimum_width = self.parent().minimumWidth()
        minimum_height = self.parent().minimumHeight()

        if "left" in self.corner:
            width = max(minimum_width, geometry.width() - delta.x())
            geometry.setLeft(geometry.right() - width)
        if "right" in self.corner:
            geometry.setWidth(max(minimum_width, geometry.width() + delta.x()))
        if "top" in self.corner:
            height = max(minimum_height, geometry.height() - delta.y())
            geometry.setTop(geometry.bottom() - height)
        if "bottom" in self.corner:
            geometry.setHeight(max(minimum_height, geometry.height() + delta.y()))

        self.parent().setGeometry(geometry)
        event.accept()

    def mouseReleaseEvent(self, event):
        # 鼠标释放后清理拖动状态，避免下一次拖动沿用旧坐标。
        self.drag_pos = None
        self.start_geometry = None
        event.accept()


class FramelessDialog(QDialog):
    """项目内通用无边框弹窗基类。

    主要作用：统一设置窗口、更新确认、文件夹编辑等自定义弹窗的标题栏、边框和缩放行为。
    输入参数：title 是弹窗标题；parent 是父窗口，通常是 SettingsWindow 或主页面。
    返回值：这是 QDialog 子类，调用方通过 exec()/accept()/reject() 使用。
    副作用：会搭建统一 UI 外壳；默认会继承父窗口 QSS，也可由公共弹窗关闭继承后加载
    自己的完整主题；不读写配置或文件。
    调用限制：子类只需要把主体控件添加到 content_layout，把按钮添加到 button_layout。
    """

    def paintEvent(self, event):
        """Own the visible surface in the translucent native window's backing store.

        Children may be invalidated independently during a theme switch. Drawing
        the shared frame style here keeps the surface opaque in that interval,
        while the stylesheet still owns its colors, border and rounded corners.
        """
        super().paintEvent(event)
        frame = getattr(self, "window_frame", None)
        if frame is not None and self.testAttribute(Qt.WA_TranslucentBackground):
            painter = QPainter(self)
            painter.translate(frame.pos())
            option = QStyleOption()
            option.initFrom(frame)
            option.rect = frame.rect()
            frame.style().drawPrimitive(QStyle.PE_Widget, option, painter, frame)
            painter.end()

    def __init__(self, title="", parent=None, translucent_background=True, inherit_parent_stylesheet=True):
        """创建无边框弹窗外壳。

        参数：title 是标题；parent 是 Qt 父窗口；translucent_background 控制透明外层；
        inherit_parent_stylesheet 表示是否复制父窗口 QSS。
        返回值：无。
        副作用：创建标题栏、内容区、按钮区和缩放手柄；当关闭父级样式继承时，调用方
        必须自行加载完整主题，避免透明外壳没有可见背景。
        """
        super().__init__(parent)
        self.drag_pos = None
        self.grip_size = 16
        self._dialog_position_prepared = False
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        if translucent_background:
            self.setAttribute(Qt.WA_TranslucentBackground)
        if inherit_parent_stylesheet:
            self._inherit_parent_stylesheet(parent)
        self._setup_frameless_ui(title)
        self._setup_grips()
        self._connect_frameless_signals()
        scale = UiScaleManager.for_widget(self)
        scale.bind_metrics(self.title_bar, fixed_height=44)
        scale.bind_metrics(self.close_btn, fixed_size=(28, 28))
        for layout, margins, spacing in (
            (self.root_layout, (10, 10, 10, 10), 0),
            (self.title_bar_layout, (14, 0, 8, 0), 0),
            (self.content_layout, (16, 0, 16, 12), 14),
            (self.button_layout, (16, 4, 20, 12), 6),
        ):
            scale.bind_layout(layout, margins=margins, spacing=spacing)

    def set_scaled_size(self, minimum, preferred=None):
        """登记基准尺寸；首次显示按缩放及屏幕可用区域计算，不改用户拖动后的尺寸。"""
        self._dialog_base_size = (minimum, preferred or minimum)
        scale = UiScaleManager.for_widget(self)
        self.setMinimumSize(*(scale.px(v) for v in minimum))
        self.resize(*(scale.px(v) for v in (preferred or minimum)))
        self._dialog_size_prepared = False

    def showEvent(self, event):
        """内容构建完成后一次性布局；系统 DPI 已由 Qt 处理，只叠加应用缩放。"""
        if hasattr(self, "_dialog_base_size") and not self._dialog_size_prepared:
            scale = UiScaleManager.for_widget(self)
            scale.register_layout_tree(self)
            scale.apply_bound_metrics(self)
            self.ensurePolished()
            owner = self._visible_owner()
            screen = owner.screen() if owner else self.screen()
            available = screen.availableGeometry()
            limit = QSize(max(1, available.width() - 24), max(1, available.height() - 24))
            minimum, preferred = self._dialog_base_size
            self.setMinimumSize(QSize(*(scale.px(v) for v in minimum)).boundedTo(limit))
            desired = QSize(*(scale.px(v) for v in preferred)).expandedTo(self.sizeHint())
            self.resize(desired.boundedTo(limit))
            self._dialog_size_prepared = True
        if self.isWindow() and not self._dialog_position_prepared:
            owner = self._visible_owner()
            available = (owner.screen() if owner else self.screen()).availableGeometry()
            center = owner.frameGeometry().center() if owner else available.center()
            self.move(max(available.left(), min(center.x() - self.width() // 2, available.right() - self.width() + 1)),
                      max(available.top(), min(center.y() - self.height() // 2, available.bottom() - self.height() + 1)))
            self._dialog_position_prepared = True
        super().showEvent(event)

    def _visible_owner(self):
        """沿父链定位实际可见窗口，兼容内容已重新挂载的隐藏设置宿主。"""
        parent = self.parentWidget()
        while parent is not None:
            frame = getattr(parent, "window_frame", None)
            if frame is not None and frame.window().isVisible():
                return frame.window()
            owner = parent.window()
            if owner.isVisible():
                return owner
            parent = parent.parentWidget()
        return None

    def _inherit_parent_stylesheet(self, parent):
        """从父窗口继承 QSS，避免统一弹窗退回系统默认样式。

        参数：parent 是 Qt 父控件，可能没有 styleSheet 方法或没有设置样式。
        返回值：无。
        副作用：会设置当前弹窗 styleSheet；子类后续仍可再次 setStyleSheet 覆盖特殊样式。
        调用限制：嵌入式设置页会把 QSS 设置在重新挂载的 window_frame，而 SettingsWindow
        本身保留空 styleSheet。此时必须回退到其保存的完整活动主题，不能让编辑弹窗丢失
        背景、按钮样式和关闭图标。
        """

        if parent is None or not hasattr(parent, "styleSheet"):
            return
        stylesheet = ""
        while parent is not None:
            complete = (getattr(parent, "_winbox_dialog_stylesheet", "") or
                        getattr(parent, "_active_settings_stylesheet", ""))
            if complete:
                stylesheet = complete
                break
            candidate = parent.styleSheet()
            if not stylesheet and "#settingsWindowFrame" in candidate:
                stylesheet = candidate
            parent = parent.parentWidget()
        if stylesheet:
            self.setStyleSheet(stylesheet)

    def _setup_frameless_ui(self, title):
        # 外层 root_layout 给透明边缘和缩放区留空间；真正可见背景放在 settingsWindowFrame。
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(10, 10, 10, 10)
        self.root_layout.setSpacing(0)

        self.window_frame = QFrame(self)
        self.window_frame.setObjectName("settingsWindowFrame")
        # 透明无边框窗口的可见内容由 QSS 主动绘制，防止 Windows 首帧或重绘时露出黑底。
        self.window_frame.setAttribute(Qt.WA_StyledBackground, True)
        self.frame_layout = QVBoxLayout(self.window_frame)
        self.frame_layout.setContentsMargins(0, 0, 0, 0)
        self.frame_layout.setSpacing(0)
        self.root_layout.addWidget(self.window_frame)

        self.title_bar = QFrame(self.window_frame)
        self.title_bar.setObjectName("settingsTitleBar")
        self.title_bar.setMinimumHeight(44)
        self.title_bar.setMaximumHeight(44)
        self.title_bar_layout = QHBoxLayout(self.title_bar)
        self.title_bar_layout.setContentsMargins(14, 0, 8, 0)
        self.title_bar_layout.setSpacing(0)

        self.title_label = QLabel(title, self.title_bar)
        self.title_label.setObjectName("settingsWindowTitle")
        self.title_button_box = QFrame(self.title_bar)
        self.title_button_box.setObjectName("settingsTitleButtons")
        self.title_button_box_layout = QHBoxLayout(self.title_button_box)
        self.title_button_box_layout.setContentsMargins(0, 0, 0, 0)
        self.title_button_box_layout.setSpacing(0)

        self.close_btn = WindowCloseButton(self.title_button_box)
        self.close_btn.setObjectName("settingsCloseBtn")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setText("")
        self.title_button_box_layout.addWidget(self.close_btn)

        self.title_bar_layout.addWidget(self.title_label)
        self.title_bar_layout.addStretch()
        self.title_bar_layout.addWidget(self.title_button_box)
        self.frame_layout.addWidget(self.title_bar)

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(16, 0, 16, 12)
        self.content_layout.setSpacing(14)
        self.frame_layout.addLayout(self.content_layout, 1)

        self.button_layout = QHBoxLayout()
        self.button_layout.setContentsMargins(16, 4, 20, 12)
        self.button_layout.addStretch()
        self.frame_layout.addLayout(self.button_layout)

        self.size_grip = QSizeGrip(self.window_frame)
        # QSizeGrip 贴在右下角，只负责拖拽；背景和边框必须透明，避免遮住窗口圆角边框。
        self.size_grip.setStyleSheet("width: 20px; height: 20px; margin: 0px; padding: 0px; background: transparent; border: none;")

    def _setup_grips(self):
        # 四边缩放复用项目已有的 CustomGrip，四角缩放由 CornerGrip 补齐。
        self.left_grip = CustomGrip(self, Qt.LeftEdge, True)
        self.right_grip = CustomGrip(self, Qt.RightEdge, True)
        self.top_grip = CustomGrip(self, Qt.TopEdge, True)
        self.bottom_grip = CustomGrip(self, Qt.BottomEdge, True)
        self.left_grip.setFixedWidth(self.grip_size)
        self.right_grip.setFixedWidth(self.grip_size)
        self.top_grip.setFixedHeight(self.grip_size)
        self.bottom_grip.setFixedHeight(self.grip_size)

        self.top_left_grip = CornerGrip(self, "top_left")
        self.top_right_grip = CornerGrip(self, "top_right")
        self.bottom_left_grip = CornerGrip(self, "bottom_left")
        self.bottom_right_grip = CornerGrip(self, "bottom_right")
        for grip in self._all_grips():
            grip.setStyleSheet("background: transparent;")
            grip.raise_()

    def _connect_frameless_signals(self):
        # 标题栏负责拖动窗口，关闭按钮默认走 reject，子类仍然可以额外连接自己的关闭逻辑。
        self.close_btn.clicked.connect(self.reject)
        self.title_bar.mousePressEvent = self._title_mouse_press
        self.title_bar.mouseMoveEvent = self._title_mouse_move

    def _all_grips(self):
        # 集中返回所有缩放控件，resizeEvent 里可以统一移动和置顶。
        return (
            self.left_grip,
            self.right_grip,
            self.top_grip,
            self.bottom_grip,
            self.top_left_grip,
            self.top_right_grip,
            self.bottom_left_grip,
            self.bottom_right_grip,
        )

    def _title_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = None
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
            self.drag_pos = event.globalPos()

    def _title_mouse_move(self, event):
        if self.drag_pos and event.buttons() == Qt.LeftButton:
            self.move(self.pos() + event.globalPos() - self.drag_pos)
            self.drag_pos = event.globalPos()
            event.accept()

    def keyPressEvent(self, event):
        """让编辑弹窗的 Enter 触发其“确定”按钮，而不是走 QDialog 默认关闭路径。

        参数：event 是当前键盘事件。
        返回值：无。
        副作用：按 Enter 时可能点击标记为 dialogAcceptButton 的按钮，因此会执行调用方的
        字段校验、配置保存和信号发送；多行文本编辑框中仍保留 Enter 换行行为。
        调用限制：提交按钮必须由 create_dialog_ok_cancel_buttons() 创建，或手动设置同名属性。
        """
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            focused = QApplication.focusWidget()
            if isinstance(focused, (QTextEdit, QPlainTextEdit)):
                super().keyPressEvent(event)
                return
            for button in self.findChildren(QPushButton):
                if button.property("dialogAcceptButton") and button.isEnabled():
                    button.click()
                    event.accept()
                    return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        # 窗口尺寸变化时，把透明缩放区重新贴到窗口边缘，并把右下角可视缩放入口固定到角落。
        if not hasattr(self, "left_grip"):
            super().resizeEvent(event)
            return
        grip_size = self.grip_size
        self.left_grip.setGeometry(0, grip_size, grip_size, self.height() - grip_size * 2)
        self.right_grip.setGeometry(self.width() - grip_size, grip_size, grip_size, self.height() - grip_size * 2)
        self.top_grip.setGeometry(0, 0, self.width(), grip_size)
        self.bottom_grip.setGeometry(0, self.height() - grip_size, self.width(), grip_size)
        self.top_left_grip.setGeometry(0, 0, grip_size, grip_size)
        self.top_right_grip.setGeometry(self.width() - grip_size, 0, grip_size, grip_size)
        self.bottom_left_grip.setGeometry(0, self.height() - grip_size, grip_size, grip_size)
        self.bottom_right_grip.setGeometry(self.width() - grip_size, self.height() - grip_size, grip_size, grip_size)
        for grip in self._all_grips():
            grip.raise_()
        self.size_grip.setGeometry(self.window_frame.width() - 20, self.window_frame.height() - 20, 20, 20)
        self.size_grip.raise_()
        super().resizeEvent(event)
