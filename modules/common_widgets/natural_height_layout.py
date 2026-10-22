"""Layouts that preserve the natural height of wrapped cards in scroll content."""
from PySide6.QtWidgets import QVBoxLayout


class NaturalHeightVBoxLayout(QVBoxLayout):
    """Include height-for-width in SetMinimumSize before arranging child cards.

    Qt's ordinary minimum omits wrapped text height. A scroll container can
    otherwise lay out a new animation frame before its viewport has grown,
    briefly compressing preceding cards. No deferred resize or timer is used.
    """

    def minimumSize(self):
        hint = super().minimumSize()
        parent = self.parentWidget()
        if parent is not None and self.hasHeightForWidth():
            height = self.totalHeightForWidth(max(parent.width(), hint.width()))
            hint.setHeight(max(hint.height(), height))
        return hint
