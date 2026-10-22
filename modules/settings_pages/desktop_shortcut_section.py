"""Immediate desktop action using the same settings controls and scale rules."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from modules.ui_scale_manager import UiScaleManager
from .settings_widgets import create_settings_button, create_hint_label, create_settings_section_title


class DesktopShortcutSection(QWidget):
    def __init__(self, presenter, parent=None):
        super().__init__(parent)
        self.presenter = presenter
        layout = QVBoxLayout(self)
        UiScaleManager.for_widget(self).bind_layout(layout, margins=(0, 0, 0, 0), spacing=8)
        layout.addWidget(create_settings_section_title("\u684c\u9762\u5feb\u6377\u65b9\u5f0f", self))
        row = QHBoxLayout()
        self.button = create_settings_button("\u6dfb\u52a0\u684c\u9762\u5feb\u6377\u65b9\u5f0f", self, "secondary")
        self.button.setAutoDefault(False)
        self.button.setDefault(False)
        self.button.clicked.connect(presenter.add_manually)
        row.addWidget(self.button)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(create_hint_label("\u7acb\u5373\u751f\u6548\uff0c\u65e0\u9700\u4fdd\u5b58\u3002", self))
        presenter.activityChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self):
        self.button.setEnabled(not self.presenter.active)
        self.button.setText("\u6b63\u5728\u5904\u7406\u2026\u2026" if self.presenter.active
                            else "\u6dfb\u52a0\u684c\u9762\u5feb\u6377\u65b9\u5f0f")
