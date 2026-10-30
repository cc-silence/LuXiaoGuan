"""OpenWrt 抓包设置页。"""

from modules.common_widgets.spin_box import CommonSpinBox as QSpinBox
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from modules.main_pages.page_action_buttons import create_page_icon_button
from modules.common_widgets.combo_box import CommonComboBox
from modules.main_pages.packet_capture.ui_helpers import create_capture_card, create_form_row, create_path_row
from .resource_picker import OpenWrtResourcePicker
from .password_dictionary_editor import PasswordDictionaryEditor
from modules.capture_services.openwrt.password_verification import dictionary_paths




class CaptureSettingsPage(QWidget):
    """纯抓包策略与字典设置页；连接、设备与依赖已搬到路由器登录页。

    参数：service 是业务服务；settings 是标准化 OpenWrt 配置。页面只收集设置，
    不执行 SSH、opkg、UCI 或无线命令。本页不再需要信息窗口。
    """

    settingsChanged = Signal()
    actionRequested = Signal(str)

    def __init__(self, service, settings, parent=None):
        super().__init__(parent)
        self.service = service
        self._dictionary_job = None
        self._disposed = False
        self.resource_picker = OpenWrtResourcePicker(self)
        self.setObjectName("openwrtCaptureSettingsPage")
        self.setFocusPolicy(Qt.StrongFocus)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        from modules.common_widgets.bounce_scroll_area import BounceScrollArea
        self.scroll_area = BounceScrollArea(self)
        self.scroll_area.setObjectName("captureScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(BounceScrollArea.NoFrame)
        content = QWidget(self.scroll_area)
        content.setObjectName("openwrtCaptureSettingsContent")
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area, 3)

        self._build_common_options(content, root, settings)
        self._build_handshake_options(content, root, settings)
        self._build_password_options(content, root, settings)
        root.addStretch(1)

        service.openwrtSnapshotChanged.connect(self._apply_snapshot)
        self._apply_snapshot(service.openwrt_snapshot)

    def _build_common_options(self, parent, root, settings):
        """创建通用设置：扫描、常规抓包、WPS 都用；猜密码不受影响。"""

        card, layout = create_capture_card(parent, "通用设置", "扫描、常规抓包和 WPS 使用这些设置。猜密码使用下方的独立设置。")
        self.output_dir = QLineEdit(card)
        self.output_dir.setObjectName("captureLineEdit")
        self.output_dir_button = create_page_icon_button(
            card,
            ":/icons/images/icons/cil-folder-open.png",
            "选择 Windows 本地抓包结果目录。",
        )
        self.refresh_seconds = QSpinBox(card)
        self.refresh_seconds.setObjectName("captureSpinBox")
        self.refresh_seconds.setRange(1, 60)
        self.refresh_seconds.setSuffix(" 秒")
        self.keep_raw = QCheckBox("保留原始 PCAPNG", card)
        self.keep_raw.setObjectName("captureCheckBox")
        self.keep_raw.setToolTip(
            "建议勾选，方便以后重新转换或检查原始包。关闭后，常规抓包成功转换的原包和无法识别的包不会保留，可能丢失后续可用的数据；转换失败时仍保留原包。"
        )
        self.generate_22000 = QCheckBox("生成 Hashcat 22000", card)
        self.generate_22000.setObjectName("captureCheckBox")
        self.generate_22000.setToolTip(
            "建议勾选，抓到可用数据后自动生成能直接跑包的文件。关闭则不生成这种文件；缺少信号名称或数据不完整时，可能无法生成，会显示原因。"
        )
        self.auto_open = QCheckBox("任务完成后打开结果目录", card)
        self.auto_open.setObjectName("captureCheckBox")
        self.auto_open.setToolTip(
            "开启后，定向抓包任务成功、且确实有文件落盘时，自动打开结果目录。\n"
            "不勾选也不丢文件，自己点“打开包存储目录”就行。"
        )
        layout.addLayout(create_path_row(card, "保存目录", self.output_dir, self.output_dir_button, "选择电脑上空间充足、容易找到的文件夹。软件会在下面创建 \u8def\u5c0f\u7ba1\u6293\u5305 文件夹存放结果。"))
        layout.addLayout(create_form_row(card, "刷新间隔", self.refresh_seconds, "建议保留 10 秒。数值越小，列表和结果更新越及时，但会增加路由器和连接的负担；连接慢或不稳定时可调大。"))
        layout.addWidget(self.keep_raw)
        layout.addWidget(self.generate_22000)
        layout.addWidget(self.auto_open)
        self.clear_history_button = QPushButton(card)
        self.clear_history_button.setObjectName("captureSecondaryButton")
        self.clear_history_button.setToolTip(
            "需要重新抓取以前已完成的信号时使用。清除完成记录，不删除已保存的包；只想重抓一个信号，可在列表中右键清除它的状态。"
        )
        self.clear_history_button.clicked.connect(
            lambda: self._request("clear_verified_history")
        )
        layout.addWidget(self.clear_history_button)
        root.addWidget(card)
        self.output_dir.setText(
            str(
                self.resource_picker.settings_manager.normalized_output_dir(
                    settings.get("output_dir", "")
                )
            )
        )
        self.refresh_seconds.setValue(int(settings.get("refresh_seconds", 10)))
        self.keep_raw.setChecked(bool(settings.get("keep_raw_capture", True)))
        self.generate_22000.setChecked(bool(settings.get("generate_22000", True)))
        self.auto_open.setChecked(bool(settings.get("auto_open_dir", True)))
        self._refresh_history_button()
        self.output_dir.editingFinished.connect(self._normalize_output_dir)
        self.output_dir_button.clicked.connect(lambda: self.resource_picker.choose_output_dir(self.output_dir))
        self.refresh_seconds.lineEdit().editingFinished.connect(self.settingsChanged)
        for control in (self.keep_raw, self.generate_22000, self.auto_open):
            control.toggled.connect(lambda _checked: self.settingsChanged.emit())

    def _build_handshake_options(self, parent, root, settings):
        """创建常规抓包专用设置：只管手动「常规抓包」按钮。"""

        card, layout = create_capture_card(parent, "常规抓包", "用于勾选目标后的常规抓包。")
        self.handshake_rounds_mode = CommonComboBox(card)
        self.handshake_rounds_mode.setObjectName("captureComboBox")
        self.handshake_rounds_mode.addItem("按固定轮数", "fixed")
        self.handshake_rounds_mode.addItem("无限循环（无进展时结束）", "infinite")
        self.handshake_rounds_mode.setToolTip(
            "选“按固定轮数”时按下面的轮数尝试；选“无限循环”时不再限制轮数，抓到结果、连续两轮没有新内容、发生错误或手动停止时结束，此时轮数输入框隐藏。长时间运行会一直占用网卡。"
        )
        self.handshake_rounds = QSpinBox(card)
        self.handshake_rounds.setObjectName("captureSpinBox")
        self.handshake_rounds.setRange(1, 99)
        self.handshake_rounds.setSuffix(" 轮")
        self.handshake_rounds.setToolTip(
            "建议保留 3 轮。未抓到可用包的信号会再尝试，轮数越多等待越久。"
            "已完成的信号会跳过，连续两轮没有新内容时也会结束。"
        )
        self.active_deauth = QCheckBox("允许主动断连（默认开启）", card)
        self.active_deauth.setObjectName("captureCheckBox")
        self.active_deauth.setToolTip(
            "只影响常规抓包。开启会让目标设备短暂断网并重新连接，以便抓到连接数据；不想影响正在上网的设备就关闭。仅用于自有或明确授权的网络，开始前会要求确认。部分路由器可能因此断开无线连接，遇到这种情况请关闭。"
        )
        layout.addLayout(create_form_row(card, "抓包轮数模式", self.handshake_rounds_mode, self.handshake_rounds_mode.toolTip()))
        self._rounds_number_row = create_form_row(card, "定向抓包轮数", self.handshake_rounds, self.handshake_rounds.toolTip())
        layout.addLayout(self._rounds_number_row)
        layout.addWidget(self.active_deauth)
        root.addWidget(card)
        self.handshake_rounds.setValue(int(settings.get("handshake_rounds", 3)))
        self.handshake_rounds_mode.setCurrentIndex(1 if bool(settings.get("handshake_rounds_infinite", False)) else 0)
        self._apply_rounds_mode_visible()
        self.active_deauth.setChecked(bool(settings.get("active_deauth", True)))
        self.handshake_rounds.lineEdit().editingFinished.connect(self.settingsChanged)
        self.handshake_rounds_mode.currentIndexChanged.connect(self._sync_rounds_mode)
        self.active_deauth.toggled.connect(lambda _checked: self.settingsChanged.emit())

    def _apply_rounds_mode_visible(self):
        """按当前轮数模式显示或隐藏轮数输入框整行，不触发保存。"""

        infinite = self.handshake_rounds_mode.currentData() == "infinite"
        for index in range(self._rounds_number_row.count()):
            widget = self._rounds_number_row.itemAt(index).widget()
            if widget is not None:
                widget.setVisible(not infinite)

    def _sync_rounds_mode(self, _index):
        """轮数模式变化后同步输入框显隐，同时标记配置已改。"""

        self._apply_rounds_mode_visible()
        self.settingsChanged.emit()

    def _refresh_history_button(self):
        """按已抓名单条数刷新清除按钮文字与可用状态，无记录时禁用。"""

        try:
            count = len(self.service.verified_history_snapshot())
        except (AttributeError, TypeError):
            count = 0
        self.clear_history_button.setText(
            f"清除已抓可用包记录（{count} 条）" if count else "清除已抓可用包记录"
        )
        self.clear_history_button.setEnabled(count > 0)

    def _build_password_options(self, parent, root, settings):
        """创建在线密码验证的独立设置分组；这里只保存字典路径，不运行验证。"""

        card, layout = create_capture_card(parent, "猜密码设置", "这是会修改路由器无线/网络配置的高风险功能。字典路径只保存位置，执行前必须再次确认并完成备份。")
        self.password_dictionary = PasswordDictionaryEditor(card)
        self.password_dictionary.setPlainText("\n".join(dictionary_paths(settings)))
        layout.addWidget(self.password_dictionary)
        self.password_count_label = QLabel(card)
        self.password_count_label.setObjectName("captureDescription")
        layout.addWidget(self.password_count_label)
        root.addWidget(card)
        self._refresh_dictionary_count()
        self.password_dictionary.pathsChanged.connect(self._dictionaries_changed)

    def _dictionary_paths(self):
        return dictionary_paths({"password_dictionaries":
            self.password_dictionary.toPlainText().splitlines()})

    def _dictionaries_changed(self):
        self._refresh_dictionary_count()
        self.settingsChanged.emit()

    def _request(self, action):
        """把设置分组动作转发给页面主线程协调器。"""

        self.actionRequested.emit(action)

    def _display_local_path(self, value):
        """将配置中的便携本地路径解析为界面绝对路径；空值保持为空。"""

        if not str(value or "").strip():
            return ""
        return str(self.resource_picker.settings_manager.resolve_path(value))

    def _refresh_dictionary_count(self):
        """刷新字典候选数说明，文件缺失或为空时如实提示。"""

        from modules.capture_services.dictionary_preview import DictionaryPreview

        if self._disposed:
            return
        if self._dictionary_job is not None:
            self._dictionary_job.cancel.set()
            return
        job = DictionaryPreview(self._dictionary_paths())
        self._dictionary_job = job
        self.password_count_label.setText("正在统计字典候选数...")
        job.completed.connect(self._dictionary_count_ready, Qt.QueuedConnection)
        self.destroyed.connect(job.cancel.set)
        try:
            job.start()
        except RuntimeError:
            job.text = "无法启动字典统计线程，请稍后重试。"
            self._dictionary_count_ready(job)

    @Slot(object)
    def _dictionary_count_ready(self, job):
        """只显示当前路径对应结果，旧任务结束后处理最新输入。"""
        from shiboken6 import isValid
        if self._disposed or not isValid(self):
            return
        self.destroyed.disconnect(job.cancel.set)
        self._dictionary_job = None
        if job.path != self._dictionary_paths() or job.cancel.is_set():
            self._refresh_dictionary_count()
        else:
            self.password_count_label.setText(job.text)
            self.password_dictionary.apply_metadata(job.metadata)

    def cancel_preview(self):
        """页面释放时取消统计，已发出的回调不再更新控件。"""
        self._disposed = True
        if self._dictionary_job is not None:
            self._dictionary_job.cancel.set()

    def _normalize_output_dir(self):
        """规范化手工输入并回显最终绝对目录，然后请求保存配置。"""

        normalized = self.resource_picker.settings_manager.normalized_output_dir(
            self.output_dir.text()
        )
        self.output_dir.setText(str(normalized))
        self.settingsChanged.emit()

    def collect_settings(self):
        """返回当前页面可落盘设置，明确不包含密码；SSH 与功能开关由登录页收集。"""

        result = {
            "output_dir": self.output_dir.text().strip(),
            "refresh_seconds": self.refresh_seconds.value(),
            "handshake_rounds": self.handshake_rounds.value(),
            "handshake_rounds_infinite": self.handshake_rounds_mode.currentData() == "infinite",
            "keep_raw_capture": self.keep_raw.isChecked(),
            "generate_22000": self.generate_22000.isChecked(),
            "auto_open_dir": self.auto_open.isChecked(),
            "active_deauth": self.active_deauth.isChecked(),
            "password_dictionaries": list(self._dictionary_paths()),
            "password_dictionary": next(iter(self._dictionary_paths()), ""),
        }
        return result

    def apply_snapshot(self, snapshot):
        """只刷新名单按钮；连接与设备显示已搬到登录页。"""

        # 名单条数变化（记/清）时同步按钮文字与可用状态。
        self._refresh_history_button()

    def _apply_snapshot(self, snapshot):
        """服务信号适配槽，保持 Qt 回调名称清晰。"""

        self.apply_snapshot(snapshot)
