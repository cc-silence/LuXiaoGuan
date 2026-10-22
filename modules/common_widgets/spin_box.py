"""公共数字输入框：按完整数值范围和后缀留宽，主题由公共 QSS 提供。"""

from PySide6.QtWidgets import QSpinBox, QDoubleSpinBox


class CommonSpinBox(QSpinBox):
    """保留原生编辑行为，避免布局压缩后数字被箭头遮挡。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._compact_digits = None
        self.valueChanged.connect(self._update_compact_geometry)
        self.lineEdit().textEdited.connect(self._update_compact_geometry)

    def set_compact_digits(self, digits):
        """按常用位数留宽；更长的当前数值自动扩展，不改变合法范围。"""
        self._compact_digits = max(1, int(digits))
        self.updateGeometry()

    def _update_compact_geometry(self, *_args):
        if self._compact_digits is not None:
            self.updateGeometry()

    def sizeHint(self):
        hint = super().sizeHint()
        digits = getattr(self, "_compact_digits", None)
        if digits is not None:
            metrics = self.fontMetrics()
            affix = self.prefix() + self.suffix()
            native_width = max(metrics.horizontalAdvance(self.textFromValue(value) + affix)
                               for value in (self.minimum(), self.maximum()))
            native_width = max(native_width, metrics.horizontalAdvance(self.specialValueText()))
            digit = max("0123456789", key=metrics.horizontalAdvance)
            compact_width = max(metrics.horizontalAdvance(digit * digits + affix),
                                metrics.horizontalAdvance(self.text()))
            hint.setWidth(hint.width() - native_width + compact_width)
        return hint

    def minimumSizeHint(self):
        return self.sizeHint()


class CommonDoubleSpinBox(QDoubleSpinBox):
    """小数输入与整数输入共用尺寸政策及上下按钮样式。"""

    def minimumSizeHint(self):
        return self.sizeHint()
