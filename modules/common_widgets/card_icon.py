"""A bounded, scalable color illustration for settings cards."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter
from PySide6.QtWidgets import QWidget
from modules.ui_scale_manager import UiScaleManager


class CardIcon(QWidget):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.icon = QIcon(str(path))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        UiScaleManager.for_widget(self).bind_metrics(self, fixed_size=(64, 64))

    def paintEvent(self, event):
        painter = QPainter(self)
        self.icon.paint(painter, self.rect(), Qt.AlignCenter, QIcon.Normal)
