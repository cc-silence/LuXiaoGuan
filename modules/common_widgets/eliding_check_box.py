"""Native styled checkbox that yields space to adjacent action buttons."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox,QStyle,QStyleOptionButton,QStylePainter,QSizePolicy


class ElidingCheckBox(QCheckBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Expanding)
        self.setSizePolicy(policy)

    def minimumSizeHint(self):
        hint=super().minimumSizeHint()
        option=QStyleOptionButton();self.initStyleOption(option)
        indicator=self.style().pixelMetric(QStyle.PM_IndicatorWidth,option,self)
        spacing=self.style().pixelMetric(QStyle.PM_CheckBoxLabelSpacing,option,self)
        hint.setWidth(indicator+spacing+self.fontMetrics().horizontalAdvance('...'))
        return hint

    def paintEvent(self,event):
        option=QStyleOptionButton();self.initStyleOption(option)
        rect=self.style().subElementRect(QStyle.SE_CheckBoxContents,option,self)
        option.text=self.fontMetrics().elidedText(option.text,Qt.ElideRight,max(0,rect.width()))
        painter=QStylePainter(self)
        painter.drawControl(QStyle.CE_CheckBox,option)
