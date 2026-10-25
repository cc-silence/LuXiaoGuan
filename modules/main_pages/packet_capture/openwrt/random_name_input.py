"""Shared Wi-Fi name input with an explicit random action."""
from PySide6.QtWidgets import QWidget,QHBoxLayout
from modules.settings_pages.settings_widgets import create_settings_button
from modules.ui_scale_manager import UiScaleManager
from modules.capture_services.openwrt.relocation_identity import generate_relocation_hostname
from .signal_setup_forms import text_input

class RandomNameInput(QWidget):
    def __init__(self,parent=None,value="",placeholder=""):
        super().__init__(parent)
        layout=QHBoxLayout(self)
        UiScaleManager.for_widget(self).bind_layout(layout,margins=(0,0,0,0),spacing=6)
        self.edit=text_input(self,value,placeholder)
        self.random_button=create_settings_button("随机",self,"secondary")
        layout.addWidget(self.edit,1)
        layout.addWidget(self.random_button)
        self.random_button.clicked.connect(self.randomize)

    def randomize(self):
        self.edit.setText(generate_relocation_hostname(self.edit.text()))
