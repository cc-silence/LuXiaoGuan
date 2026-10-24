"""OpenWrt 扫描结果和目标选择表格。"""

from contextlib import contextmanager

from PySide6.QtCore import Qt, QTimer, Signal, QSignalBlocker
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableWidget, QTableWidgetItem, QStyle, QStyleOptionViewItem

from modules.capture_services.openwrt.models import AccessPointCaptureState
from modules.capture_services.privacy_mask import mask_text
from modules.display_privacy import controller_for, masker_for, Masker
from modules.common_widgets.auto_hide_scroll_bar import AutoHideScrollBar
from modules.common_widgets.checkbox_paint import CheckboxPaintController
from modules.dialog_theme import create_themed_menu, detect_light_theme
from modules.ui_scale_manager import UiScaleManager
from modules.common_widgets.spaced_item_delegate import SpacedItemDelegate, minimum_row_height

from .privacy_header_view import EYE_COLOR_DARK, EYE_COLOR_LIGHT, PrivacyHeaderView


class _CaptureDelegate(SpacedItemDelegate):
    """运行行保留黄色背景，不被选中高亮覆盖。"""

    def paint(self, painter, option, index):
        table = self.parent()
        identity = index.siblingAtColumn(table.STATUS_COLUMN).data(Qt.UserRole)
        if str(identity).upper() in table._capturing:
            option = QStyleOptionViewItem(option)
            option.state &= ~(QStyle.State_Selected | QStyle.State_HasFocus)
            painter.fillRect(option.rect, table._capturing_bg())
        super().paint(painter, option, index)
        return



class _NumberItem(QTableWidgetItem):
    """序号列数字项；按整数比较，避免 "10" 排在 "9" 前面。"""

    def __lt__(self, other):
        try:
            return int(self.data(Qt.UserRole)) < int(other.data(Qt.UserRole))
        except (TypeError, ValueError):
            return super().__lt__(other)


