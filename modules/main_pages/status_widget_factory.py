"""下载状态列 UI 组件工厂。

主要作用：从 SoftwareDownloadPage 提取的状态列控件创建函数。
负责根据下载状态（STATUS_*）生成对应的进度条、标签、按钮组合。

所有函数不依赖类实例，通过显式参数传递回调处理器。
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QProgressBar,
    QToolButton,
)

from modules.common_widgets.sweep_button import SweepButton
from modules.icon_utils import get_theme_icon
from modules.main_pages.download.download_page_utils import (
    current_download_mode,
    is_download_mode_allowed,
    network_channels,
)
from modules.network_policy import configured_proxy_channels
from modules.main_pages.download.download_status import (
    STATUS_ARCH_MISMATCH,
    STATUS_CHECKING,
    STATUS_CHECK_FAILED,
    STATUS_DIRECT_FAILED,
    STATUS_DONE,
    STATUS_DOWNLOADING,
    STATUS_DOWNLOAD_FAILED,
    STATUS_EXISTING_FILE,
    STATUS_MANUAL_DECISION,
    STATUS_NOT_CHECKED,
    STATUS_NO_UPDATE,
    STATUS_PAUSED,
    STATUS_PROXY_SKIPPED,
    STATUS_READY_DOWNLOAD,
    STATUS_WAITING,
    STATUS_TEXT,
)


def mini_button_scale(scale_manager):
    """返回小按钮的固定尺寸，基于缩放管理器计算。"""
    if scale_manager is not None:
        return scale_manager.px(28)
    return 28


def format_speed(bytes_per_second):
    """把 aria2 返回的字节/秒转换成适合状态栏显示的速度文本。

    参数：
        bytes_per_second: aria2 的 downloadSpeed，单位为字节/秒
    返回值：
        形如 "0 B/s"、"128 KB/s"、"1.2 MB/s" 的字符串，无副作用。
    """
    try:
        speed = float(bytes_per_second or 0)
    except (TypeError, ValueError):
        speed = 0.0
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    unit_index = 0
    while speed >= 1024 and unit_index < len(units) - 1:
        speed /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{speed:.0f} {units[unit_index]}"
    return f"{speed:.1f} {units[unit_index]}"


def status_label(text, status, parent, mini_size):
    """创建支持自动换行的状态显示标签，带有主题属性标记。

    状态列空间会被操作按钮分走，较长状态文字不能依赖单行固定高度，
    否则窄列时会被 QLabel 直接裁切。
    """
    label = QLabel(text, parent)
    label.setObjectName("downloadStatusLabel")
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    label.setProperty("downloadStatus", status)
    label.setMinimumHeight(mini_size)
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    return label


def small_status_button(
    icon_path,
    tooltip,
    parent,
    light_theme,
    scale_manager,
    handler=None,
    download_status=None,
):
    """创建状态列小图标按钮，并可继承当前状态的主题颜色。"""
    button = QToolButton(parent)
    button.setObjectName("downloadMiniButton")
    button.setCursor(Qt.PointingHandCursor)
    button.setIcon(get_theme_icon(icon_path, light_theme))
    sz = mini_button_scale(scale_manager)
    button.setFixedSize(sz, sz)
    button.setIconSize(QSize(sz // 2, sz // 2))
    button.setToolTip(tooltip)
    button.setProperty("iconPath", icon_path)
    if download_status:
        # 同一状态中的文字按钮和图标按钮使用相同属性，颜色由深浅主题 QSS 统一决定。
        button.setProperty("downloadStatus", download_status)
    if handler is not None:
        button.clicked.connect(handler)
    return button


def build_status_widget(row_data, table, light_theme, scale_manager, config_manager, callbacks):
    """根据下载状态创建状态列容器控件。

    参数：
        row_data: 当前行的数据字典
        table: 父级 QTableWidget
        light_theme: 是否亮色主题
        scale_manager: 缩放管理器
        config_manager: ConfigManager 实例
        callbacks: 字典，包含以下可选键：
            - pause_single(sid)
            - cancel_single(sid)
            - resume_single(sid)
            - open_software_dir(rd)
            - retry_with_proxy(sid)
            - download_single(sid)
            - arch_mismatch_switch(rd, arch)
            - arch_mismatch_keep(rd)
            - check_single(sid)
            - check_with_proxy(sid)
            - cancel_check(sid)
            - start_auto_download(row_data, button)
            - start_auto_arch_switch(row_data, button, architecture)
            - cancel_auto_action(sid)
    返回值：
        包含状态控件的 QFrame
    """
    status = row_data.get("status", STATUS_NOT_CHECKED)
    software_id = row_data.get("id", "")
    container = QFrame(table)
    container.setObjectName("downloadStatusCell")
    container.setMinimumWidth(0)
    container.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)
    mini_sz = mini_button_scale(scale_manager)

    if status == STATUS_DOWNLOADING:
        progress = QProgressBar(container)
        progress.setObjectName("downloadProgressBar")
        progress.setRange(0, 10000)
        progress_percent = float(row_data.get("progress_percent", row_data.get("progress", 0)))
        progress.setValue(int(progress_percent * 100))
        speed_text = format_speed(row_data.get("download_speed", 0))
        progress.setFormat(f"{progress_percent:.2f} % · {speed_text}")
        layout.addWidget(progress, 1)
        layout.addWidget(small_status_button(
            ":/icons/images/icons/cil-media-pause.png", "暂停下载",
            container, light_theme, scale_manager,
            lambda checked=False, sid=software_id: callbacks.get("pause_single", lambda x: None)(sid),
        ))
        layout.addWidget(small_status_button(
            ":/icons/images/icons/cil-x.png", "取消下载",
            container, light_theme, scale_manager,
            lambda checked=False, sid=software_id: callbacks.get("cancel_single", lambda x: None)(sid),
        ))
        return container

    if status == STATUS_PAUSED:
        layout.addWidget(status_label("已暂停", status, container, mini_sz), 1)
        layout.addWidget(small_status_button(
            ":/icons/images/icons/cil-media-play.png", "继续下载",
            container, light_theme, scale_manager,
            lambda checked=False, sid=software_id: callbacks.get("resume_single", lambda x: None)(sid),
        ))
        layout.addWidget(small_status_button(
            ":/icons/images/icons/cil-x.png", "取消下载",
            container, light_theme, scale_manager,
            lambda checked=False, sid=software_id: callbacks.get("cancel_single", lambda x: None)(sid),
        ))
        return container

    if status == STATUS_WAITING:
        layout.addWidget(status_label("准备下载", status, container, mini_sz), 1)
        layout.addWidget(small_status_button(
            ":/icons/images/icons/cil-x.png", "取消下载",
            container, light_theme, scale_manager,
            lambda checked=False, sid=software_id: callbacks.get("cancel_single", lambda x: None)(sid),
        ))
        return container

    if status == STATUS_EXISTING_FILE:
        layout.addWidget(status_label("已存在文件", status, container, mini_sz))
        open_dir_btn = SweepButton("打开下载目录", table)
        open_dir_btn.setObjectName("downloadStatusActionButton")
        open_dir_btn.setCursor(Qt.PointingHandCursor)
        open_dir_btn.setProperty("downloadStatus", status)
        open_dir_btn.set_light_theme(light_theme)
        open_dir_btn.set_hover_sweep(False)
        open_dir_btn.setToolTip("检测到目标目录里已经有下载好的文件，本次跳过重复下载。")
        open_dir_btn.clicked.connect(
            lambda checked=False, rd=row_data: callbacks.get("open_software_dir", lambda x: None)(rd)
        )
        layout.addWidget(open_dir_btn)
        return container

    if status == STATUS_DIRECT_FAILED:
        label = status_label("直连失败", status, container, mini_sz)
        label.setToolTip("直连下载没有成功。可直接重试，或临时切换为代理后重新下载。")
        layout.addWidget(label)

        retry_btn = SweepButton("重试", table)
        retry_btn.setObjectName("downloadStatusActionButton")
        retry_btn.setCursor(Qt.PointingHandCursor)
        retry_btn.setProperty("downloadStatus", status)
        retry_btn.setToolTip("继续使用直连方式重新提交这个下载任务。")
        retry_btn.set_light_theme(light_theme)
        retry_btn.set_hover_sweep(False)
        retry_btn.clicked.connect(
            lambda checked=False, sid=software_id: callbacks.get("download_single", lambda x: None)(sid)
        )
        layout.addWidget(retry_btn)

        if not is_download_mode_allowed(row_data, "proxy"):
            return container

        proxy_retry_btn = SweepButton("切换代理", table)
        proxy_retry_btn.setObjectName("downloadStatusActionButton")
        proxy_retry_btn.setCursor(Qt.PointingHandCursor)
        proxy_retry_btn.setProperty("downloadStatus", status)
        proxy_retry_btn.setToolTip("临时切换为代理模式，并重新提交这个下载任务。")
        proxy_retry_btn.set_light_theme(light_theme)
        proxy_retry_btn.set_hover_sweep(False)
        proxy_retry_btn.setEnabled(bool(configured_proxy_channels(config_manager)))
        if not proxy_retry_btn.isEnabled():
            proxy_retry_btn.setToolTip("当前没有可用代理。请先在代理设置中启用 WinBox 代理或系统代理。")
        proxy_retry_btn.clicked.connect(
            lambda checked=False, sid=software_id: callbacks.get("retry_with_proxy", lambda x: None)(sid)
        )
        layout.addWidget(proxy_retry_btn)
        return container

    if status == STATUS_CHECK_FAILED and current_download_mode(row_data) == "direct":
        label = status_label("直连检测失败", status, container, mini_sz)
        label.setToolTip("直连检测没有成功。可直接重试，或临时切换为代理后重新检测。")
        layout.addWidget(label)

        retry_btn = SweepButton("重试", table)
        retry_btn.setObjectName("downloadStatusActionButton")
        retry_btn.setCursor(Qt.PointingHandCursor)
        retry_btn.setProperty("downloadStatus", status)
        retry_btn.setToolTip("继续使用直连方式重新检测。")
        retry_btn.set_light_theme(light_theme)
        retry_btn.set_hover_sweep(False)
        retry_btn.clicked.connect(
            lambda checked=False, sid=software_id: callbacks.get("check_single", lambda x: None)(sid)
        )
        layout.addWidget(retry_btn)

        if not is_download_mode_allowed(row_data, "proxy"):
            return container

        proxy_retry_btn = SweepButton("切换代理", table)
        proxy_retry_btn.setObjectName("downloadStatusActionButton")
        proxy_retry_btn.setCursor(Qt.PointingHandCursor)
        proxy_retry_btn.setProperty("downloadStatus", status)
        proxy_retry_btn.setToolTip("临时切换为代理模式，并重新检测更新。")
        proxy_retry_btn.set_light_theme(light_theme)
        proxy_retry_btn.set_hover_sweep(False)
        proxy_retry_btn.setEnabled(bool(configured_proxy_channels(config_manager)))
        if not proxy_retry_btn.isEnabled():
            proxy_retry_btn.setToolTip("当前没有可用代理。请先在代理设置中启用 WinBox 代理或系统代理。")
        proxy_retry_btn.clicked.connect(
            lambda checked=False, sid=software_id: callbacks.get("check_with_proxy", lambda x: None)(sid)
        )
        layout.addWidget(proxy_retry_btn)
        return container

    if status == STATUS_DOWNLOAD_FAILED:
        failure = row_data.get("_multi_download_failure")
        if isinstance(failure, dict):
            successful = int(failure.get("successful", 0) or 0)
            total = int(failure.get("total", 0) or 0)
            failed_names = [str(item) for item in failure.get("failed", []) if str(item)]
            partial = successful > 0 and total > successful
            label_text = "部分文件下载失败" if partial else "全部文件下载失败"
            tooltip = (
                f"共 {total} 个文件，已完成或已存在 {successful} 个，失败 {len(failed_names)} 个。"
                f"失败文件：{'、'.join(failed_names) or '未命名文件'}。"
                "点击重试只会下载缺失或失败文件。"
            )
            retry_text = "重试未完成文件"
        else:
            label_text = "下载失败"
            tooltip = "下载过程中发生了错误。可以点击重新下载按钮再次尝试。"
            retry_text = "重试"
        label = status_label(label_text, status, container, mini_sz)
        label.setToolTip(tooltip)
        layout.addWidget(label)

        retry_btn = SweepButton(retry_text, table)
        retry_btn.setObjectName("downloadStatusActionButton")
        retry_btn.setCursor(Qt.PointingHandCursor)
        retry_btn.setProperty("downloadStatus", status)
        retry_btn.set_light_theme(light_theme)
        retry_btn.set_hover_sweep(False)
        retry_btn.setToolTip(tooltip)
        retry_btn.clicked.connect(
            lambda checked=False, sid=software_id: callbacks.get("download_single", lambda x: None)(sid)
        )
        layout.addWidget(retry_btn)
        layout.addWidget(small_status_button(
            ":/icons/images/icons/cil-x.png", "取消下载",
            container, light_theme, scale_manager,
            lambda checked=False, sid=software_id: callbacks.get("cancel_single", lambda x: None)(sid),
        ))
        return container

    if status == STATUS_ARCH_MISMATCH:
        detected_arch = row_data.get("_detected_local_arch", "")
        current_arch = row_data.get("current_arch", "")

        label = status_label(
            f"本地\n{detected_arch}",
            "manual_decision",
            container,
            mini_sz,
        )
        # 架构不匹配状态需要并排放置三个操作。固定拆成“本地 / x86”两行，
        # 让标签宽度只按较长的一行计算，把横向空间留给切换倒计时按钮。
        label.setWordWrap(False)
        label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        label.setProperty("compactStatus", True)
        label.setToolTip(f"检测到本地安装的软件是 {detected_arch} 版本。")
        layout.addWidget(label)

        auto_suppressed = bool(row_data.get("_auto_action_suppressed"))
        switch_btn = SweepButton(
            f"切换{detected_arch}" if auto_suppressed else f"切换{detected_arch} 10s",
            table,
        )
        switch_btn.setObjectName("downloadStatusActionButton")
        switch_btn.setCursor(Qt.PointingHandCursor)
        switch_btn.setProperty("downloadStatus", "manual_decision")
        switch_btn.setProperty("compactStatus", True)
        switch_btn.set_light_theme(light_theme)
        switch_btn.set_hover_sweep(False)
        switch_btn.setToolTip(
            f"点击后立即切换到本地 {detected_arch} 架构。"
            if auto_suppressed
            else f"10 秒后自动切换到本地 {detected_arch} 架构；点击可立即切换。"
        )
        switch_btn.clicked.connect(
            lambda checked=False, rd=row_data, arch=detected_arch:
                callbacks.get("arch_mismatch_switch", lambda x, y: None)(rd, arch)
        )
        layout.addWidget(switch_btn)

        keep_btn = SweepButton(f"保持{current_arch}", table)
        keep_btn.setObjectName("downloadStatusActionButton")
        keep_btn.setCursor(Qt.PointingHandCursor)
        keep_btn.setProperty("downloadStatus", "manual_decision")
        keep_btn.setProperty("compactStatus", True)
        keep_btn.set_light_theme(light_theme)
        keep_btn.set_hover_sweep(False)
        keep_btn.setToolTip(f"停止自动切换，继续使用当前选择的 {current_arch} 架构。")
        keep_btn.clicked.connect(
            lambda checked=False, rd=row_data:
                callbacks.get("arch_mismatch_keep", lambda x: None)(rd)
        )
        layout.addWidget(keep_btn)

        # 复用暂停/下载状态已有的方形 X 按钮，减少三个文字操作并排时的宽度压力。
        layout.addWidget(small_status_button(
            ":/icons/images/icons/cil-x.png",
            "取消本次操作并回到检测更新状态",
            container,
            light_theme,
            scale_manager,
            lambda checked=False, sid=software_id:
                callbacks.get("cancel_auto_action", lambda x: None)(sid),
            download_status="manual_decision",
        ))

        if not auto_suppressed:
            callbacks.get("start_auto_arch_switch", lambda rd, btn, arch: None)(
                row_data,
                switch_btn,
                detected_arch,
            )
        return container

    # 未检测和检测失败状态使用可点击按钮
    if status in (STATUS_READY_DOWNLOAD, STATUS_MANUAL_DECISION):
        enabled = bool(network_channels(row_data, config_manager))
        auto_suppressed = bool(row_data.get("_auto_action_suppressed")) or not bool(
            callbacks.get("auto_download_enabled", True)
        )
        download_btn = SweepButton(
            "下载" if auto_suppressed or not enabled else "下载10s",
            table,
        )
        download_btn.setObjectName("downloadStatusActionButton")
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.setProperty("downloadStatus", status)
        download_btn.set_light_theme(light_theme)
        download_btn.set_hover_sweep(False)
        download_btn.setEnabled(enabled)
        if not enabled:
            download_btn.setToolTip("当前代理开关关闭，代理模式软件不能下载。请打开代理开关，或临时切换为直连。")
        else:
            download_btn.setToolTip(
                "点击后立即下载。" if auto_suppressed else "10 秒后自动下载；点击可立即下载。"
            )
        download_btn.clicked.connect(
            lambda checked=False, sid=software_id: callbacks.get("download_single", lambda x: None)(sid)
        )
        layout.addWidget(download_btn)

        if enabled and not auto_suppressed:
            layout.addWidget(small_status_button(
                ":/icons/images/icons/cil-x.png",
                "取消本次自动下载并回到检测更新状态",
                container,
                light_theme,
                scale_manager,
                lambda checked=False, sid=software_id:
                    callbacks.get("cancel_auto_action", lambda x: None)(sid),
                download_status=status,
            ))
        if enabled and not auto_suppressed:
            callbacks.get("start_auto_download", lambda rd, btn: None)(row_data, download_btn)
        return container

    if status == STATUS_DONE:
        open_dir_btn = SweepButton("打开下载目录", table)
        open_dir_btn.setObjectName("downloadStatusActionButton")
        open_dir_btn.setCursor(Qt.PointingHandCursor)
        open_dir_btn.setProperty("downloadStatus", status)
        open_dir_btn.set_light_theme(light_theme)
        open_dir_btn.set_hover_sweep(False)
        open_dir_btn.clicked.connect(
            lambda checked=False, rd=row_data: callbacks.get("open_software_dir", lambda x: None)(rd)
        )
        layout.addWidget(open_dir_btn)
        return container

    if status == STATUS_NOT_CHECKED and row_data.get("completed_packages"):
        package_count = len(row_data["completed_packages"])
        completed_btn = SweepButton("存在已完成文件", table)
        # 这是可点击的打开目录操作，必须使用按钮样式，不能伪装成普通状态文字。
        completed_btn.setObjectName("downloadStatusActionButton")
        completed_btn.setCursor(Qt.PointingHandCursor)
        completed_btn.setProperty("downloadStatus", "completed_package")
        completed_btn.set_light_theme(light_theme)
        completed_btn.set_hover_sweep(False)
        completed_btn.setToolTip(
            f"检测到 {package_count} 个已完成的下载文件。点击打开该软件的下载目录。"
        )
        completed_btn.clicked.connect(
            lambda checked=False, rd=row_data: callbacks.get("open_software_dir", lambda x: None)(rd)
        )
        layout.addWidget(completed_btn, 1)

        check_btn = SweepButton("检测更新", table)
        check_btn.setObjectName("downloadStatusActionButton")
        check_btn.setCursor(Qt.PointingHandCursor)
        check_btn.setProperty("downloadStatus", status)
        check_btn.setProperty("softwareId", software_id)
        check_btn.set_light_theme(light_theme)
        check_btn.set_hover_sweep(False)
        if not network_channels(row_data, config_manager):
            check_btn.setEnabled(False)
            check_btn.setToolTip("当前代理开关关闭，代理模式软件不能检测。请打开代理开关，或临时切换为直连。")
        check_btn.clicked.connect(
            lambda checked=False, sid=software_id: callbacks.get("check_single", lambda x: None)(sid)
        )
        layout.addWidget(check_btn)
        return container

    if status == STATUS_NO_UPDATE:
        layout.addWidget(status_label("无需更新", status, container, mini_sz), 1)
        return container

    btn = SweepButton(STATUS_TEXT.get(status, "未知状态"), table)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setProperty("downloadStatus", status)
    btn.setProperty("softwareId", software_id)
    btn.set_light_theme(light_theme)
    if status == STATUS_CHECKING:
        btn.setObjectName("downloadStatusLabel")
        btn.set_guided_mode(True)
    else:
        btn.setObjectName("downloadStatusActionButton")
        btn.set_hover_sweep(False)
        if not network_channels(row_data, config_manager):
            btn.setEnabled(False)
            btn.setToolTip("当前代理开关关闭，代理模式软件不能检测。请打开代理开关，或临时切换为直连。")
    btn.clicked.connect(lambda checked=False, sid=software_id: callbacks.get("check_single", lambda x: None)(sid))
    layout.addWidget(btn, 1)
    if status == STATUS_CHECKING:
        # 检测进行中允许取消：与暂停状态使用同款小 X 图标按钮。
        layout.addWidget(small_status_button(
            ":/icons/images/icons/cil-x.png", "取消检测",
            container, light_theme, scale_manager,
            lambda checked=False, sid=software_id: callbacks.get("cancel_check", lambda x: None)(sid),
        ))
    return container
