from modules.capture_services.openwrt.password_verification import dictionary_paths
"""\u8def\u5c0f\u7ba1 抓包主页面。"""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QSignalBlocker
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout

from modules.config_manager import ConfigManager
from modules.config_services.capture_settings_manager import CaptureSettingsManager
from modules.main_pages.info_panel import InfoPanel
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.main_pages.packet_capture.openwrt.openwrt_page import OpenWrtPage
from modules.main_pages.packet_capture.openwrt.ui_features import PMKID_UI_VISIBLE
from modules.common_widgets.common_dialogs import MessageDialog
from modules.main_pages.packet_capture.openwrt.confirmation_helpers import confirm_host_fingerprint
from modules.main_pages.packet_capture.openwrt.confirmation_helpers import confirm_fingerprint_change
from modules.main_pages.packet_capture.openwrt.confirmation_helpers import confirm_radio_takeover
from modules.common_widgets.common_dialogs import ConfirmDialog



class PacketCapturePage(QFrame):
    """主窗口堆栈中的抓包页面，直接装配路由器四个标签和信息面板。

    参数：capture_service 由 ResidentRuntime 长期持有；scale_manager 跟随主窗口缩放；
    settings_manager 可由测试注入。页面只装配 UI 和保存普通配置，不执行平台命令。
    副作用：构造时读取抓包配置并连接服务信号；页面销毁不会停止后台抓包服务。
    """


    def __init__(self, capture_service, parent=None, scale_manager=None, settings_manager=None,
                 light_theme=False):
        super().__init__(parent)
        self.setObjectName("packetCapturePage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.capture_service = capture_service
        self.scale_manager = scale_manager
        self.settings_manager = settings_manager or CaptureSettingsManager()
        self.settings = self.settings_manager.load()
        self.light_theme = bool(light_theme)
        self.style_mgr = CapturePageStyleManager(self)

        self.root_layout = QHBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(6)

        saved_font_delta = int(ConfigManager().load().get("ui", "info_font_delta", fallback="0"))
        self.info_panel = self._create_info_panel(
            saved_font_delta,
            "抓包环境、设备探测和任务信息会显示在这里。",
            "抓包功能架构已建立。\n\n"
            "OpenWrt 模式将通过 SSH 控制已授权设备，抓包结果保存为 PCAPNG，并生成 22000。\n"
            "主动断连默认开启，启动定向任务时会如实说明本次行为。",
        )
        self.settings_info_panel = self._create_info_panel(
            saved_font_delta,
            "路由器登录、设备检测和依赖安装信息会显示在这里。",
            "等待登录 OpenWrt 路由器。\n\n"
            "登录后会显示设备探测、SFTP 和抓包依赖检查结果。\n"
            "依赖安装失败时，详细原因和处理建议也会保留在这里。",
            log_category="\u8def\u7531\u5668\u767b\u5f55",
        )
        self.signal_info_panel = self._create_info_panel(
            saved_font_delta, "路由器信号管理信息", "",
            log_category="\u4fe1\u53f7\u7ba1\u7406",
        )
        self.file_info_panel = self._create_info_panel(
            saved_font_delta, "\u6587\u4ef6\u7ba1\u7406\u548c\u4f20\u8f93\u4fe1\u606f", "", log_category="\u6587\u4ef6\u7ba1\u7406",
        )
        self.common_info_panel = self._create_info_panel(
            saved_font_delta, "\u5e38\u7528\u8bbe\u7f6e\u64cd\u4f5c\u4fe1\u606f", "", log_category="\u5e38\u7528\u8bbe\u7f6e",
        )
        self.openwrt_tab = OpenWrtPage(
            self.capture_service,
            self.settings["openwrt"],
            self.info_panel,
            self.settings_info_panel,
            self,
            signal_info_panel=self.signal_info_panel,
            file_info_panel=self.file_info_panel,
            common_info_panel=self.common_info_panel,
        )
        self.root_layout.addWidget(self.openwrt_tab, 1)
        from .packet_capture.openwrt.configuration_progress_controller import ConfigurationProgressController
        self.configuration_progress = ConfigurationProgressController(self, self.capture_service)
        self.openwrt_tab.settingsChanged.connect(self._save_openwrt_settings)
        self.openwrt_tab.actionRequested.connect(self._handle_openwrt_action)
        # 已抓名单进常驻服务内存；记/清后由服务信号触发同一套配置保存链落盘。
        # 设置页构造时内存还是空的，载完必须手动刷一次按钮，否则有记录也一直灰着。
        self.capture_service.set_verified_history(
            self.settings.get("openwrt", {}).get("verified_bssids", {}), initial_only=True,
        )
        self.capture_service.verified_results.configure_store(self.settings_manager)
        self.settings["openwrt"]["verified_bssids"] = self.capture_service.verified_history_snapshot()

        self.capture_service.verifiedHistoryChanged.connect(
            self._on_verified_history_changed
        )
        self.capture_service.taskFinishedNotice.connect(self._show_task_result_notice)
        self.capture_service.compatibleToolSuggested.connect(self._suggest_compatible_tool)
        self.capture_service.takeoverProbeFinished.connect(self._on_takeover_probe_finished)
        # 射频预检的待认领请求（probe_id, 设置副本, 勾选快照），结果过期直接丢弃。
        self._pending_takeover_probe = None
        self.capture_service.openwrtFingerprintRequired.connect(
            self._confirm_openwrt_fingerprint
        )
        self.capture_service.messageGenerated.connect(self.info_panel.append_info)
        if self.capture_service.openwrt_snapshot.auto_summary:
            self.info_panel.append_info(self.capture_service.openwrt_snapshot.auto_summary, "info")
        self.capture_service.settingsMessageGenerated.connect(
            self.settings_info_panel.append_info
        )
        self.info_panels = (self.info_panel, self.settings_info_panel, self.signal_info_panel, self.file_info_panel, self.common_info_panel)
        for panel in self.info_panels:
            panel.fontDeltaChanged.connect(self._sync_info_font_delta)
        self.style_mgr.set_light_theme(self.light_theme)


    def _create_info_panel(self, font_delta, placeholder, default_text, log_category="\u6293\u5305"):
        """创建抓包页统一样式的信息窗口，不连接服务信号。"""

        return InfoPanel(
            self,
            placeholder_text=placeholder,
            default_text=default_text,
            light_theme=self.light_theme,
            scale_manager=self.scale_manager,
            initial_font_delta=font_delta,
            save_font_delta_callback=self._save_info_font_delta,
            floating_zoom_controls=True,
            color_success_messages=True,
            log_category=log_category,
            export_context_provider=self._router_export_context,
            diagnostic_references=lambda: self.capture_service.dependency_service.diagnostic_references(),
        )


    def _router_export_context(self, masker):
        """四个路由器信息窗口共用当前连接的冻结诊断，不持有旧设备缓存。"""
        from modules.capture_services.openwrt.diagnostic_context import router_context_json
        backend=self.capture_service.openwrt_backend
        selected=backend.pmkid_capture.tool_selection
        compatible=bool(selected and selected[0] is backend.ssh_client._client)
        from PySide6.QtWidgets import QApplication
        evidence = backend.command_runner.radio_diagnostics.snapshot()
        version = QApplication.instance().applicationVersion()
        return router_context_json(self.capture_service.openwrt_snapshot, masker, compatible,
                                   radio_evidence=evidence, app_version=version,
                                   operation_evidence=backend.command_runner.operation_diagnostics.snapshot(),
                                   signal_snapshot=self.capture_service.router_signals.snapshot,
                                   signal_setup_snapshot=self.capture_service.router_signals.setup.snapshot,
                                   application_window=self.window(),
                                   device_history=self.capture_service.diagnostic_history)

    def _suggest_compatible_tool(self):
        """\u786e\u8ba4\u540e\u68c0\u67e5\u517c\u5bb9\u5305\uff0c\u4e0d\u81ea\u52a8\u4fee\u6539\u8def\u7531\u5668\u3002"""
        if ConfirmDialog("\u68c0\u67e5\u517c\u5bb9\u5de5\u5177", "\u672c\u6b21 PMKID \u542f\u52a8\u5931\u8d25\u53ef\u80fd\u6d89\u53ca\u5de5\u5177\u7248\u672c\u6216\u63a5\u53e3\u517c\u5bb9\u6027\u3002\u662f\u5426\u5e2e\u4f60\u68c0\u67e5\u5f53\u524d\u56fa\u4ef6\u7684\u5b98\u65b9\u517c\u5bb9\u5305\uff1f\u5b89\u88c5\u524d\u4f1a\u663e\u793a\u7248\u672c\u548c\u786e\u8ba4\u3002", self).exec() != ConfirmDialog.Accepted:
            return
        self.openwrt_tab.tabs.setCurrentWidget(self.openwrt_tab.login_page)
        section = self.openwrt_tab.login_page.compatible_tools
        self.openwrt_tab.login_page.scroll_area.ensureWidgetVisible(section.card)
        section.check()

    def _show_task_result_notice(self, title, text):
        """任务终局弹窗告知最终结果，主线程调用，不展示敏感内容。"""

        MessageDialog(self.info_panel.display_text(title), self.info_panel.display_text(text), self).exec()


    def _save_openwrt_settings(self):
        """保存 OpenWrt 普通设置，明确不收集密码控件内容。"""

        self.settings["openwrt"].update(self.openwrt_tab.collect_settings())
        # 已抓名单由常驻服务维护，这里合并后一起落盘，不经过页面表单。
        self.settings["openwrt"]["verified_bssids"] = (
            self.capture_service.verified_history_snapshot()
        )
        return self._save_settings_with_feedback()

    def _on_verified_history_changed(self, history):
        """常驻服务已保存历史，页面只同步内存，防止其他设置保存时写回旧名单。"""
        self.settings["openwrt"]["verified_bssids"] = history

    def _handle_openwrt_action(self, action):
        """在主线程处理 OpenWrt 页面动作，确认和配置保存都在 worker 启动前完成。"""

        if action == "fingerprint_preference_changed":
            # 用户取消“保存此设备的主机指纹”时，必须同时移除当前设备的旧记录。
            # 否则连接器仍会读取旧指纹，把用户主动改为临时信任误判成主机指纹变化。
            from copy import deepcopy
            previous = deepcopy(self.settings["openwrt"])
            connection = self.openwrt_tab.login_page.connection
            cleared = False
            if not connection.fingerprint_check.isChecked():
                settings = self.openwrt_tab.connection_settings()
                try:
                    key = self._fingerprint_key(settings.get("host", ""), settings.get("port", 22))
                    self.settings["openwrt"].setdefault("trusted_host_fingerprints", {}).pop(key, None)
                    cleared = bool(str(settings.get("host", "")).strip())
                except (TypeError, ValueError):
                    # 地址或端口尚未填写完整时没有可删除的稳定键，保存普通偏好即可。
                    pass
            saved = self._save_openwrt_settings()
            if not saved:
                self.settings["openwrt"].clear()
                self.settings["openwrt"].update(previous)
                with QSignalBlocker(connection.fingerprint_check):
                    connection.fingerprint_check.setChecked(bool(previous.get("remember_host_fingerprint", False)))
            elif cleared:
                self.openwrt_tab.login_page.show_storage_feedback("\u5df2\u5220\u9664\u672c\u673a\u4fdd\u5b58\u7684\u5f53\u524d\u8bbe\u5907\u6307\u7eb9")
            return
        if action == "clear_verified_history":
            count = len(self.capture_service.verified_history_snapshot())
            if count <= 0:
                self.info_panel.append_info("当前没有已抓可用包记录，无需清除。", "info")
                return
            if ConfirmDialog(
                "清除已抓可用包记录",
                f"将清除 {count} 条已抓出可用包的信号记忆，状态列恢复为待处理。\n\n"
                "只删记忆名单，不删除任何包文件；下次抓出可用包会重新记住。",
                self,
                yes_text="确认清除",
                no_text="取消",
            ).exec() != ConfirmDialog.Accepted:
                return
            # 清空方法内部已发名单变更信号，保存链自动落盘，这里不再重复保存。
            removed = self.capture_service.clear_verified_history()
            self.info_panel.append_info(f"已清除 {removed} 条已抓可用包记录。", "success")
            return
        if action == "scan":
            self._start_openwrt_scan()
            return
        if action == "pmkid":
            self._start_openwrt_pmkid()
            return
        if action == "handshake":
            self._start_openwrt_handshake()
            return
        if action == "password":
            self._start_openwrt_password()
            return
        if action in {"pause_password", "resume_password"}:
            self.capture_service.set_openwrt_password_paused(action == "pause_password")
            return
        if action == "wps":
            self._start_openwrt_wps("second")
            return
        if action == "advanced_wps_confirmed":
            dialog_mode = str(
                (self.settings.get("openwrt", {}) or {}).get("wps_defaults", {}).get("mode", "normal")
            )
            self._start_openwrt_wps(
                {"normal": "normal", "quick": "second", "offline": "offline"}.get(dialog_mode, "normal")
            )
            return
        if action.startswith("capture_dependency:"):
            from .packet_capture.openwrt.dependency_actions import operate_capture
            operate_capture(self, action.split(":",1)[1])
            return
        if action == "install_dependencies":
            self._install_openwrt_dependencies()
            return
        if action == "uninstall_dependencies":
            self._uninstall_openwrt_dependencies()
            return
        if action == "open_output_dir":
            self._open_output_dir()
            return
        if action == "stop":
            if self.capture_service.cancel_openwrt_task():
                task_type = self.capture_service.openwrt_snapshot.task_type.value
                if task_type.startswith("pmkid_"):
                    task_name = "PMKID 任务"
                elif task_type == "handshake":
                    task_name = "定向任务"
                elif task_type == "password_verify":
                    task_name = "猜密码任务"
                elif task_type == "wps":
                    task_name = "WPS 任务"
                else:
                    task_name = "Wi-Fi 扫描"
                self.info_panel.append_info(
                    f"正在停止{task_name}并清理路由器临时资源...",
                    "info",
                )
            return
        if action != "connect":
            # 其他功能尚未接后端，按钮应保持禁用；这里保留防御性反馈，避免外部误触。
            # 反馈必须发到动作所在的页面信息窗（设置页动作进设置信息窗），否则用户
            # 在设置页点按钮，却要去常规页找日志。
            self._report_unhandled_action(action)
            return
        snapshot = self.capture_service.openwrt_snapshot
        if snapshot.connection_state.value in {"connected", "disconnecting"}:
            if not self.capture_service.disconnect_openwrt_async():
                self.info_panel.append_info("当前有任务运行，暂时不能断开路由器。", "warning")
            return
        settings = self.openwrt_tab.connection_settings()
        # 登录瞬间直接读取当前可见密码框，不能只依赖 textChanged 曾经同步到服务内存。
        # 页面重建、凭据回显或 Qt 信号顺序变化时，界面已有密码仍必须能正常登录。
        password = self.openwrt_tab.login_page.connection.password_text()
        if settings.get("auth_method") == "password":
            self.capture_service.set_openwrt_password(password)
        try:
            normalized = self.capture_service.validate_openwrt_settings(settings)
        except Exception as exc:
            MessageDialog(
                "OpenWrt 登录设置有误",
                f"无法开始登录。\n\n原因：{exc}\n\n请检查路由器地址、SSH 端口和用户名。",
                self,
            ).exec()
            return
        settings.update(normalized)
        if (
            settings.get("auth_method") == "private_key"
            and not str(settings.get("private_key_path", "")).strip()
        ):
            message = "当前选择私钥认证，但没有选择 SSH 私钥文件。请选择 OpenSSH/PEM 私钥后再试。"
            self.settings_info_panel.append_info(message, "error")
            MessageDialog("缺少 SSH 私钥", message, self).exec()
            return
        self.settings["openwrt"].update(settings)
        if not self._save_settings_with_feedback():
            return
        try:
            self.capture_service.save_openwrt_password_preference(settings)
        except OSError as exc:
            MessageDialog(
                "保存路由器密码失败",
                f"Windows 凭据管理器无法保存密码。\n\n原因：{exc}\n\n"
                "可以取消“记住密码”后继续登录，密码仍只在当前进程内使用。",
                self,
            ).exec()
            return
        fingerprint_key = self._fingerprint_key(normalized["host"], normalized["port"])
        # 未勾选时只进行本次人工核对，绝不读取历史指纹；勾选时才启用长期自动校验。
        expected = ""
        if bool(settings.get("remember_host_fingerprint", False)):
            expected = self.settings["openwrt"].get("trusted_host_fingerprints", {}).get(
                fingerprint_key,
                "",
            )
        if not self.capture_service.connect_openwrt_async(settings, expected):
            self.info_panel.append_info("当前已有抓包或连接任务，请等待完成后重试。", "warning")

    def _require_feature_dependencies(self, feature, display):
        """依赖缺失时弹窗指引去登录页安装；返回 True 表示已拦截。

        只拦明确 MISSING 的功能；从未检测过直接放行，由后端如实报错。
        """

        snapshot = self.capture_service.openwrt_snapshot
        for item in snapshot.dependencies:
            if item.key == feature and item.state.value == "missing":
                packages = "、".join(item.missing_packages or ()) or "对应软件包"
                if ConfirmDialog(
                    f"{display}缺少依赖",
                    f"{display}需要依赖：{packages}。\n\n请到“路由器登录”页安装依赖后再试。",
                    self,
                    yes_text="去登录页安装",
                    no_text="取消",
                ).exec() == ConfirmDialog.Accepted:
                    self.openwrt_tab.tabs.setCurrentIndex(0)
                return True
        return False

    def _start_openwrt_scan(self):
        """主线程确认无线运行态变更后启动扫描 worker。"""

        settings = self.openwrt_tab.connection_settings()
        snapshot = self.capture_service.openwrt_snapshot
        if snapshot.connection_state.value != "connected":
            message = "开始 Wi-Fi 扫描前，请先在“路由器登录”中登录 OpenWrt 路由器。"
            self.info_panel.append_info(message, "warning")
            self.settings_info_panel.append_info(message, "warning")
            MessageDialog("请先登录路由器", message, self).exec()
            # 用户确认提示后直接进入 SSH 设置，避免还要手动寻找登录入口。
            self.openwrt_tab.tabs.setCurrentIndex(0)
            self.openwrt_tab.login_page.connection.login_button.setFocus()
            return
        if self._require_feature_dependencies("scan", "信号扫描"):
            return
        controller = getattr(self, "_scan_start_controller", None)
        if controller is None:
            from modules.main_pages.packet_capture.scan_start_controller import ScanStartController
            controller = self._scan_start_controller = ScanStartController(self)
        controller.start(settings)

    def _start_openwrt_pmkid(self):
        """验证连接和目标后，在主线程确认授权范围并启动 PMKID 任务。"""

        snapshot = self.capture_service.openwrt_snapshot
        if snapshot.connection_state.value != "connected":
            message = "开始 PMKID 任务前，请先在“路由器登录”中登录 OpenWrt 路由器。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("请先登录路由器", message, self).exec()
            self.openwrt_tab.tabs.setCurrentIndex(0)
            self.openwrt_tab.login_page.connection.login_button.setFocus()
            return
        if self._require_feature_dependencies("pmkid", "PMKID"):
            return
        settings = self.openwrt_tab.connection_settings()
        from modules.capture_services.openwrt.hcx_capabilities import validate_pmkid_mode, HcxCompatibilityError
        try:
            validate_pmkid_mode(settings.get("mode", "filter"), bool(settings.get("pmkid_active_beacon", False)))
        except HcxCompatibilityError as exc:
            self.info_panel.append_info(str(exc), "warning")
            MessageDialog("PMKID 尚未开始", str(exc), self).exec()
            return
        selected = tuple(point.bssid for point in snapshot.access_points if point.selected)
        if settings.get("mode") != "global" and not selected:
            message = "当前为手动过滤模式，请先在扫描结果表中勾选至少一个目标。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("请选择目标", message, self).exec()
            return
        mode_text = "所选目标" if settings.get("mode") != "global" else "当前频段（全局）"
        band = "5G" if settings.get("band") == "5G" else "2.4G"
        takeover = ({"needed": False} if settings.get("idle_radio_only", False)
                    else self._pmkid_takeover_need(snapshot, band))
        if takeover is None:
            message = "设备无线信息缺失，无法判断是否需要接管无线网卡，请重新连接后再试。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("信息缺失", message, self).exec()
            return
        if takeover["needed"]:
            if bool(settings.get("skip_radio_takeover_confirmation", False)):
                settings["allow_radio_takeover"] = True
            else:
                accepted, remember = confirm_radio_takeover(
                    self,
                    band,
                    "全频段跳频",
                    takeover["ap_channel"],
                )
                if not accepted:
                    return
                if remember:
                    settings["skip_radio_takeover_confirmation"] = True
                    self.settings["openwrt"]["skip_radio_takeover_confirmation"] = True
                    if not self._save_settings_with_feedback():
                        return
                settings["allow_radio_takeover"] = True
        if bool(settings.get("pmkid_active_beacon", False)):
            if ConfirmDialog(
                "开始 PMKID 任务（主动请求）",
                f"本次选择对{mode_text}按设置进行 PMKID 主动请求采集。\n\n"
                "向下面勾选的授权目标发送认证和关联请求，尝试获取 PMKID。不发送断连帧、"
                "全局信标或客户端诱导；需要 hcxdumptool 6.3.4 和驱动发送支持。"
                "目标可能不返回 PMKID，无效结果不保存。\n\n"
                + "\n".join(self.capture_service._masked_target(p.ssid,p.bssid)
                            for p in snapshot.access_points if p.selected)
                + "\n\n请确认你拥有以上设备和网络的管理或测试授权。",
                self,
                yes_text="确认并开始",
                no_text="取消",
            ).exec() != ConfirmDialog.Accepted:
                return
        elif not takeover["needed"] and ConfirmDialog(
            "开始 PMKID 任务",
            f"将对{mode_text}按设置进行被动 PMKID 数据采集。\n\n"
            "后台核对 6.2.4/6.3.5 精确版本与能力；目标模式最多 32 个 BSSID。"
            "两版必需接收白名单仅保留信标、探测响应及普通/QoS EAPOL-Key，阻断主动响应触发帧。"
            "6.2.4 会漏收接收方属工具内置 16 个随机 OUI 的 M1，以及重放计数为 "
            "0xf000..0xfffe 的 M3；需要这些帧时请使用被动常规握手。"
            "接收 BPF 不等于发射过滤，不保证原始包及元数据只含目标；本地结果再次按目标整理。"
            "通过准入后不主动发射，不修改 UCI；任务结束后仅保存含可用组合的 PCAPNG，"
            "但在转换验证完成前不代表已经获得有效 PMKID。\n\n"
            "请确认你拥有相关设备和网络的管理或测试授权。",
            self,
            yes_text="确认并开始",
            no_text="取消",
        ).exec() != ConfirmDialog.Accepted:
            return
        try:
            settings["resolved_output_dir"] = str(
                CaptureSettingsManager.normalized_output_dir(settings.get("output_dir"))
            )
        except Exception as exc:
            self.info_panel.append_info(f"结果目录无效：{exc}", "error")
            return
        if not self.capture_service.capture_openwrt_pmkid_async(settings, selected):
            self.info_panel.append_info("当前任务条件不满足或已有任务运行，不能开始 PMKID。", "warning")

    @staticmethod
    def _pmkid_takeover_need(snapshot, band):
        """根据快照判断 PMKID 任务是否需要临时接管 radio。

        PMKID 全频段跳频，只要目标频段没有空闲 radio 就需要接管，与目标信道无关。
        参数：snapshot 是常驻服务快照；band 是目标频段。返回值：不需要接管返回
        {"needed": False}；需要返回 {"needed": True, "ap_channel"}；无线信息缺失
        返回 None。只读判断，不执行探测。
        """

        radios = [r for r in snapshot.device.radios if band in (r.bands or ())]
        if not radios:
            return None
        if any(r.free for r in radios):
            return {"needed": False}
        ap_channel = next((r.channel for r in radios if r.channel), 0)
        return {"needed": True, "ap_channel": ap_channel or "未知"}

    def _start_openwrt_handshake(self):
        """验证连接和目标后，在主线程确认授权并启动多目标定向任务。

        支持一次勾选多个目标：逐个抓取，最多循环指定轮数，某轮已出可跑包结论的
        目标不再重复。单目标与多目标走同一批量通道。
        """

        snapshot = self.capture_service.openwrt_snapshot
        if snapshot.connection_state.value != "connected":
            message = "开始定向任务前，请先在“路由器登录”中登录 OpenWrt 路由器。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("请先登录路由器", message, self).exec()
            self.openwrt_tab.tabs.setCurrentIndex(0)
            self.openwrt_tab.login_page.connection.login_button.setFocus()
            return
        if self._require_feature_dependencies("handshake", "常规抓包"):
            return
        settings = self.openwrt_tab.connection_settings()
        selected = tuple(point.bssid for point in snapshot.access_points if point.selected)
        if not selected:
            message = "定向任务需要至少选择一个已扫描目标，请先扫描并在结果表中勾选目标。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("请选择目标", message, self).exec()
            return
        target_points = [
            point for point in snapshot.access_points if point.bssid in selected
        ]
        if settings.get("idle_radio_only", False):
            # 严格模式由后台实时核实空闲，从不需要接管确认，直接进开始确认。
            self._launch_handshake_with_probe_result(dict(settings), selected, target_points, [])
            return
        probe_id = self.capture_service.probe_openwrt_takeover_async(dict(settings), selected)
        if not probe_id:
            self.info_panel.append_info("当前任务条件不满足或已有任务运行，不能开始定向任务。", "warning")
            return
        self._pending_takeover_probe = (probe_id, dict(settings), selected)
        self.info_panel.append_info("正在检测射频占用（只读普查，不修改路由器配置），请稍候…", "info")

    def _on_takeover_probe_finished(self, probe_id, entries, error, cancelled):
        """预检结果回主线程：凭 probe_id 认领，新鲜结论进确认框，过期直接丢弃。"""

        pending = self._pending_takeover_probe
        if pending is None or pending[0] != probe_id:
            return
        self._pending_takeover_probe = None
        settings, selected = pending[1], pending[2]
        if cancelled:
            self.info_panel.append_info("射频占用检测已取消。", "info")
            return
        if error:
            self.info_panel.append_info(f"射频占用检测失败：{error}", "warning")
            return
        snapshot = self.capture_service.openwrt_snapshot
        if snapshot.connection_state.value != "connected":
            self.info_panel.append_info("射频检测完成时连接已断开，请重新登录后再试。", "warning")
            return
        current = tuple(point.bssid for point in snapshot.access_points if point.selected)
        if set(current) != set(selected):
            self.info_panel.append_info("检测期间目标选择已变化，请重新开始定向任务。", "warning")
            return
        by_bssid = {str(entry.bssid).upper(): entry for entry in entries or ()}
        target_points = [
            point for point in snapshot.access_points if point.bssid in selected
        ]
        failed = []
        takeovers = []
        for point in target_points:
            entry = by_bssid.get(str(point.bssid).upper())
            if entry is None or getattr(entry, "error", ""):
                failed.append((point, "" if entry is None else entry.error))
                continue
            if getattr(entry, "need_takeover", False):
                takeovers.append({
                    "needed": True,
                    "band": point.band or "2.4G",
                    "target_channel": point.channel,
                    "ap_channel": getattr(entry, "phy_channel", 0) or "未知",
                })
        if failed:
            names = "、".join(
                f"{point.ssid or point.bssid}（{reason or '无普查结论'}）"
                for point, reason in failed[:3]
            )
            message = f"{names}：射频选卡失败，不能开始定向任务，请检查设备无线状态后重试。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("选卡失败", message, self).exec()
            return
        self._launch_handshake_with_probe_result(settings, selected, target_points, takeovers)

    def _launch_handshake_with_probe_result(self, settings, selected, target_points, takeovers):
        """凭新鲜普查结论走接管确认与开始确认，通过后启动多目标定向任务。

        参数：settings 是启动时冻结的设置副本（授权只写副本，不污染配置）；
        takeovers 是需接管的目标信息（空表示都不需要）。本方法仍在主线程弹框。
        """
        if takeovers:
            if bool(settings.get("skip_radio_takeover_confirmation", False)):
                settings["allow_radio_takeover"] = True
            else:
                accepted, remember = confirm_radio_takeover(
                    self,
                    "/".join(sorted({item["band"] for item in takeovers})),
                    f"{len(takeovers)} 个目标需要独立射频" if len(target_points) > 1 else f"信道 {takeovers[0]['target_channel']}",
                    next(
                        (str(item["ap_channel"]) for item in takeovers if item["ap_channel"]),
                        "未知",
                    ),
                )
                if not accepted:
                    return
                if remember:
                    settings["skip_radio_takeover_confirmation"] = True
                    self.settings["openwrt"]["skip_radio_takeover_confirmation"] = True
                    if not self._save_settings_with_feedback():
                        return
                settings["allow_radio_takeover"] = True
        # 单次确认：开关在设置页，这里按开关如实说明本次行为，不再加弹窗。
        names = [point.ssid or point.bssid for point in target_points[:3]]
        scope_text = "、".join(names)
        if len(target_points) > 3:
            scope_text += f"等 {len(target_points)} 个目标"
        if not bool(settings.get("skip_handshake_confirmation", False)):
            if bool(settings.get("active_deauth", False)):
                client_count = sum(len(getattr(point, "clients", ()) or ()) for point in target_points)
                if client_count:
                    start_text = (
                        f"将对 {scope_text} 的已发现及任务中后续发现的客户端逐个发送少量"
                        "去认证帧，迫使其短暂断线重连以产生握手；同时被动采集原始 CAP。\n\n"
                        "这些客户端会短暂断网（通常几秒自动恢复）。"
                        "只能用于自有或已获明确授权的网络。\n\n"
                        "请确认你拥有相关设备和网络的管理或测试授权。"
                    )
                else:
                    start_text = (
                        f"{scope_text}当前没有已发现的客户端，暂时被动等待；主动断连开关已开启，"
                        "任务中后续发现客户端时仍会发送少量去认证帧，使其短暂断线重连。"
                        "全程未发现客户端才不会发送。只能用于自有或已获明确授权的网络；"
                        "任务结束后只保留含可跑包组合的原始 CAP。\n\n"
                        "请确认你拥有相关设备和网络的管理或测试授权。"
                    )
                dialog = ConfirmDialog(
                    "开始定向任务",
                    start_text,
                    self,
                    yes_text="确认并开始",
                    no_text="取消",
                    remember_option_text="下次不再提示",
                )
                if dialog.exec() != ConfirmDialog.Accepted:
                    return
                if dialog.remember_checked:
                    settings["skip_handshake_confirmation"] = True
                    self.settings["openwrt"]["skip_handshake_confirmation"] = True
                    if not self._save_settings_with_feedback():
                        return
            else:
                dialog = ConfirmDialog(
                    "开始定向任务",
                    f"将对{scope_text}进行限定时间的定向数据采集。同信道的目标同时抓取，不同信道分组轮换，最多循环指定轮数。\n\n"
                    "当前实现为被动等待，不执行主动断连，不修改 UCI；任务结束后只保留含可跑包组合的原始 CAP，"
                    "凑不出组合的不保留，在转换验证完成前不代表已经获得有效结果。\n\n"
                    "请确认你拥有相关设备和网络的管理或测试授权。",
                    self,
                    yes_text="确认并开始",
                    no_text="取消",
                    remember_option_text="下次不再提示",
                )
                if dialog.exec() != ConfirmDialog.Accepted:
                    return
                if dialog.remember_checked:
                    settings["skip_handshake_confirmation"] = True
                    self.settings["openwrt"]["skip_handshake_confirmation"] = True
                    if not self._save_settings_with_feedback():
                        return
        try:
            settings["resolved_output_dir"] = str(
                CaptureSettingsManager.normalized_output_dir(settings.get("output_dir"))
            )
        except Exception as exc:
            self.info_panel.append_info(f"结果目录无效：{exc}", "error")
            return
        if not self.capture_service.capture_openwrt_handshakes_async(settings, selected):
            self.info_panel.append_info("当前任务条件不满足或已有任务运行，不能开始定向任务。", "warning")

    def _start_openwrt_password(self):
        """验证连接、目标和字典后，在主线程确认授权并启动在线密码验证。

        支持一次勾选多个目标：逐个试字典，找到即写结果文件。候选密码永不进日志
        和消息，成功只报文件路径。路由器无线配置会被临时修改，结束自动恢复。
        """

        snapshot = self.capture_service.openwrt_snapshot
        if snapshot.connection_state.value != "connected":
            message = "开始猜密码前，请先在“路由器登录”中登录 OpenWrt 路由器。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("请先登录路由器", message, self).exec()
            self.openwrt_tab.tabs.setCurrentIndex(0)
            self.openwrt_tab.login_page.connection.login_button.setFocus()
            return
        if self._require_feature_dependencies("password", "猜密码"):
            return
        settings = self.openwrt_tab.connection_settings()
        selected = tuple(point.bssid for point in snapshot.access_points if point.selected)
        if not selected:
            message = "猜密码需要至少选择一个已扫描目标，请先扫描并在结果表中勾选目标。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("请选择目标", message, self).exec()
            return
        dictionary = dictionary_paths(settings)
        if not dictionary or any(not Path(path).is_file() for path in dictionary):
            message = "密码字典不存在，请先在“设置 → 猜密码设置”中选择字典文件。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("请选择字典", message, self).exec()
            self.openwrt_tab.tabs.setCurrentWidget(self.openwrt_tab.settings_page)
            return
        names = [
            point.ssid or point.bssid
            for point in snapshot.access_points
            if point.bssid in selected
        ][:3]
        scope_text = "、".join(names)
        if len(selected) > 3:
            scope_text += f"等 {len(selected)} 个目标"
        if ConfirmDialog(
            "开始猜密码（高风险）",
            f"将对{scope_text}逐个在线试字典密码。\n\n"
            "路由器会被临时改成客户端去连目标：无线配置先备份、结束自动恢复；"
            "期间路由器自身 Wi-Fi 会中断，耗时很长（每个密码约十几秒）。"
            "只能用于自有或已获明确授权的网络。\n\n"
            "请确认你拥有相关设备和网络的管理或测试授权。",
            self,
            yes_text="确认并开始",
            no_text="取消",
        ).exec() != ConfirmDialog.Accepted:
            return
        try:
            settings["resolved_output_dir"] = str(
                CaptureSettingsManager.normalized_output_dir(settings.get("output_dir"))
            )
        except Exception as exc:
            self.info_panel.append_info(f"结果目录无效：{exc}", "error")
            return
        if not self.capture_service.capture_openwrt_password_async(settings, selected):
            self.info_panel.append_info("当前任务条件不满足或已有任务运行，不能开始猜密码。", "warning")

    def _start_openwrt_wps(self, mode):
        """验证连接、目标与同频后，在主线程确认授权并启动 WPS 任务。

        参数 mode 是 second/normal/offline/combo。耗时很长且部分路由器会锁定
        WPS，只测自有或已获明确授权的网络。
        """

        snapshot = self.capture_service.openwrt_snapshot
        if snapshot.connection_state.value != "connected":
            message = "开始 WPS 前，请先在“路由器登录”中登录 OpenWrt 路由器。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("请先登录路由器", message, self).exec()
            self.openwrt_tab.tabs.setCurrentIndex(0)
            self.openwrt_tab.login_page.connection.login_button.setFocus()
            return
        settings = self.openwrt_tab.connection_settings()
        selected = tuple(point.bssid for point in snapshot.access_points if point.selected)
        if not selected:
            message = "WPS 需要至少选择一个已扫描目标，请先扫描并在结果表中勾选支持 WPS 的目标。"
            self.info_panel.append_info(message, "warning")
            MessageDialog("请选择目标", message, self).exec()
            return
        names = [
            point.ssid or point.bssid
            for point in snapshot.access_points
            if point.bssid in selected
        ][:3]
        scope_text = "、".join(names)
        if len(selected) > 3:
            scope_text += f"等 {len(selected)} 个目标"
        mode_text = {"normal": "常规 Pin", "second": "秒 Pin", "offline": "离线 Pin"}.get(mode, "组合 Pin")
        if ConfirmDialog(
            f"开始{mode_text}（高风险）",
            f"将对{scope_text}逐个跑 {mode_text}。\n\n"
            "耗时很长（分钟到小时级），期间占用无线网卡；部分路由器会锁定 WPS，"
            "导致后续正常连接都受影响。只能用于自有或已获明确授权的网络。\n\n"
            "请确认你拥有相关设备和网络的管理或测试授权。",
            self,
            yes_text="确认并开始",
            no_text="取消",
        ).exec() != ConfirmDialog.Accepted:
            return
        try:
            settings["resolved_output_dir"] = str(
                CaptureSettingsManager.normalized_output_dir(settings.get("output_dir"))
            )
        except Exception as exc:
            self.info_panel.append_info(f"结果目录无效：{exc}", "error")
            return
        if not self.capture_service.capture_openwrt_wps_async(settings, selected, mode):
            self.info_panel.append_info("当前任务条件不满足或已有任务运行，不能开始 WPS。", "warning")

    def _install_openwrt_dependencies(self):
        from .packet_capture.openwrt.dependency_actions import install_capture
        install_capture(self)

    def _uninstall_openwrt_dependencies(self):
        from .packet_capture.openwrt.dependency_actions import uninstall_capture
        uninstall_capture(self)

    def _report_unhandled_action(self, action):
        """未接入动作的防御性反馈，发到动作所在的页面信息窗。"""

        panel = self.info_panel
        if action in {"install_dependencies"}:
            settings_panel = self.openwrt_tab.login_page.info_panel
            if settings_panel is not None:
                panel = settings_panel
        panel.append_info(f"功能尚未接入：{action}", "warning")

    def _open_output_dir(self):
        """打开抓包结果保存目录；目录不存在则先创建，无效则明确提示。"""

        from pathlib import Path

        settings = self.openwrt_tab.connection_settings()
        try:
            directory = Path(
                str(CaptureSettingsManager.normalized_output_dir(settings.get("output_dir")))
            )
            directory.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.info_panel.append_info(f"结果目录无效：{exc}", "error")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self.info_panel.append_info(f"打开保存目录失败：{directory}", "error")

    def _confirm_openwrt_fingerprint(self, result):
        """Confirm this connection; persist identity only when explicitly enabled."""

        settings = self.openwrt_tab.connection_settings()
        host = str(settings.get("host", "")).strip()
        port = int(settings.get("port", 22))
        if getattr(result, "status", "") == "fingerprint_changed":
            if not confirm_fingerprint_change(
                self,
                host,
                port,
                result.key_type,
                getattr(result, "expected_fingerprint", ""),
                result.fingerprint,
            ):
                self.capture_service.reject_openwrt_fingerprint()
                return
            if bool(settings.get("remember_host_fingerprint", False)):
                if not self._persist_openwrt_fingerprint(host, port, result.fingerprint):
                    self.capture_service.reject_openwrt_fingerprint()
                    return
            self.capture_service.connect_openwrt_async(settings, result.fingerprint)
            return
        accepted, _dialog_remember_fingerprint = confirm_host_fingerprint(
            self,
            host,
            port,
            result.key_type,
            result.fingerprint,
        )
        if not accepted:
            self.capture_service.reject_openwrt_fingerprint()
            return
        # 设置页的复选框是唯一的长期保存开关；弹窗仅负责让用户本次核对指纹。
        if bool(settings.get("remember_host_fingerprint", False)):
            if not self._persist_openwrt_fingerprint(host, port, result.fingerprint):
                self.capture_service.reject_openwrt_fingerprint()
                return
        self.capture_service.connect_openwrt_async(settings, result.fingerprint)

    def _persist_openwrt_fingerprint(self, host, port, fingerprint):
        """Publish permanent trust only after the configuration write succeeds."""
        settings = self.settings["openwrt"]
        previous = dict(settings.get("trusted_host_fingerprints", {}))
        settings["trusted_host_fingerprints"] = {
            **previous, self._fingerprint_key(host, port): fingerprint,
        }
        if self._save_settings_with_feedback():
            return True
        settings["trusted_host_fingerprints"] = previous
        return False

    @staticmethod
    def _fingerprint_key(host, port):
        """生成稳定的主机指纹配置键，区分同地址不同 SSH 端口。"""

        return f"{str(host).strip().casefold()}:{int(port)}"

    def _save_settings_with_feedback(self):
        """保存抓包配置，失败时说明文件、原因和建议处理方式。"""

        try:
            self.settings = self.settings_manager.save(self.settings)
                # 保存会返回全新标准化字典，页面持有的 OpenWrt 配置引用必须同步更新，
            # 否则后续高级 Pin 和内部标签仍读取保存前旧对象。
            self.openwrt_tab.settings = self.settings["openwrt"]
            if "capture" in self.openwrt_tab._created_pages:
                self.openwrt_tab._created_pages["capture"].settings = self.settings["openwrt"]
            return True
        except OSError as exc:
            MessageDialog(
                "抓包设置保存失败",
                f"无法写入抓包配置：{self.settings_manager.storage_hint()}\n\n原因：{exc}\n\n"
                "只有主副本写入失败才会报错；请确认磁盘空间充足、用户配置目录可写，"
                "并关闭占用配置文件的软件后重试。（软件目录只读不影响保存）",
                self,
            ).exec()
            return False

    def _sync_info_font_delta(self, delta):
        """Keep every router log panel on the same shared zoom value."""
        for panel in self.info_panels:
            panel.set_font_delta(delta)

    def _save_info_font_delta(self, delta):
        """沿用主页面共享的信息文字缩放配置。"""

        manager = ConfigManager()
        config = manager.load()
        config.set("ui", "info_font_delta", str(delta))
        try:
            manager.save(config)
        except OSError as exc:
            self.info_panel.append_info(f"保存信息文字缩放失败：{exc}", "error")

    def set_light_theme(self, enabled, force=False):
        """应用主窗口主题；force 供主题文件整体切换时强制刷新。"""

        if not force and self.light_theme == bool(enabled):
            return
        self.style_mgr.set_light_theme(enabled)

    def apply_scale(self):
        """应用主窗口最新缩放管理器并刷新页面尺寸和字体。"""

        for panel in self.info_panels:
            panel._scale_manager = self.scale_manager
        self.style_mgr.apply_scale()

    def has_background_activity(self):
        """返回常驻抓包服务是否忙，用于页面空闲回收判断。"""

        return self.capture_service.is_busy or self.capture_service.router_terminal.active

    def dispose(self):
        """页面级空闲释放前断开长期服务信号，不停止后台抓包会话。"""


        self.configuration_progress.dispose()
        self.openwrt_tab.dispose()
        self.capture_service.verifiedHistoryChanged.disconnect(self._on_verified_history_changed)
        self.capture_service.taskFinishedNotice.disconnect(self._show_task_result_notice)
        try:
            self.capture_service.messageGenerated.disconnect(self.info_panel.append_info)
            self.capture_service.settingsMessageGenerated.disconnect(
                self.settings_info_panel.append_info
            )
            self.capture_service.openwrtFingerprintRequired.disconnect(
                self._confirm_openwrt_fingerprint
            )
        except (RuntimeError, TypeError):
            pass
