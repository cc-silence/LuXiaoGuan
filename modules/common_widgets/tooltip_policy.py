"""WinBox 统一鼠标提示策略。

控件只保存原始提示文字；应用级事件过滤器在显示时统一生成富文本，
避免纯文本和各窗口手写 HTML 使用不同的内部边距。
"""

from html import escape

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractItemView, QToolTip, QWidget
from PySide6.QtGui import QTextLayout, QTextOption, QFont
from modules.ui_scale_manager import UiScaleManager

TOOLTIP_TEXT_WIDTH = 360
# padding 写死在 HTML 里，不依赖 QSS，确保每次显示边距一致。
# QSS 的 QToolTip padding 已设为 0，避免双重叠加。
_TOOLTIP_BODY_STYLE = "margin: 0; padding: 8px 12px;"
_TOOLTIP_DIV_STYLE = "margin: 0; padding: 0; white-space: pre;"


def format_tooltip_text(text: str, font=None, width=TOOLTIP_TEXT_WIDTH) -> str:
    """将普通提示文本转换为固定边距的统一富文本。

    参数：text 是控件原始提示文字，可以包含换行。
    返回值：供 QToolTip.showText() 使用的 HTML 字符串。
    副作用：无；函数会转义输入文本，避免提示内容中的尖括号被当作 HTML 解析。
    padding 写死在 HTML body 上（8px 12px），不依赖 QSS 里的 QToolTip padding，
    避免 QSS 有时生效有时不生效导致边距不一致。
    """
    font = QFont(QToolTip.font()) if font is None else QFont(font)
    wrapped = []
    for paragraph in str(text).split("\n"):
        if not paragraph:
            wrapped.append("")
            continue
        utf16 = paragraph.encode("utf-16-le")
        layout = QTextLayout(paragraph, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            start = line.textStart() * 2
            end = start + line.textLength() * 2
            wrapped.append(utf16[start:end].decode("utf-16-le"))
        layout.endLayout()
    normalized = "<br>".join(escape(line) for line in wrapped)
    return (
        f'<html><body style="{_TOOLTIP_BODY_STYLE}">'
        f'<div style="{_TOOLTIP_DIV_STYLE}">'
        f"{normalized}</div></body></html>"
    )



class TooltipPolicyFilter(QObject):
    """在 Tooltip 显示前统一文本布局的应用级事件过滤器。

    过滤器只处理 QWidget 的 QEvent.ToolTip，不修改控件保存的原始 tooltip。
    因此业务模块仍按 Qt 原生方式调用 setToolTip()，长短提示都使用相同的外层 QSS 边距。
    """

    def eventFilter(self, watched, event):
        """拦截 tooltip 事件并显示标准化内容。

        参数：watched 是接收事件的 Qt 对象，event 是当前 Qt 事件。
        返回值：处理 tooltip 时返回 True 阻止 Qt 的默认显示，其余事件交给父类。
        副作用：可能显示或隐藏全局 QToolTip，不修改业务控件的状态和配置。
        """
        # QApplication 事件过滤器也会收到 QSpacerItem 等非 QObject 对象；PySide 的基类
        # eventFilter 不能接收它们，直接返回 False 交回 Qt 即可。
        if not isinstance(watched, QObject):
            return False
        if event.type() != QEvent.ToolTip or not isinstance(watched, QWidget):
            return super().eventFilter(watched, event)
        text = self._item_view_tooltip(watched, event) or watched.toolTip()
        if text:
            scale = UiScaleManager.for_widget(watched)
            font = QFont(scale._resolved_font(), scale.font_pt(1))
            screen = watched.screen()
            width = min(scale.px(TOOLTIP_TEXT_WIDTH), max(120, screen.availableGeometry().width() - 48))
            QToolTip.showText(event.globalPos(), format_tooltip_text(text, font, width), watched)
        else:
            QToolTip.hideText()
        return True

    @staticmethod
    def _item_view_tooltip(watched, event):
        """返回鼠标所在表格/列表单元格的 TooltipRole 文本。

        参数：watched 是接收 Tooltip 事件的控件；event 提供相对鼠标位置。
        返回值：当前索引的提示文本；鼠标不在项目视图单元格时返回空字符串。
        副作用：无，只读取模型数据。
        调用限制：QTableWidget 的 Tooltip 事件通常由 viewport 接收，不能只读取
        watched.toolTip()，否则 QTableWidgetItem.setToolTip() 写入的备注会被全局策略吞掉。
        """
        view = None
        viewport_pos = event.pos()
        if isinstance(watched, QAbstractItemView):
            view = watched
            viewport_pos = view.viewport().mapFrom(watched, event.pos())
        else:
            parent = watched.parentWidget()
            if isinstance(parent, QAbstractItemView) and watched is parent.viewport():
                view = parent
        if view is None:
            return ""
        index = view.indexAt(viewport_pos)
        if not index.isValid():
            return ""
        return str(index.data(Qt.ToolTipRole) or "")


def install_tooltip_policy(app) -> TooltipPolicyFilter | None:
    """为 QApplication 安装一次全局 Tooltip 统一策略。

    参数：app 是当前 QApplication 实例；传入 None 时不执行。
    返回值：已安装的 TooltipPolicyFilter，方便调用方长期持有或测试。
    副作用：向 QApplication 注册事件过滤器，但同一应用实例只会注册一次。
    """
    if app is None:
        return None
    existing = getattr(app, "_winbox_tooltip_policy", None)
    if existing is not None:
        return existing
    policy = TooltipPolicyFilter(app)
    from .widget_event_filters import install_widget_event_filter
    install_widget_event_filter(app, policy, lambda widget: True)
    app._winbox_tooltip_policy = policy
    return policy
