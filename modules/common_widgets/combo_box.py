"""公共下拉框：统一弹出列表绘制，不改变单选或调用方的多选语义。"""

from contextlib import contextmanager
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QComboBox, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from modules.dialog_theme import combo_popup_stylesheet, detect_light_theme
from modules.ui_scale_manager import UiScaleManager


@contextmanager
def popup_theme_transaction():
    """主窗口更换 QSS 期间不判断中间主题，由调用方统一刷新最终弹出样式。"""
    app=QApplication.instance()
    previous=getattr(app,"_winbox_popup_theme_depth",0)
    app._winbox_popup_theme_depth=previous+1
    try:yield
    finally:app._winbox_popup_theme_depth=previous


class ComboPopupDelegate(QStyledItemDelegate):
    """让 Qt 6.3 的弹出视图也按公共 QSS 绘制复选框；普通项目仍走原生绘制。"""

    def sizeHint(self, option, index):
        """补齐原生组合框代理的焦点留白，单选和多选使用相同的行高。"""
        size = super().sizeHint(option, index)
        view = self.parent()
        size.setHeight(size.height() + 2 * view.style().pixelMetric(
            QStyle.PM_FocusFrameVMargin, option, view))
        return size

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        if not opt.features & QStyleOptionViewItem.HasCheckIndicator:
            super().paint(painter, option, index)
            return
        view = opt.widget or self.parent()
        opt.widget = view
        style = view.style()
        rect = style.subElementRect(QStyle.SE_ItemViewItemCheckIndicator, opt, view)
        # 不能先调用 super().paint() 再遮盖：QSS 内边距会移动原生 indicator，
        # 造成裁剪区外残留方框。去掉原生勾选特征，公共 indicator 只绘制一次。
        background = QStyleOptionViewItem(opt)
        background.features &= ~(QStyleOptionViewItem.HasCheckIndicator |
                                 QStyleOptionViewItem.HasDisplay | QStyleOptionViewItem.HasDecoration)
        background.text = ""
        background.icon = QIcon()
        painter.save()
        try:
            painter.setClipRect(opt.rect, Qt.IntersectClip)
            style.drawControl(QStyle.CE_ItemViewItem, background, painter, view)
            content = QStyleOptionViewItem(opt)
            content.features &= ~QStyleOptionViewItem.HasCheckIndicator
            spacing = style.pixelMetric(QStyle.PM_CheckBoxLabelSpacing, opt, view)
            if opt.direction == Qt.RightToLeft:
                content.rect.setRight(rect.left() - spacing)
            else:
                content.rect.setLeft(rect.right() + spacing)
            style.drawControl(QStyle.CE_ItemViewItem, content, painter, view)
            opt.rect = rect
            opt.state &= ~(QStyle.State_On | QStyle.State_Off | QStyle.State_NoChange)
            opt.state |= (QStyle.State_On if opt.checkState == Qt.Checked else
                          QStyle.State_NoChange if opt.checkState == Qt.PartiallyChecked else QStyle.State_Off)
            painter.setClipRect(rect, Qt.IntersectClip)
            style.drawPrimitive(QStyle.PE_IndicatorItemViewItemCheck, opt, painter, view)
        finally:
            painter.restore()


class CommonComboBox(QComboBox):
    """可直接替换 QComboBox；弹出视图共用主题，选择信号和业务数据保持原生。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_view = None
        self._popup_light = detect_light_theme(self, getattr(self, "_popup_light", False))

    def _configure_popup(self, view=None):
        """首次使用或替换视图时接回公共代理和当前主题。"""
        view = super().view() if view is None else view
        if view is not getattr(self, "_popup_view", None):
            self._popup_view = view
            view.setProperty("winboxComboPopup", True)
            view.setItemDelegate(ComboPopupDelegate(view))
        self.set_light_theme(detect_light_theme(self, getattr(self, "_popup_light", False)))
        return view

    def view(self):
        """显式获取列表时才创建；自定义多选视图保持原有接口。"""
        view = super().view()
        if view is not getattr(self, "_popup_view", None):
            self._configure_popup(view)
        return view

    def setView(self, view):
        self._popup_view = None
        super().setView(view)
        self._configure_popup(view)

    def showPopup(self):
        """首次展开前使用当前主题和缩放，不提前常驻隐藏列表。"""
        self._configure_popup()
        super().showPopup()

    def set_light_theme(self, light):
        """记住主题；仅已创建的列表需要生成和应用样式。"""
        self._popup_light = bool(light)
        view = getattr(self, "_popup_view", None)
        if view is None:
            return
        scale = UiScaleManager.for_widget(self)
        key = (bool(light), scale.global_scale, scale._resolved_font())
        if getattr(self, "_popup_scale_key", None) == key:
            stylesheet = self._popup_stylesheet
        else:
            stylesheet = scale.scale_stylesheet(combo_popup_stylesheet(light))
            self._popup_scale_key = key
            self._popup_stylesheet = stylesheet
        if view.styleSheet() != stylesheet:
            view.setStyleSheet(stylesheet)

    def changeEvent(self, event):
        super().changeEvent(event)
        if (getattr(self, "_popup_view", None) is not None
                and event.type() in (QEvent.StyleChange, QEvent.PaletteChange)
                and not getattr(QApplication.instance(), "_winbox_popup_theme_depth", 0)):
            self.set_light_theme(detect_light_theme(self, getattr(self, "_popup_light", False)))
