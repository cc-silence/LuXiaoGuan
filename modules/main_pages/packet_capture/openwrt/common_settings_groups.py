"""Presentation-only grouping for the existing common-settings cards."""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QFrame
from modules.ui_scale_manager import UiScaleManager


class SettingsCardGroups:
    def __init__(self, parent, layout, groups):
        self.layout = layout
        self.groups = tuple(groups)
        self.headers = []
        for title, _cards in self.groups:
            header = QWidget(parent)
            line = QHBoxLayout(header)
            UiScaleManager.for_widget(parent).bind_layout(line, margins=(0, 2, 0, 0), spacing=12)
            label = QLabel(title, header)
            label.setObjectName('captureGroupTitle')
            line.addWidget(label)
            separator = QFrame(header)
            separator.setObjectName('captureSectionSeparator')
            UiScaleManager.for_widget(parent).bind_metrics(separator, fixed_height=1)
            line.addWidget(separator, 1)
            self.headers.append(header)

    def place(self, columns):
        layout = self.layout
        for row in range(layout.rowCount()):
            layout.setRowStretch(row, 0)
        for column in range(3):
            layout.setColumnStretch(column, 1 if column < columns else 0)
        for header, (_title, cards) in zip(self.headers, self.groups):
            layout.removeWidget(header)
            for card in cards:
                layout.removeWidget(card)
        row = 0
        for header, (_title, cards) in zip(self.headers, self.groups):
            layout.addWidget(header, row, 0, 1, columns)
            row += 1
            for index, card in enumerate(cards):
                layout.addWidget(card, row + index // columns, index % columns)
            row += (len(cards) + columns - 1) // columns
        layout.setRowStretch(row, 1)
