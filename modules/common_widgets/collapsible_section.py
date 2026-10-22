"""公共简约折叠分组：细线箭头和文字，不持有业务或配置。"""
from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, Qt
from PySide6.QtGui import QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout
from modules.ui_scale_manager import UiScaleManager


class _ChevronIndicator(QLabel):
    """按当前调色板绘制矢量箭头，无位图缓存或主题轮询。"""
    def __init__(self, parent):
        super().__init__(parent)
        self._expanded = False
        UiScaleManager.for_widget(parent).bind_metrics(self, fixed_size=(12, 16))

    def set_expanded(self, expanded):
        self._expanded = bool(expanded)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        scale = min(self.width() / 12, self.height() / 16)
        painter.scale(scale, scale)
        painter.setPen(QPen(self.palette().color(QPalette.WindowText), 1.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        points = ((-4, -2), (0, 2), (4, -2)) if self._expanded else ((-2, -4), (2, 0), (-2, 4))
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))
        painter.end()


class _SectionHeaderButton(QPushButton):
    """只由子控件绘制箭头与文字，不绘制原生悬停边框和焦点框。"""
    def paintEvent(self, event):
        return


class _SectionContent(QFrame):
    """Keep wrapped rows at their natural height within a narrow scroll viewport."""
    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        layout = self.layout()
        if layout is not None and layout.hasHeightForWidth():
            height = layout.totalHeightForWidth(max(self.width(), hint.width()))
            hint.setHeight(max(hint.height(), height))
        return hint


class CollapsibleSection(QFrame):
    """设置页和弹窗通用的折叠分组面板。

    主要作用：把一组高级选项收进可展开区域，避免页面一次显示太多低频配置。
    输入参数：title 是分组名称；description 是少数需要补充说明的标题描述；expanded
    控制初始展开状态；parent 是 Qt 父控件。
    返回值：这是 QWidget 子类，调用方直接加入布局使用。
    副作用：点击标题会显示/隐藏内容区并触发布局重算；不会读写配置，也不会保存折叠状态。
    调用限制：调用方通过 addWidget()/addLayout() 往内容区添加控件，不要直接操作内部布局。
    收起时保留内容的最小宽度；换行高度和动画终点按实际宽度计算，避免展开时其他分组重排。
    """
    def __init__(self, title, description="", expanded=True, parent=None, animate_initial=False):
        super().__init__(parent)
        self.title = title
        self.description = description
        self.setObjectName("settingsCollapsibleSection")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        self.header_button = _SectionHeaderButton(self)
        self.header_button.setObjectName("settingsCollapseHeader")
        self.header_button.setCheckable(True)
        self.header_button.setAutoDefault(False)
        self.header_button.setAccessibleName(str(title))
        self.header_button.setCursor(Qt.PointingHandCursor)
        self.header_layout = QHBoxLayout(self.header_button)
        UiScaleManager.for_widget(self).bind_layout(self.header_layout, margins=(10, 8, 10, 8), spacing=12)
        self.arrow_label = _ChevronIndicator(self.header_button)
        self.arrow_label.setObjectName("settingsCollapseArrow")
        self.title_label = QLabel(self.title, self.header_button)
        self.title_label.setObjectName("settingsCollapseTitle")
        self.description_label = QLabel(self.description, self.header_button)
        self.description_label.setObjectName("settingsCollapseDescription")
        self.description_label.setWordWrap(True)
        self.header_layout.addWidget(self.arrow_label, 0, Qt.AlignVCenter)
        self.header_layout.addWidget(self.title_label, 0, Qt.AlignVCenter)
        if self.description:
            self.header_layout.addWidget(self.description_label, 1, Qt.AlignVCenter)
        else:
            self.description_label.hide()
            self.header_layout.addStretch(1)
        # PySide6 在部分环境下会为 clicked 连接无参重载，不能依赖它传递 checked。
        # 直接读取按钮自身的选中状态，确保普通模式的折叠区始终可开关。
        self.header_button.clicked.connect(
            lambda: self.set_expanded(self.header_button.isChecked())
        )
        self.root_layout.addWidget(self.header_button)
        self._expanded = False

        self.content_frame = _SectionContent(self)
        self.content_frame.setObjectName("settingsCollapseContent")
        self.content_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.content_layout = QVBoxLayout(self.content_frame)
        UiScaleManager.for_widget(self).bind_layout(self.content_layout, margins=(34, 8, 10, 14), spacing=12)
        self.root_layout.addWidget(self.content_frame)

        # 首次显示时可按场景跳过展开动画；例如主机模式只有一个更新方案分组，
        # 直接呈现完整内容比从零高度展开更自然。
        for label in (self.arrow_label, self.title_label, self.description_label):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._section_anim = QPropertyAnimation(self.content_frame, b"maximumHeight", self)
        self._section_anim.setDuration(180)
        self._section_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._section_anim.valueChanged.connect(self._update_animation_geometry)
        self._section_anim.finished.connect(self._finish_animation)
        self.set_expanded(expanded, animate=animate_initial)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        if hasattr(self, "content_frame"):
            margins = self.root_layout.contentsMargins()
            hint.setWidth(max(hint.width(), self.content_frame.minimumSizeHint().width()
                              + margins.left() + margins.right()))
        return hint

    def set_expanded(self, expanded, animate=True):
        """切换内容显隐；复用一个有所属的动画，连续点击从当前高度续接。"""
        self._section_anim.stop()
        expanded = bool(expanded)
        target = self.content_frame
        start = 0 if target.isHidden() else target.height()
        self._expanded = expanded
        self.header_button.setChecked(expanded)
        self.arrow_label.set_expanded(expanded)
        if not animate:
            self._finish_animation()
            return
        if expanded:
            target.show()
        target.setMaximumHeight(start)
        self._section_anim.setStartValue(start)
        width = max(0, self.contentsRect().width() - self.root_layout.contentsMargins().left()
                    - self.root_layout.contentsMargins().right())
        wrapped_height = self.content_layout.totalHeightForWidth(width)
        end = wrapped_height if wrapped_height >= 0 else target.sizeHint().height()
        self._section_anim.setEndValue(end if expanded else 0)
        self._section_anim.start()
        self.updateGeometry()

    def _update_animation_geometry(self, _value):
        self.updateGeometry()

    def _finish_animation(self):
        self.content_frame.setVisible(self._expanded)
        self.content_frame.setMaximumHeight(16777215 if self._expanded else 0)
        self.updateGeometry()

    def addLayout(self, layout):
        # 兼容 QVBoxLayout 的常用调用方式，调用方可以直接 section.addLayout(row)。
        self.content_layout.addLayout(layout)

    def addWidget(self, widget):
        # 兼容 QVBoxLayout 的常用调用方式，调用方可以直接 section.addWidget(widget)。
        self.content_layout.addWidget(widget)


