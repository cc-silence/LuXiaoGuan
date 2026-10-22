"""带眼睛开关的表头；只管画眼睛和点眼睛，不碰排序逻辑。"""

from PySide6.QtCore import Qt, QRect, Signal, QEvent
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QHeaderView, QToolTip, QStyle, QStyleOptionHeader
from modules.icon_utils import get_visibility_icon
from modules.ui_scale_manager import UiScaleManager

EYE_SIZE = 18
EYE_MARGIN = 11

# 眼睛描线色必须由表格按主题下发；嗅探残留画笔在深色底下会画出黑色对黑色。
EYE_COLOR_DARK = "#c3ccd8"
EYE_COLOR_LIGHT = "#3d4b5e"


def eye_rect(section_rect, size=EYE_SIZE, margin=EYE_MARGIN):
    """返回表头格内右侧居中的眼睛矩形；点击只认这一块。"""
    left = section_rect.right() - margin - size + 1
    top = section_rect.top() + max(0, (section_rect.height() - size) // 2)
    return QRect(left, top, size, size)


class PrivacyHeaderView(QHeaderView):
    """在指定列画眼睛开关；点中眼睛只发信号，不触发排序。"""

    eyeClicked = Signal(int)
    widthsEdited = Signal()

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._eye_sections = set()
        self._masked = {}
        self._pressed_section = -1
        self._widths_before_drag = None
        self._eye_color = QColor(EYE_COLOR_DARK)

    def set_eye_color(self, color):
        """设置眼睛描线色；表格在主题切换时下发。"""
        self._eye_color = QColor(color)
        self.viewport().update()

    def _eye_rect(self, rect):
        scale = UiScaleManager.for_widget(self)
        return eye_rect(rect, scale.px(EYE_SIZE), scale.px(EYE_MARGIN))

    def sectionSizeFromContents(self, section):
        size = super().sectionSizeFromContents(section)
        if section in getattr(self, "_eye_sections", ()):
            size.setWidth(size.width() + UiScaleManager.for_widget(self).px(EYE_SIZE + EYE_MARGIN))
        return size

    def set_eye_sections(self, sections):
        """设置哪些逻辑列显示眼睛；其他列与原生表头一致。"""
        self._eye_sections = set(sections)
        self.viewport().update()

    def set_masked(self, section, masked):
        """更新睁眼/闭眼状态并重画该节。"""
        self._masked[section] = bool(masked)
        self.viewport().update()

    def is_masked(self, section):
        """返回该列当前是否隐藏。"""
        return bool(self._masked.get(section))

    def paintSection(self, painter, rect, logicalIndex):
        """沿用主题表头绘制，标题省略范围避开眼睛槽。"""
        if logicalIndex not in self._eye_sections:
            painter.save()
            super().paintSection(painter, rect, logicalIndex)
            painter.restore()
            return
        painter.save()
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        self.initStyleOptionForIndex(option, logicalIndex)
        option.rect = rect
        font = self.model().headerData(logicalIndex, self.orientation(), Qt.FontRole)
        painter.setFont(font if isinstance(font, QFont) else self.font())
        label = self.style().subElementRect(QStyle.SE_HeaderLabel, option, self)
        eye = self._eye_rect(rect)
        # 只在右侧为开关留空，避免左右重复预留导致短标题也被省略。
        label.setRight(min(label.right(), eye.left() - UiScaleManager.for_widget(self).px(4)))
        text = painter.fontMetrics().elidedText(option.text, Qt.ElideRight, max(0, label.width()))
        option.text = ""
        painter.save()
        self.style().drawControl(QStyle.CE_Header, option, painter, self)
        painter.restore()
        option.text = text
        option.rect = label
        self.style().drawControl(QStyle.CE_HeaderLabel, option, painter, self)
        color = QColor(self._eye_color)
        if not color.isValid():
            color = option.palette.color(QPalette.ButtonText)
        icon_rect = QRect(0, 0, round(eye.width() * 0.8), round(eye.height() * 0.8))
        icon_rect.moveCenter(eye.center())
        get_visibility_icon(self.is_masked(logicalIndex), color).paint(painter, icon_rect)
        painter.restore()

    def viewportEvent(self, event):
        """眼睛区域明确提示当前可执行的显示/隐藏动作。"""
        if event.type() == QEvent.ToolTip:
            section = self.logicalIndexAt(event.pos())
            if section in self._eye_sections and self._eye_rect(self._section_rect(section)).contains(event.pos()):
                title = self.model().headerData(section, self.orientation(), Qt.DisplayRole)
                action = "显示" if self.is_masked(section) else "隐藏"
                QToolTip.showText(event.globalPos(), f"{action}{title}内容", self)
                return True
        return super().viewportEvent(event)

    def mousePressEvent(self, event):
        """点中眼睛只记录，不交给基类（避免误触排序）。"""
        point = event.position().toPoint()
        if event.button() == Qt.LeftButton:
            self._widths_before_drag = tuple(self.sectionSize(i) for i in range(self.count()))
        section = self.logicalIndexAt(point)
        if event.button() == Qt.LeftButton and section in self._eye_sections and self._eye_rect(self._section_rect(section)).contains(point):
            self._pressed_section = section
            event.accept()
            return
        self._pressed_section = -1
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """在同一只眼睛内松开才发信号，否则走默认行为。"""
        section = self._pressed_section
        self._pressed_section = -1
        if event.button() == Qt.LeftButton and section in self._eye_sections and self._eye_rect(self._section_rect(section)).contains(event.position().toPoint()):
            self.eyeClicked.emit(section)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self._widths_before_drag is not None:
            previous = self._widths_before_drag
            self._widths_before_drag = None
            if previous != tuple(self.sectionSize(i) for i in range(self.count())):
                self.widthsEdited.emit()

    def _section_rect(self, section):
        """按视口坐标重建该节矩形；隐藏列返回空矩形。"""
        if section < 0 or self.isSectionHidden(section):
            return QRect()
        left = self.sectionViewportPosition(section)
        return QRect(left, 0, self.sectionSize(section), self.height())
