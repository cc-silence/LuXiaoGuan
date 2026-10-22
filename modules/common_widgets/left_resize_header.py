"""支持从指定列左边界调整列宽的表头控件。"""

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QHeaderView


class LeftResizeHeaderView(QHeaderView):
    """让指定列可以从左侧分隔线调整宽度。

    参数：orientation 是表头方向；status_section 是需要从左边界拖动的列号；
    companion_sections 是状态列调整时共同分配空间的列号序列；
    minimum_sections 是各列的业务最小宽度映射。
    返回值：无。控件会在用户拖动时直接修改目标列宽，并发出 Qt 原生的
    ``sectionResized`` 信号，因此调用方原有的列宽保存逻辑仍然有效。
    限制：只接管目标列左边界附近的鼠标拖动，其他表头行为交给 QHeaderView。
    """

    _HANDLE_TOLERANCE = 5

    def __init__(self, orientation, status_section, companion_sections=(), minimum_sections=None, parent=None):
        super().__init__(orientation, parent)
        self._status_section = int(status_section)
        self._companion_sections = tuple(int(section) for section in companion_sections)
        self._minimum_sections = {
            int(section): int(width) for section, width in (minimum_sections or {}).items()
        }
        self._dragging_status = False
        self._drag_start_x = 0
        self._drag_start_width = 0
        self._drag_start_companion_widths = {}
        self._fitting_viewport = False
        self._resize_targets = []
        self._preferred_widths = None
        if parent is not None:
            parent.installEventFilter(self)
            viewport = getattr(parent, "viewport", lambda: None)()
            if viewport is not None:
                viewport.installEventFilter(self)
                self._resize_targets.append(viewport)

    def eventFilter(self, watched, event):
        """监听所属表格尺寸变化，延迟到布局完成后重新分配列宽。"""
        if watched in [self.parent(), *self._resize_targets] and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self.fit_to_viewport)
        return super().eventFilter(watched, event)

    def _on_status_left_handle(self, x):
        """判断鼠标是否位于状态列左侧的可拖动区域。"""
        left = self.sectionViewportPosition(self._status_section)
        return abs(x - left) <= self._HANDLE_TOLERANCE

    def _on_status_right_handle(self, x):
        """判断鼠标是否位于状态列右侧的锁定边界。"""
        right = self.sectionViewportPosition(self._status_section) + self.sectionSize(self._status_section)
        return abs(x - right) <= self._HANDLE_TOLERANCE

    def _minimum_for_section(self, section):
        """返回指定列的最小宽度，未单独配置时使用 Qt 默认值。"""
        return max(self.minimumSectionSize(), self._minimum_sections.get(section, 0))

    @property
    def is_fitting_viewport(self):
        """返回当前是否正在执行自动贴右调整。"""
        return self._fitting_viewport

    def set_preferred_widths(self, widths, minimums=None):
        """启用保留列宽模式；只分配多余空间，不为塞进视口压缩列，旧调用方不受影响。"""
        self._preferred_widths = dict(widths)
        if minimums is not None:
            self._minimum_sections = dict(minimums)

    def _fit_preferred_widths(self):
        widths = {section: max(self._minimum_for_section(section), self._preferred_widths.get(section, self.sectionSize(section)))
                  for section in range(self.count()) if not self.isSectionHidden(section)}
        extra = max(0, self.viewport().width() - sum(widths.values()))
        flexible = [section for section in (*self._companion_sections, self._status_section) if section in widths]
        if not flexible:
            return
        total = sum(widths[section] for section in flexible)
        remaining = extra
        for section in flexible[:-1]:
            addition = min(remaining, round(extra * widths[section] / max(1, total)))
            widths[section] += addition
            remaining -= addition
        widths[flexible[-1]] += remaining
        for section, width in widths.items():
            self.resizeSection(section, width)

    def fit_to_viewport(self):
        """让状态列右边界贴住表头右侧，并在空间不足时压缩可调列。

        参数：无。
        返回值：无。
        副作用：修改状态列和 companion_sections 的宽度，不写配置。
        调用限制：表头已经安装到表格后调用；拖动过程中不会强行重排。
        """
        if self._dragging_status or self._fitting_viewport or not self._companion_sections:
            return
        self._fitting_viewport = True
        try:
            if self._preferred_widths is not None:
                self._fit_preferred_widths()
                return
            minimum = self._minimum_for_section(self._status_section)
            viewport_width = self.viewport().width()

            # 恢复配置或窗口缩放后，先保证软件和文件名列不会低于业务最小宽度。
            for section in self._companion_sections:
                current = self.sectionSize(section)
                section_minimum = self._minimum_for_section(section)
                if current < section_minimum:
                    self.resizeSection(section, section_minimum)

            left = self.sectionPosition(self._status_section)
            status_width = max(
                self._minimum_for_section(self._status_section),
                self.sectionSize(self._status_section),
            )
            if self.sectionSize(self._status_section) != status_width:
                self.resizeSection(self._status_section, status_width)

            # 窗口变宽时，新增空间按当前比例分给软件、文件名和状态列，
            # 避免所有空白都集中到最右侧的状态列。
            flexible_sections = list(self._companion_sections) + [self._status_section]
            flexible_total = sum(self.sectionSize(section) for section in flexible_sections)
            companion_total = sum(self.sectionSize(section) for section in self._companion_sections)
            fixed_before_status = left - companion_total
            available_total = viewport_width - fixed_before_status
            extra_width = available_total - flexible_total
            if extra_width > 0 and flexible_total > 0:
                remaining = extra_width
                for index, section in enumerate(flexible_sections):
                    if index == len(flexible_sections) - 1:
                        addition = remaining
                    else:
                        addition = round(extra_width * self.sectionSize(section) / flexible_total)
                        addition = min(addition, remaining)
                    self.resizeSection(section, self.sectionSize(section) + addition)
                    remaining -= addition
                final_status_width = max(
                    self._minimum_for_section(self._status_section),
                    viewport_width - self.sectionPosition(self._status_section),
                )
                self.resizeSection(self._status_section, final_status_width)
                return

            # 窗口变窄时三列也按当前比例同步缩小，避免状态列单独被压到最小值。
            if extra_width < 0 and flexible_total > 0:
                target_total = max(
                    sum(self._minimum_for_section(section) for section in flexible_sections),
                    available_total,
                )
                remaining = target_total
                for index, section in enumerate(flexible_sections):
                    section_minimum = self._minimum_for_section(section)
                    if index == len(flexible_sections) - 1:
                        target_width = max(section_minimum, remaining)
                    else:
                        ratio = self.sectionSize(section) / flexible_total
                        target_width = max(section_minimum, round(target_total * ratio))
                        remaining_minimum = sum(
                            self._minimum_for_section(item) for item in flexible_sections[index + 1:]
                        )
                        target_width = min(target_width, remaining - remaining_minimum)
                    self.resizeSection(section, target_width)
                    remaining -= target_width
                final_status_width = max(
                    self._minimum_for_section(self._status_section),
                    viewport_width - self.sectionPosition(self._status_section),
                )
                self.resizeSection(self._status_section, final_status_width)
                return

            overflow = left + minimum - viewport_width

            # 窗口变窄时只压缩允许自定义的列，固定列不被破坏。
            if overflow > 0:
                for section in self._companion_sections:
                    current = self.sectionSize(section)
                    section_minimum = self._minimum_for_section(section)
                    reducible = max(0, current - section_minimum)
                    reduction = min(reducible, overflow)
                    if reduction:
                        self.resizeSection(section, current - reduction)
                        overflow -= reduction
                    if overflow <= 0:
                        break

            left = self.sectionPosition(self._status_section)
            target_width = max(minimum, viewport_width - left)
            if self.sectionSize(self._status_section) != target_width:
                self.resizeSection(self._status_section, target_width)
        finally:
            self._fitting_viewport = False

    def resizeEvent(self, event):
        """表格尺寸变化后重新计算状态列，避免右侧空白或超出视口。"""
        super().resizeEvent(event)
        self.fit_to_viewport()

    def mousePressEvent(self, event):
        """在状态列左边界开始拖动，否则使用 Qt 默认处理。"""
        # 状态列右侧必须锁定在表格最右端，不能让 Qt 原生逻辑从右侧改变宽度。
        if event.button() == Qt.LeftButton and self._on_status_right_handle(event.position().toPoint().x()):
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._on_status_left_handle(event.position().toPoint().x()):
            self._dragging_status = True
            self._drag_start_x = event.position().toPoint().x()
            self._drag_start_width = self.sectionSize(self._status_section)
            self._drag_start_companion_widths = {
                section: self.sectionSize(section) for section in self._companion_sections
            }
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """拖动状态列左边界时反向调整状态列宽度。"""
        x = event.position().toPoint().x()
        if self._dragging_status:
            delta = x - self._drag_start_x
            minimum = self._minimum_for_section(self._status_section)
            companions_total = sum(self._drag_start_companion_widths.values())
            companion_minimum = sum(
                self._minimum_for_section(section) for section in self._companion_sections
            )
            maximum_status_width = self._drag_start_width + companions_total - companion_minimum
            new_width = min(
                maximum_status_width,
                max(minimum, self._drag_start_width - delta),
            )
            self.resizeSection(self._status_section, new_width)
            space_delta = new_width - self._drag_start_width
            target_companions_total = companions_total - space_delta
            remaining = target_companions_total
            sections = list(self._companion_sections)
            for index, section in enumerate(sections):
                if index == len(sections) - 1:
                    companion_width = max(self._minimum_for_section(section), remaining)
                else:
                    start_width = self._drag_start_companion_widths[section]
                    ratio = start_width / companions_total if companions_total else 0
                    section_minimum = self._minimum_for_section(section)
                    companion_width = max(section_minimum, round(target_companions_total * ratio))
                    remaining_minimum = sum(
                        self._minimum_for_section(item) for item in sections[index + 1:]
                    )
                    companion_width = min(companion_width, remaining - remaining_minimum)
                self.resizeSection(section, companion_width)
                remaining -= companion_width
            event.accept()
            return
        if self._on_status_right_handle(x):
            self.unsetCursor()
            event.accept()
            return
        if self._on_status_left_handle(x):
            self.setCursor(Qt.SizeHorCursor)
            event.accept()
            return
        self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """结束状态列左边界拖动，否则使用 Qt 默认处理。"""
        if self._dragging_status and event.button() == Qt.LeftButton:
            self._dragging_status = False
            self._drag_start_companion_widths = {}
            self.fit_to_viewport()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        """鼠标离开表头后恢复默认光标。"""
        if not self._dragging_status:
            self.unsetCursor()
        super().leaveEvent(event)
