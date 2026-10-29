"""Themed sections that retain existing action cards across responsive layouts."""
from PySide6.QtWidgets import QVBoxLayout, QGridLayout, QLabel, QFrame
from modules.ui_scale_manager import UiScaleManager


class SettingsCardGroups:
    def __init__(self, parent, layout, groups):
        self.layout = layout
        self.groups = tuple(groups)
        self.headers = []
        self.sections = []
        self.grids = []
        scale = UiScaleManager.for_widget(parent)
        for row, (title, cards) in enumerate(self.groups):
            section = QFrame(parent)
            section.setObjectName("captureSettingsGroup")
            contents = QVBoxLayout(section)
            scale.bind_layout(contents, margins=(0, 0, 0, 0), spacing=12)
            header = QLabel(title, section)
            header.setObjectName("captureSettingsGroupTitle")
            contents.addWidget(header)
            grid = QGridLayout()
            scale.bind_layout(grid, margins=(0, 0, 0, 0), spacing=12)
            contents.addLayout(grid)
            for card in cards:
                card.setParent(section)
            self.headers.append(header)
            self.sections.append(section)
            self.grids.append(grid)
            layout.addWidget(section, row, 0)
        scale.bind_layout(layout, margins=(10, 8, 10, 14), spacing=24)
        layout.setColumnStretch(0, 1)
        layout.setRowStretch(len(self.groups), 1)

    def place(self, columns):
        for grid, (_title, cards) in zip(self.grids, self.groups):
            for row in range(grid.rowCount()):
                grid.setRowStretch(row, 0)
            for column in range(3):
                grid.setColumnStretch(column, 1 if column < columns else 0)
            for card in cards:
                grid.removeWidget(card)
            for index, card in enumerate(cards):
                grid.addWidget(card, index // columns, index % columns)
