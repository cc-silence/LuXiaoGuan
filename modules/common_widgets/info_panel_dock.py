"""Resizable information docks with explicit hide/restore controls."""
from PySide6.QtCore import Qt, QEvent, QSize, QPoint
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSplitter
from modules.common_widgets.icon_button import create_themed_icon_button
from modules.config_manager import BASE_DIR
from modules.dialog_theme import detect_light_theme
from modules.icon_utils import get_theme_icon
from modules.ui_scale_manager import UiScaleManager


class InfoPanelDock(QWidget):
    """Keep the reopen control outside a hidden panel; retain its last extent."""
    def __init__(self, content, panel, orientation, parent=None, sizes=(600, 240)):
        super().__init__(parent)
        self.setObjectName("infoPanelDock")
        self.panel = panel
        self.orientation = orientation
        self.collapsed = False
        self._expanded_ratio = sizes[1] / sum(sizes)
        self._refreshing = False
        self._toggle_insets = (0.0, 0.0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.splitter = QSplitter(orientation, self)
        self.splitter.setObjectName("captureContentSplitter")
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter)
        self.splitter.addWidget(content)
        panel.setMaximumWidth(16777215)
        self.splitter.addWidget(panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes(sizes)
        self.splitter.splitterMoved.connect(self._remember_sizes)
        self.toggle_button = create_themed_icon_button(self, "", "")
        self.toggle_button.setObjectName("downloadInfoZoomButton")
        self.toggle_button.setCheckable(True)
        self.toggle_button.clicked.connect(self.set_collapsed)
        self._overlay = getattr(panel, "zoom_overlay", None)
        if self._overlay is not None:
            self._overlay.layout().addWidget(self.toggle_button)
        self.refresh_appearance()

    def _remember_sizes(self, *_args):
        if self.collapsed:
            return
        sizes = self.splitter.sizes()
        if len(sizes) == 2 and sum(sizes) and sizes[1] > 0:
            self._expanded_ratio = sizes[1] / sum(sizes)

    def set_collapsed(self, collapsed):
        collapsed = bool(collapsed)
        if collapsed == self.collapsed:
            return
        if collapsed:
            self._remember_sizes()
            if self._overlay is not None:
                position = self.toggle_button.mapTo(self, QPoint())
                factor = UiScaleManager.for_widget(self).global_scale / 100
                self._toggle_insets = (
                    (self.width() - position.x() - self.toggle_button.width()) / factor,
                    (self.height() - position.y() - self.toggle_button.height()) / factor,
                )
                self._overlay.layout().removeWidget(self.toggle_button)
            self.toggle_button.setParent(self)
            self.panel.hide()
        else:
            self.panel.show()
            total = self.width() if self.orientation == Qt.Horizontal else self.height()
            total = max(1, total - self.splitter.handleWidth())
            extent = round(total * self._expanded_ratio)
            self.splitter.setSizes([total - extent, extent])
            if self._overlay is not None:
                self._overlay.layout().addWidget(self.toggle_button)
        self.collapsed = collapsed
        self.toggle_button.setChecked(collapsed)
        self.toggle_button.show()
        self.refresh_appearance()

    def refresh_appearance(self):
        if self._refreshing or not hasattr(self, "toggle_button"):
            return
        self._refreshing = True
        try:
            scale = UiScaleManager.for_widget(self)
            self.splitter.setHandleWidth(scale.px(5))
            if self.orientation == Qt.Horizontal:
                self.panel.setMinimumWidth(scale.px(160))
                direction = "left" if self.collapsed else "right"
            else:
                direction = "top" if self.collapsed else "bottom"
            path = str(BASE_DIR / ("images/icons/cil-chevron-" + direction + ".png"))
            self.toggle_button.setProperty("iconPath", path)
            self.toggle_button.setIcon(get_theme_icon(path, detect_light_theme(self)))
            self.toggle_button.setFixedSize(scale.px(26), scale.px(26))
            self.toggle_button.setIconSize(QSize(scale.px(16), scale.px(16)))
            text = "\u5c55\u5f00\u4fe1\u606f\u7a97\u53e3" if self.collapsed else "\u6536\u8d77\u4fe1\u606f\u7a97\u53e3"
            self.toggle_button.setToolTip(text)
            self.toggle_button.setAccessibleName(text)
            self._position_toggle()
        finally:
            self._refreshing = False

    def _position_toggle(self):
        if not hasattr(self, "toggle_button"):
            return
        if self.toggle_button.parentWidget() is self:
            factor = UiScaleManager.for_widget(self).global_scale / 100
            right, bottom = (round(value * factor) for value in self._toggle_insets)
            x = max(0, self.width() - self.toggle_button.width() - right)
            y = max(0, self.height() - self.toggle_button.height() - bottom)
            self.toggle_button.move(x, y)
        elif self._overlay is not None:
            self.panel._position_zoom_overlay()
        self.toggle_button.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_toggle()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.StyleChange, QEvent.FontChange):
            self.refresh_appearance()