class AccessPointTable(QTableWidget):
    """展示服务层 AP 快照并收集目标 BSSID。

    参数：parent 是常规抓包页面。信号 selectionChangedByUser 仅携带 BSSID 元组。
    副作用：只修改表格和选择模型，不直接修改配置或调用抓包服务。
    """

    selectionChangedByUser = Signal(object)
    columnWidthsChanged = Signal(object)
    maskingChanged = Signal()
    privacyDisplayChanged = Signal()
    clearStatusRequested = Signal(str)
    HEADERS = ("序号", "状态", "路由器 Mac", "Wi-Fi 名称", "信号强度", "客户端", "WPS", "信道", "加密")
    STATUS_COLUMN = 1
    NUMBER_COLUMN = 0
    BSSID_COLUMN = 2
    SSID_COLUMN = 3

    def __init__(self, parent=None):
        super().__init__(0, len(self.HEADERS), parent)
        self.setObjectName("captureAccessPointTable")
        self.setProperty("captureResults", True)
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setShowGrid(False)
        self.setItemDelegate(_CaptureDelegate(self, show_focus_rect=False))
        self.setSortingEnabled(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setVerticalScrollBar(AutoHideScrollBar(Qt.Vertical, self))
        self.setHorizontalScrollBar(AutoHideScrollBar(Qt.Horizontal, self))
        self._privacy_header = PrivacyHeaderView(Qt.Horizontal, self)
        self.setHorizontalHeader(self._privacy_header)
        # 换表头会重置为不可点击，必须显式恢复，否则所有表头点击失效。
        self._privacy_header.setSectionsClickable(True)
        self._privacy_header.set_eye_sections({self.BSSID_COLUMN, self.SSID_COLUMN})
        self._privacy_header.eyeClicked.connect(self._on_eye_clicked)
        self._masked = {"bssid": False, "ssid": False}
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        # 标题字号与留白统一交给主题，不能再用构造时的字体锁死表头高度。
        self._column_scale = 100
        # 序号是普通列，左侧特殊行号栏不再使用，直接隐藏。
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.horizontalHeaderItem(self.BSSID_COLUMN).setToolTip("点击表头右侧眼睛隐藏/显示本列内容")
        self.horizontalHeaderItem(self.SSID_COLUMN).setToolTip("点击表头右侧眼睛隐藏/显示本列内容")
        for index, width in enumerate((48, 86, 150, 180, 88, 72, 80, 66, 100)):
            self.setColumnWidth(index, width)
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        header.setSortIndicator(-1, Qt.AscendingOrder)
        self._points = ()
        self._privacy = controller_for(self)
        self._ssid_values = set()
        self._bssid_values = set()
        self._identity_overflow = False
        self._local_mask_cache_key = None
        self._local_mask_cache = None
        if self._privacy is not None:
            self._privacy.changed.connect(self._refresh_display_privacy)
        self._updating = False
        self._restoring_column_widths = False
        self._light_theme = False
        self._capturing = set()
        self._fit_height_to_content()
        self.itemChanged.connect(self._on_item_changed)
        self.customContextMenuRequested.connect(self._open_context_menu)
        header.widthsEdited.connect(self._column_width_changed)
        self._checkbox_paint = CheckboxPaintController(
            self, self.STATUS_COLUMN, can_edit=self._can_paint, on_finished=self._paint_finished)
        self._refresh_display_privacy()
        self._ensure_status_width()

    def _can_paint(self, index):
        point = next((entry for entry in self._points if entry.bssid == index.data(Qt.UserRole)), None)
        return bool(not self._updating and point and
                    not self._selection_blocked(point))

    @staticmethod
    def _selection_blocked(point):
        """已知名称的开放网络仍禁选；扫描器标记的隐藏信号允许勾选。"""
        return (str(point.encryption or "").strip().upper() == "OPN"
                and point.ssid != "__hidden__")

    def _paint_finished(self):
        """一次涂选结束后统一通知服务，避免每行快照回放打断排序冻结。"""
        self.selectionChangedByUser.emit(self.selected_bssids())

    def _status_minimum_width(self):
        """按五个汉字、复选框及留白计算状态列下限，适配主题字体和缩放。"""
        scale = UiScaleManager.for_widget(self)
        indicator = max(self.style().pixelMetric(QStyle.PM_IndicatorWidth),
                        scale.px(16))
        return QFontMetrics(self.font()).horizontalAdvance("没有可用包") + indicator + scale.px(16)

    def _ensure_status_width(self):
        """只保护状态列，程序修正不触发用户列宽保存。"""
        minimum = self._status_minimum_width()
        if self.columnWidth(self.STATUS_COLUMN) < minimum:
            with QSignalBlocker(self.horizontalHeader()):
                self.setColumnWidth(self.STATUS_COLUMN, minimum)

    def set_column_widths(self, widths):
        """恢复用户保存的列宽；列数对不上（旧存档）时直接用默认，不错位。"""

        if not isinstance(widths, (list, tuple)) or len(widths) != self.columnCount():
            return
        self._restoring_column_widths = True
        try:
            for index, width in enumerate(widths[: self.columnCount()]):
                try:
                    normalized = max(40, min(int(width), 600))
                except (TypeError, ValueError):
                    continue
                width = round(normalized * self._column_scale / 100)
                if index == self.STATUS_COLUMN:
                    width = max(width, self._status_minimum_width())
                self.setColumnWidth(index, width)
        finally:
            self._restoring_column_widths = False

    def refresh_scale(self):
        """缩放现有列宽而非重置用户排列；程序调整不触发配置保存。"""
        scale = UiScaleManager.for_widget(self)
        self.verticalHeader().setMinimumSectionSize(minimum_row_height(self))
        self.verticalHeader().setDefaultSectionSize(minimum_row_height(self))
        ratio = scale.global_scale / self._column_scale
        header = self.horizontalHeader()
        with QSignalBlocker(header):
            for column in range(self.columnCount()):
                minimum = header.sectionSizeHint(column)
                if column == self.STATUS_COLUMN:
                    minimum = max(minimum, self._status_minimum_width())
                self.setColumnWidth(column, max(minimum, round(self.columnWidth(column) * ratio)))
        self._column_scale = scale.global_scale
        self._fit_height_to_content()

    def set_light_theme(self, is_light):
        """记录主题并按模型重刷所有行前景；不等下次数据到达。"""

        self._light_theme = bool(is_light)
        self._privacy_header.set_eye_color(
            EYE_COLOR_LIGHT if self._light_theme else EYE_COLOR_DARK)
        self.refresh_highlight()

    def refresh_highlight(self):
        """遍历现有行重算高亮；主题切换后调用，消除旧颜色残留。"""

        self._updating = True
        try:
            for row in range(self.rowCount()):
                status_item = self.item(row, self.STATUS_COLUMN)
                if status_item is None:
                    continue
                point = next(
                    (entry for entry in self._points
                     if entry.bssid == str(status_item.data(Qt.UserRole))),
                    None,
                )
                if point is None:
                    continue
                self._refresh_status_cell(row, point)
                self._apply_row_highlight(
                    (self.item(row, 2), self.item(row, 3), self.item(row, 5)), point
                )
                self._apply_capturing_row(row, point.bssid)
        finally:
            self._updating = False
        self.viewport().update()

    def _highlight_green(self):
        """有客户端/已出包的主题绿；亮色取深值保证白底可读。"""

        return QColor("#7ee2a8" if not self._light_theme else "#0a5c2e")

    def _capturing_bg(self):
        """正在抓包行的主题黄背景；亮色取浅值保证深字可读。"""

        return QColor("#574817" if not self._light_theme else "#fff0b3")

    def set_capturing_bssids(self, bssids):
        """按 BSSID 集合刷新正在抓包行的整行黄背景；空集合恢复全部行。"""

        wanted = {str(bssid).upper() for bssid in (bssids or ()) if str(bssid).strip()}
        if wanted == self._capturing:
            return
        self._capturing = wanted
        self._refresh_capturing_rows()

    def _refresh_capturing_rows(self):
        """重算所有行的抓包背景；排序勾选不受影响，只动背景角色。"""

        with QSignalBlocker(self):
            for row in range(self.rowCount()):
                status_item = self.item(row, self.STATUS_COLUMN)
                if status_item is None:
                    continue
                bssid = str(status_item.data(Qt.UserRole))
                point = next((entry for entry in self._points if entry.bssid == bssid), None)
                if point is not None:
                    self._refresh_status_cell(row, point)
                self._apply_capturing_row(row, bssid)
        self.viewport().update()

    def _apply_capturing_row(self, row, bssid):
        """该行正在抓包则整行铺黄背景，否则清除背景恢复交替色。"""

        color = self._capturing_bg() if str(bssid).upper() in self._capturing else None
        for column in range(self.columnCount()):
            item = self.item(row, column)
            if item is None:
                continue
            if color is None:
                item.setData(Qt.BackgroundRole, None)
            else:
                item.setBackground(color)

    def column_widths(self):
        """保存 100% 逻辑列宽，避免重启后再次乘缩放比例。"""

        return [round(self.columnWidth(index) * 100 / self._column_scale)
                for index in range(self.columnCount())]

    def mask_state(self):
        """返回掩码开关，供配置保存；键名与落盘字段一致。"""

        return {
            "mask_bssid_column": bool(self._masked["bssid"]),
            "mask_ssid_column": bool(self._masked["ssid"]),
        }

    def set_masked(self, mask_bssid=None, mask_ssid=None, notify=True):
        """设置掩码开关并重画；None 表示保持；notify=False 时不发保存信号。"""

        changed = False
        for key, value in (("bssid", mask_bssid), ("ssid", mask_ssid)):
            if value is not None and bool(value) != self._masked[key]:
                self._masked[key] = bool(value)
                changed = True
        if not changed:
            return
        self._privacy_header.set_masked(self.BSSID_COLUMN, self._effective_mask("bssid"))
        self._privacy_header.set_masked(self.SSID_COLUMN, self._effective_mask("ssid"))
        self._apply_mask_to_rows()
        if notify:
            self.maskingChanged.emit()

    def _on_eye_clicked(self, section):
        """表头眼睛翻转对应列掩码；排序不受影响。"""

        if self._privacy and self._privacy.enabled:
            return
        if section == self.BSSID_COLUMN:
            self.set_masked(mask_bssid=not self._masked["bssid"])
        elif section == self.SSID_COLUMN:
            self.set_masked(mask_ssid=not self._masked["ssid"])

    def _effective_mask(self, key):
        return self._masked[key] or bool(self._privacy and self._privacy.enabled)

    def local_masker(self):
        key = (self._masked["ssid"], self._masked["bssid"], len(self._ssid_values),
               len(self._bssid_values), self._identity_overflow)
        if key == self._local_mask_cache_key:
            return self._local_mask_cache
        values = set()
        if self._masked["ssid"]:
            values.update(self._ssid_values)
        if self._masked["bssid"]:
            values.update(self._bssid_values)
        if self._identity_overflow and any(self._masked.values()):
            result = Masker(overflow=True)
        else:
            result = masker_for(values, mask_mac=self._masked["bssid"])
        self._local_mask_cache_key, self._local_mask_cache = key, result
        return result

    def _refresh_display_privacy(self):
        self._privacy_header.set_masked(self.BSSID_COLUMN, self._effective_mask("bssid"))
        self._privacy_header.set_masked(self.SSID_COLUMN, self._effective_mask("ssid"))
        self._apply_mask_to_rows()
        self.privacyDisplayChanged.emit()

    def _display_bssid(self, point):
        """BSSID 显示文本；隐藏时固定圆点，真实值只留模型。"""
        return mask_text(point.bssid, self._effective_mask("bssid"))

    def _display_ssid(self, point):
        """SSID 显示文本；隐藏时连占位一起盖掉。"""
        return mask_text(point.ssid or "<隐藏网络>", self._effective_mask("ssid"))

    def _apply_mask_to_rows(self):
        """按当前开关重写可见行两列文本；模型与勾选不动。"""

        points_by_id = {point.bssid: point for point in self._points}
        for row in range(self.rowCount()):
            status_item = self.item(row, self.STATUS_COLUMN)
            if status_item is None:
                continue
            point = points_by_id.get(str(status_item.data(Qt.UserRole)))
            if point is None:
                continue
            bssid_item = self.item(row, self.BSSID_COLUMN)
            ssid_item = self.item(row, self.SSID_COLUMN)
            if bssid_item is not None and bssid_item.text() != self._display_bssid(point):
                bssid_item.setText(self._display_bssid(point))
            if ssid_item is not None and ssid_item.text() != self._display_ssid(point):
                ssid_item.setText(self._display_ssid(point))

    def _column_width_changed(self):
        """仅在用户拖动表头后通知保存，恢复配置时不触发重复写盘。"""

        if not self._restoring_column_widths:
            self._ensure_status_width()
            self.columnWidthsChanged.emit(self.column_widths())

    def set_access_points(self, access_points):
        """用不可变 AP 模型刷新表格，并按 BSSID 保留用户选择。

        同一批目标只就地更新单元格，行不动、滚动条不跳；只有目标集合变化
        （新扫描）才重建行。只有重建会按当前排序指示器重排。
        """

        points = tuple(access_points)
        if self._privacy is not None:
            self._privacy.register_values(value for point in points for value in (point.ssid, point.bssid))
        for point in points:
            if len(self._ssid_values) + len(self._bssid_values) < 50000:
                if point.ssid:
                    self._ssid_values.add(point.ssid)
                self._bssid_values.add(point.bssid)
            else:
                self._identity_overflow = True
        old_ids = []
        for row in range(self.rowCount()):
            item = self.item(row, self.STATUS_COLUMN)
            old_ids.append(str(item.data(Qt.UserRole)) if item is not None else "")
        if old_ids and sorted(old_ids) == sorted(point.bssid for point in points):
            self._update_rows_in_place(points)
        else:
            self._checkbox_paint.cancel()
            self._rebuild_rows(points)

    @contextmanager
    def _stable_update(self, restore_indicator):
        """批量改值期间冻结排序，避免逐行重排打乱循环索引。

        原理：关排序→清指示器→改值→重开。任务刷新传 False（冻结到底，
        用户再点表头才重排）；用户批量勾选传 True（最后非阻塞恢复指示器，
        做一次正确的最终排序）。注意：阻塞信号恢复指示器会激活后续改值重排，
        绝不能用于冻结路径。
        """

        header = self.horizontalHeader()
        saved_section = header.sortIndicatorSection()
        saved_order = header.sortIndicatorOrder()
        was_sorting = self.isSortingEnabled()
        prev_updating = self._updating
        painting = self._checkbox_paint.active
        self._updating = True
        try:
            self.setSortingEnabled(False)
            if not painting:
                header.setSortIndicator(-1, saved_order)
            yield
        finally:
            self.setSortingEnabled(was_sorting)
            if restore_indicator and saved_section >= 0:
                header.setSortIndicator(saved_section, saved_order)
            self._updating = prev_updating

    def _update_rows_in_place(self, points):
        """同批目标的单元格刷新，不增删行、不触动排序，用户指示器也不恢复。

        任务刷新只求冻结，指示器由用户下次点表头重建。
        """

        rows_by_bssid = {}
        for row in range(self.rowCount()):
            item = self.item(row, self.STATUS_COLUMN)
            if item is not None:
                rows_by_bssid[str(item.data(Qt.UserRole))] = row
        with self._stable_update(False):
            self._points = tuple(points)
            for point in points:
                row = rows_by_bssid.get(point.bssid)
                if row is None:
                    continue
                self._refresh_status_cell(row, point)
                status_item = self.item(row, self.STATUS_COLUMN)
                checked = Qt.Checked if point.selected and not self._selection_blocked(point) else Qt.Unchecked
                if not self._checkbox_paint.active and status_item.checkState() != checked:
                    status_item.setCheckState(checked)
                self._apply_capturing_row(row, point.bssid)
                self.item(row, 3).setText(self._display_ssid(point))
                self.item(row, 4).setText("未知" if point.power is None else str(point.power))
                self._refresh_client_cell(row, point)
                self.item(row, 6).setText(self._wps_text(point))
                self.item(row, 7).setText(str(point.channel or ""))
                self.item(row, 8).setText(point.encryption)

    def _refresh_client_cell(self, row, point):
        """刷新客户端数及三列高亮；无客户端时恢复默认前景。"""

        client_item = self.item(row, 5)
        client_item.setText(str(len(point.clients)))
        self._apply_row_highlight(
            (self.item(row, 2), self.item(row, 3), client_item), point
        )

    def _apply_row_highlight(self, items, point):
        """有客户端时把关键列置绿并附数量说明，否则恢复默认。

        QSS 写不出按行条件，只能在代码里按当前主题设前景色。
        """

        if point.clients:
            for item in items:
                if item is not None:
                    item.setForeground(self._highlight_green())
            client_item = items[-1]
            if client_item is not None:
                client_item.setToolTip(
                    f"有 {len(point.clients)} 个客户端在线，适合做定向握手。"
                )
        else:
            for item in items:
                if item is not None:
                    item.setData(Qt.ForegroundRole, None)
            client_item = items[-1]
            if client_item is not None:
                client_item.setToolTip("")

    def _rebuild_rows(self, points):
        """目标集合变化时全量重建；会按当前排序指示器重排。"""

        self._updating = True
        self.setSortingEnabled(False)
        self.setRowCount(0)
        self._points = tuple(points)
        for position, point in enumerate(self._points, start=1):
            row = self.rowCount()
            self.insertRow(row)
            number_item = _NumberItem(str(position))
            number_item.setData(Qt.UserRole, position)
            number_item.setTextAlignment(Qt.AlignCenter)
            status_item = QTableWidgetItem()
            status_item.setData(Qt.UserRole, point.bssid)
            status_item.setFlags(status_item.flags() | Qt.ItemIsUserCheckable)
            status_item.setCheckState(Qt.Checked if point.selected else Qt.Unchecked)
            status_item.setTextAlignment(Qt.AlignCenter)
            bssid_item = self._item(self._display_bssid(point))
            ssid_item = self._item(self._display_ssid(point))
            client_item = self._item(str(len(point.clients)), center=True)
            values = (
                number_item,
                status_item,
                bssid_item,
                ssid_item,
                self._item("未知" if point.power is None else str(point.power), center=True),
                client_item,
                self._item(self._wps_text(point), center=True),
                self._item(str(point.channel or ""), center=True),
                self._item(point.encryption),
            )
            for column, item in enumerate(values):
                self.setItem(row, column, item)
            self._refresh_status_cell(row, point)
            self._apply_row_highlight((bssid_item, ssid_item, client_item), point)
        self._refresh_capturing_rows()
        self.setSortingEnabled(True)
        self._renumber_rows()
        self._updating = False
        self._fit_height_to_content()

    def _fit_height_to_content(self, max_rows=15):
        """表格随分隔条分配伸展，不再限制为 15 行高度。"""

        self.setMaximumHeight(16777215)

    @staticmethod
    def _item(text, center=False):
        item = QTableWidgetItem(str(text))
        if center:
            item.setTextAlignment(Qt.AlignCenter)
        return item

    def _header_font(self):
        """返回放大三分之一的表头字体，整行共用。"""

        font = QFont(self.font())
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() * 4.0 / 3.0)
        return font

    @staticmethod
    def _status_text(state):
        return {
            AccessPointCaptureState.PENDING: "待处理",
            AccessPointCaptureState.PARTIAL: "没有可用包",
            AccessPointCaptureState.CAPTURED: "已有可用包",
            AccessPointCaptureState.FAILED: "失败",
            AccessPointCaptureState.NO_RESULT: "没有可用包",
            AccessPointCaptureState.NO_PROGRESS: "无进展",
        }.get(state, "待处理")

    @staticmethod
    def _status_tooltip(state):
        """解释是否已有可用包，内部仍区分零散数据和完全没有结果。"""

        return {
            AccessPointCaptureState.PENDING: "尚未对该目标抓包。",
            AccessPointCaptureState.PARTIAL: "没有可用包：虽收到零散数据，但尚未形成可用于跑包的握手组合。可延长等待时间或稍后重试。",
            AccessPointCaptureState.CAPTURED: "已保存可用于跑包的文件，但不代表已找到密码。通常无需重复抓取；需要重抓时可右键清除状态。",
            AccessPointCaptureState.FAILED: "抓取失败，原因见左侧信息窗。",
            AccessPointCaptureState.NO_RESULT: "本次没有抓到可用数据，可等待目标有设备连接时再试，不代表这个信号永远抓不到。",
            AccessPointCaptureState.NO_PROGRESS: "连续两轮没有新的可用内容，已结束等待；尚未抓包成功，需要时可稍后再试。",
        }.get(state, "")

    def _refresh_status_cell(self, row, point):
        """刷新状态文字、说明与已出可用包的高亮。

        已出可用包的状态格用主题绿显示，一眼可辨；其他状态恢复默认前景，
        避免旧高亮残留。QSS 写不出按行条件，只能在代码里按当前主题设前景色。
        """

        status_item = self.item(row, self.STATUS_COLUMN)
        if self._selection_blocked(point):
            status_item.setFlags((status_item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEnabled)
            status_item.setCheckState(Qt.Unchecked)
            status_item.setText("没有密码")
            status_item.setToolTip("开放网络，没有 Wi-Fi 密码，无需抓取密码握手包。")
            status_item.setData(Qt.ForegroundRole, None)
            return
        status_item.setFlags(status_item.flags() | Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        status_item.setText(self._status_text(point.capture_state))
        status_item.setToolTip(self._status_tooltip(point.capture_state))
        if point.capture_state == AccessPointCaptureState.CAPTURED and point.result_availability == "unknown":
            status_item.setText("结果待核实")
            status_item.setToolTip("以前抓到过可用包，现在暂时无法访问或确认文件位置，所以保留完成记录。请检查保存位置；需要重抓时右键清除状态。")
        elif point.result_availability == "missing":
            status_item.setToolTip("以前保存的可用包都已找不到，现可重新抓取。软件没有删除这些文件，请检查是否被移动或手动删除。")
        if point.bssid.upper() in self._capturing:
            status_item.setText("抓包中")
            status_item.setToolTip("正在采集此信号，后台自动检查是否已抓到可用包。")
        if point.capture_state == AccessPointCaptureState.CAPTURED and point.bssid.upper() not in self._capturing:
            status_item.setForeground(self._highlight_green())
        else:
            status_item.setData(Qt.ForegroundRole, None)

    @staticmethod
    def _wps_text(point):
        if not point.wps_supported:
            return "不支持"
        return "已锁定" if point.wps_locked else "支持"

    def _renumber_rows(self):
        """按当前显示顺序重写序号，永远 1..N；只改文本，不碰勾选。"""

        self._updating = True
        try:
            for row in range(self.rowCount()):
                item = self.item(row, self.NUMBER_COLUMN)
                if item is None:
                    continue
                if item.text() != str(row + 1):
                    item.setText(str(row + 1))
                    item.setData(Qt.UserRole, row + 1)
        finally:
            self._updating = False

    def sortByColumn(self, column, order):
        """序号/MAC/名称列不可排序：任何路径调用都直接吞掉，行顺序不动。"""

        if column in (self.NUMBER_COLUMN, self.BSSID_COLUMN, self.SSID_COLUMN):
            header = self.horizontalHeader()
            header.blockSignals(True)
            try:
                header.setSortIndicator(-1, Qt.AscendingOrder)
            finally:
                header.blockSignals(False)
            return
        super().sortByColumn(column, order)

    def _on_sort_indicator_changed(self, section, _order):
        """序号/MAC/名称列不可排序：点它们只清除指示器；其他列排序后重排序号。

        指示器信号先于视图实际搬行，重排号必须 deferred，否则排的是旧顺序。
        """

        if section in (self.NUMBER_COLUMN, self.BSSID_COLUMN, self.SSID_COLUMN):
            header = self.horizontalHeader()
            header.blockSignals(True)
            try:
                header.setSortIndicator(-1, Qt.AscendingOrder)
            finally:
                header.blockSignals(False)
            return
        QTimer.singleShot(0, self._renumber_rows)

    def selected_bssids(self):
        """返回当前勾选的 BSSID 元组，不读取服务层。"""

        selected = []
        for row in range(self.rowCount()):
            item = self.item(row, self.STATUS_COLUMN)
            if item is not None and item.flags() & Qt.ItemIsEnabled and item.checkState() == Qt.Checked:
                selected.append(str(item.data(Qt.UserRole)))
        return tuple(selected)

    def _on_item_changed(self, item):
        if self._updating or item.column() != self.STATUS_COLUMN:
            return
        point = next((entry for entry in self._points if entry.bssid == item.data(Qt.UserRole)), None)
        if point is not None and item.checkState() == Qt.Checked:
            if self._selection_blocked(point):
                self._updating = True
                item.setCheckState(Qt.Unchecked)
                self._updating = False
        if not self._checkbox_paint.active:
            self.selectionChangedByUser.emit(self.selected_bssids())

    def _open_context_menu(self, pos):
        if not self._points:
            return
        menu = create_themed_menu(self, detect_light_theme(self))
        actions = (
            ("全选", self.select_all),
            ("反选", self.invert_selection),
            ("勾选所有可 Pin 的信号", self._select_wps),
            ("勾选所有有客户端的信号", self._select_clients),
            ("取消所有选择", self.deselect_all),
        )
        for text, callback in actions:
            action = QAction(text, menu)
            action.triggered.connect(callback)
            menu.addAction(action)
        item = self.item(self.rowAt(pos.y()), self.STATUS_COLUMN)
        point = next(
            (point for point in self._points
             if item is not None and point.bssid == item.data(Qt.UserRole)),
            None,
        )
        if point is not None and point.capture_state == AccessPointCaptureState.CAPTURED:
            menu.addSeparator()
            action = menu.addAction("清除状态")
            action.setToolTip("将此目标恢复为待处理，清除对应记忆，不删除任何文件。")
            action.triggered.connect(
                lambda _checked=False, bssid=point.bssid: self.clearStatusRequested.emit(bssid)
            )
        try:
            menu.exec(self.viewport().mapToGlobal(pos))
        finally:
            menu.deleteLater()

    def select_all(self):
        """勾选全部可选目标，开放无密码网络除外。"""

        self._select_matching(lambda point: bool(point.ssid.strip()) and point.ssid != "__hidden__")

    def deselect_all(self):
        """取消全部目标选择。"""

        self._select_matching(lambda _point: False)

    def invert_selection(self):
        """反选：已勾选变不勾选、未勾选变勾选，已知名称的开放网络不勾选。"""

        with self._stable_update(True):
            for row in range(self.rowCount()):
                item = self.item(row, self.STATUS_COLUMN)
                if item is None:
                    continue
                bssid = str(item.data(Qt.UserRole))
                point = next((entry for entry in self._points if entry.bssid == bssid), None)
                selectable = bool(point and not self._selection_blocked(point))
                if not selectable:
                    item.setCheckState(Qt.Unchecked)
                else:
                    item.setCheckState(
                        Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
                    )
        self.selectionChangedByUser.emit(self.selected_bssids())

    def _select_wps(self):
        self._select_matching(
            lambda point: bool(point.ssid) and point.ssid != "__hidden__"
            and point.wps_supported and not point.wps_locked
        )

    def _select_clients(self):
        """勾选有客户端的信号，但跳过已抓出可用包的，避免重复抓取。"""

        self._select_matching(
            lambda point: bool(point.clients)
            and point.capture_state != AccessPointCaptureState.CAPTURED
        )

    def _select_matching(self, predicate):
        with self._stable_update(True):
            for row in range(self.rowCount()):
                item = self.item(row, self.STATUS_COLUMN)
                if item is None:
                    continue
                bssid = str(item.data(Qt.UserRole))
                point = next((entry for entry in self._points if entry.bssid == bssid), None)
                selectable = bool(point and not self._selection_blocked(point))
                item.setCheckState(Qt.Checked if selectable and predicate(point) else Qt.Unchecked)
        self.selectionChangedByUser.emit(self.selected_bssids())
