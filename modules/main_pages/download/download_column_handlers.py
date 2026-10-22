"""下载页列控件处理器

主要作用：在第 4 列（架构）和第 5 列（下载方式）创建并管理下拉框/标签控件，
检测到用户切换架构或下载方式时自动处理状态变更和下载重启。

使用方式：页面创建 DownloadColumnHandler(page) 实例。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QSizePolicy

from modules.config_services.software_items_manager import SoftwareItemsConfigManager
from modules.common_widgets.centered_combo import CenteredComboBox, ComboAlignedLabel
from modules.main_pages.download.download_page_utils import (
    configured_download_modes,
    current_download_mode,
    network_channels,
)
from modules.network_policy import blocked_proxy_message
from modules.platform_services.system_architecture import preferred_download_architecture
from .download_status import (
    STATUS_DOWNLOADING,
    STATUS_MANUAL_DECISION,
    STATUS_NOT_CHECKED,
    STATUS_NO_UPDATE,
    STATUS_PAUSED,
    STATUS_PROXY_SKIPPED,
    STATUS_READY_DOWNLOAD,
    STATUS_WAITING,
)


class DownloadColumnHandler:
    """下载页列控件处理器

    负责：在表格的架构列和下载方式列安装控件，并处理用户切换时的逻辑。
    不负责：控件样式（由 QSS 统一控制）。

    输入参数：
        page: SoftwareDownloadPage 实例。
    """

    def __init__(self, page):
        self._page = page

    # ── 架构列 ──────────────────────────────────────────

    def install_arch_column(self, row, row_data):
        """在第 4 列放置架构显示控件。"""
        page = self._page
        node = row_data["node"]
        arch_config = SoftwareItemsConfigManager.normalize_architectures(
            node.get("architectures"),
        )
        x64 = arch_config.get("x64", True)
        x86 = arch_config.get("x86", False)

        if x64 and x86:
            combo = CenteredComboBox(page.table)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            combo.setObjectName("downloadModeCombo")
            combo.addItems(["x64", "x86"])
            # 默认位数跟随当前系统，不再读取软件配置中的机器相关默认值。
            default = preferred_download_architecture(arch_config)
            row_data["current_arch"] = default
            combo.blockSignals(True)
            combo.setCurrentIndex(0 if default == "x64" else 1)
            combo.blockSignals(False)
            combo.currentTextChanged.connect(
                lambda arch, rd=row_data: self._on_arch_changed(rd, arch)
            )
            page.table.setCellWidget(row, 4, combo)
        else:
            arch_text = "x64" if x64 else "x86"
            label = self._create_combo_aligned_label(arch_text)
            page.table.setCellWidget(row, 4, label)
            row_data["current_arch"] = arch_text

    def on_arch_mismatch_switch(self, row_data, arch):
        """用户点击"切换 x86/x64"时，切换架构并重新检测。"""
        page = self._page
        current_arch = row_data.get("current_arch", "")
        if current_arch == arch:
            return
        row_data["current_arch"] = arch
        row = page._row_for_data(row_data)
        if row is not None:
            widget = page.table.cellWidget(row, 4)
            if isinstance(widget, QComboBox):
                widget.blockSignals(True)
                widget.setCurrentIndex(0 if arch == "x64" else 1)
                widget.blockSignals(False)
        software_id = row_data.get("id", "")
        QTimer.singleShot(0, lambda: page.check_single(software_id))

    def on_arch_mismatch_keep(self, row_data):
        """用户点击"保持 x64/x86"时，恢复检测结果状态。"""
        page = self._page
        row_data["_arch_auto_switched"] = True
        software_id = row_data.get("id", "")
        result = page._check_results.get(software_id)
        if result and result.get("success", False):
            has_update = result.get("has_update", False)
            if result.get("latest_version") and str(result.get("current_version", "")).strip() in (
                "--",
                "无法使用exe读取",
                "未检测到本地安装的exe文件",
            ):
                page._set_status(row_data, STATUS_MANUAL_DECISION)
            elif has_update:
                page._set_status(row_data, STATUS_READY_DOWNLOAD)
            else:
                page._set_status(row_data, STATUS_NO_UPDATE)
        else:
            page._set_status(row_data, STATUS_NO_UPDATE)

    def _on_arch_changed(self, row_data, arch):
        """架构下拉框切换时更新当前架构并自动重新检测。"""
        page = self._page
        page._auto_action_coordinator.cancel(row_data.get("id", ""))
        row_data["current_arch"] = arch
        row_data.pop("_arch_auto_switched", None)
        software_id = row_data.get("id", "")
        if software_id and software_id in page._check_results:
            current_status = row_data.get("status")
            if current_status in (STATUS_WAITING, STATUS_DOWNLOADING, STATUS_PAUSED):
                page._append_info(
                    f"{row_data['node'].get('name', '')}：架构已切换为 {arch}，"
                    f"下载完成后重新检测将使用新架构。",
                    level="info",
                )
                return
            page._append_info(
                f"{row_data['node'].get('name', '')}：架构已切换为 {arch}，重新获取下载链接...",
                level="info",
            )
            if page.version_checker:
                page.version_checker.invalidate_cache(software_id)
            page.check_single(software_id)

    # ── 下载方式列 ──────────────────────────────────────

    def install_download_mode_combo(self, row, row_data):
        """按软件允许方式安装固定标签或临时切换下拉框。"""
        page = self._page
        modes = configured_download_modes(row_data)
        allowed_modes = [mode for mode in ("direct", "proxy") if modes.get(mode, False)]
        if len(allowed_modes) == 1:
            mode = allowed_modes[0]
            label = self._create_combo_aligned_label("代理" if mode == "proxy" else "直连")
            label.setToolTip("该软件只允许使用当前显示的下载方式，不能在下载页面切换。")
            page.table.setCellWidget(row, 5, label)
            return

        combo = CenteredComboBox(page.table)
        combo.setMinimumWidth(0)
        combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        combo.setObjectName("downloadModeCombo")
        for mode in allowed_modes:
            combo.addItem("代理" if mode == "proxy" else "直连", mode)
            combo.setItemData(combo.count() - 1, Qt.AlignCenter, Qt.TextAlignmentRole)
        current_index = combo.findData(current_download_mode(row_data))
        combo.setCurrentIndex(current_index if current_index >= 0 else 0)
        combo.currentIndexChanged.connect(
            lambda _index, rd=row_data, c=combo: self._on_download_mode_changed(rd, c.currentData())
        )
        page.table.setCellWidget(row, 5, combo)

    def _on_download_mode_changed(self, row_data, mode):
        """下拉框切换时确认、更新临时方式并重启下载中任务。"""
        page = self._page
        new_mode = "proxy" if mode == "proxy" else "direct"
        old_mode = current_download_mode(row_data)
        if new_mode == old_mode:
            return
        if not page.confirm_download_mode_switch(row_data, new_mode):
            self._sync_download_mode_combo(row_data, old_mode)
            return
        row_data["temp_download_mode"] = new_mode
        row_data.pop("network_channel_index", None)
        row_data.pop("active_network_channel", None)
        software_id = row_data.get("id")
        if not software_id:
            return
        current_status = row_data.get("status")
        if current_status not in (STATUS_DOWNLOADING, STATUS_WAITING, STATUS_PAUSED):
            if current_status == STATUS_PROXY_SKIPPED and network_channels(row_data, page.config_manager):
                current_status = STATUS_NOT_CHECKED
            page._set_status(row_data, current_status or STATUS_NOT_CHECKED)
            return
        if not network_channels(row_data, page.config_manager):
            page._append_info(
                blocked_proxy_message(row_data["node"].get("name", "这个软件")),
                level="warning",
            )
            page._set_status(row_data, STATUS_NOT_CHECKED)
            return
        old_task = page._download_tasks.pop(software_id, None)
        if old_task and page.download_service:
            page.download_service.cancel(old_task.gid)
        page._append_info(
            f"{row_data['node'].get('name', '')}：下载方式已切换为{'代理' if new_mode == 'proxy' else '直连'}，重新开始下载...",
            level="info",
        )
        page._do_download(software_id, row_data)

    def set_temporary_download_mode(self, row_data, mode):
        """临时切换行内下载方式，并同步下拉框显示。

        参数：row_data 是软件下载行数据；mode 只能为 direct 或 proxy。
        返回值：无。
        副作用：只修改当前页面的临时下载方式和网络通道缓存，不写入
        software_items.json，也不会自动取消或重新提交已有任务。供状态栏中的
        “切换代理”使用，调用方负责随后启动检测或下载。
        """

        page = self._page
        normalized_mode = "proxy" if mode == "proxy" else "direct"
        row_data["temp_download_mode"] = normalized_mode
        row_data.pop("network_channel_index", None)
        row_data.pop("active_network_channel", None)
        row = page._row_for_data(row_data)
        if row is None:
            return
        combo = page.table.cellWidget(row, 5)
        if isinstance(combo, QComboBox):
            self._sync_download_mode_combo(row_data, normalized_mode)

    def _sync_download_mode_combo(self, row_data, mode):
        """在不触发切换逻辑的前提下同步下载方式下拉框。"""

        page = self._page
        row = page._row_for_data(row_data)
        if row is None:
            return
        combo = page.table.cellWidget(row, 5)
        if not isinstance(combo, QComboBox):
            return
        index = combo.findData("proxy" if mode == "proxy" else "direct")
        if index < 0:
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _create_combo_aligned_label(self, text):
        """创建与可切换下拉框文字位置一致的单项显示控件。"""

        page = self._page
        label = ComboAlignedLabel(text, page.table)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        label.setObjectName("downloadStatusLabel")
        return label

    def download_mode_text(self, mode):
        return {
            "global": "跟随全局",
            "direct": "直连",
            "proxy": "代理",
        }.get(str(mode or "global"), "跟随全局")
