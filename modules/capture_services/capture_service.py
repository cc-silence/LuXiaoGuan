from modules.capture_services.openwrt.password_verification import dictionary_paths
"""抓包会话常驻服务。"""

from threading import Event, Lock, Thread
from copy import deepcopy

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from modules.capture_services.backends import OpenWrtCaptureBackend, WindowsWifiCaptureBackend
from modules.capture_services.backends.openwrt_backend import (
    _MAX_HANDSHAKE_ROUNDS, HandshakeBatchResult, HandshakeTargetResult,
)
from modules.capture_services.openwrt.result_converter import CONVERTIBLE_VERDICTS as _CONVERTIBLE_VERDICTS
from modules.capture_services.models import CaptureSessionState, WindowsCaptureCapabilities
from modules.capture_services.task_controller import CaptureTaskController
from modules.capture_services.dependency_service import OpenWrtDependencyService
from modules.capture_services.verified_results import VerifiedResultCheck, VerifiedResults, check_verified_results
from modules.capture_services.router_signal_service import RouterSignalService
from modules.capture_services.openwrt.models import (
    AccessPointCaptureState,
    CaptureArtifact,
    CaptureProgress,
    CaptureTaskType,
    OpenWrtConnectionState,
)
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4


class CaptureService(QObject):
    """长期持有抓包后端和会话状态，允许主窗口销毁后任务仍有明确所有者。

    输入参数：parent 通常是 ResidentRuntime；无运行时时可由 MainWindow 临时持有。
    信号：capabilitiesChanged 返回 Windows 能力快照；stateChanged 返回稳定状态字符串；
    messageGenerated 返回用户可见文本及层级（抓包任务侧，进常规页信息窗）；
    settingsMessageGenerated 返回设置侧文本及层级（登录/登出/指纹，进设置页信息窗）；
    activityChanged 通知运行时重算待机释放。
    副作用：能力检测会在守护线程读取 Windows 驱动和安装路径，不修改系统。
    """

    capabilitiesChanged = Signal(object)
    stateChanged = Signal(str)
    messageGenerated = Signal(str, str)
    settingsMessageGenerated = Signal(str, str)
    windowsMessageGenerated = Signal(str, str)
    activityChanged = Signal()
    openwrtSnapshotChanged = Signal(object)
    openwrtFingerprintRequired = Signal(object)
    verifiedHistoryChanged = Signal(object)
    taskFinishedNotice = Signal(str, str)
    takeoverProbeFinished = Signal(str, object, str, bool)
    compatibleToolSuggested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.windows_backend = WindowsWifiCaptureBackend()
        self.openwrt_backend = OpenWrtCaptureBackend()
        from .openwrt.diagnostic_history import RouterDiagnosticHistory
        self.diagnostic_history = RouterDiagnosticHistory()
        self.openwrtSnapshotChanged.connect(self.diagnostic_history.observe)
        self.task_controller = CaptureTaskController(self)
        from .scan_management_service import ScanManagementService
        self.scan_management = ScanManagementService(self)
        self.task_controller.stateChanged.connect(self._on_task_state_changed)
        self.task_controller.pauseChanged.connect(self._on_task_pause_changed)
        self.windows_capabilities = WindowsCaptureCapabilities.unchecked()
        self.state = CaptureSessionState.IDLE
        self._probe_lock = Lock()
        self._probe_cancel = Event()
        self._probe_thread = None
        self._shutting_down = False
        # SSH 密码只保存在当前进程内的服务对象中，页面空闲回收后仍可恢复；退出时清空。
        self._openwrt_password = ""
        self._openwrt_workers = {}
        self._pending_fingerprint_request = None
        self._handshake_active_deauth = False
        # 本次多目标批次的 BSSID 顺序（与后端进度 target_index 对齐），
        # 用于把进度映射回“正在抓包的那一行”；单目标与非批量任务保持空。
        self._handshake_batch_targets = ()
        # 射频预检的 probe_id：页面凭它认领新鲜普查结果，过期/取消的直接丢弃。
        self._takeover_probe_id = ""
        # 本次任务的结果偏好（任务启动时暂存，结束处理器在主线程读取）：
        # _openwrt_auto_open 是 (是否自动打开目录, 结果目录)，
        # _pmkid_keep_raw 是 PMKID 是否保留原始（H 由后端按设置直接处理）。
        self._openwrt_auto_open = (True, "")
        self._pmkid_keep_raw = True
# 已抓出可用包的 BSSID 名单（内存）：{大写BSSID: {ssid, first_seen,
        # cap_path, hc22000_path}}；页面构造/重建时从配置载入，记/清后发
        # verifiedHistoryChanged 由页面转存落盘；重扫回显和状态列都读这一份。
        self._verified_history = {}
        self._verified_history_loaded = False
        self.verified_results = VerifiedResults(self)
        # 隐私掩码开关：任务启动时从配置快照，当次任务播报沿用，切换下次任务生效。
        self._mask_bssid = False
        self._mask_ssid = False
        # 信号管理独立持有业务状态，只共享连接和后台任务生命周期。
        self.router_signals = RouterSignalService(self)
        # 抓包 worker 在接管/恢复无线后顺手推一次信号清单；回调内部自保失败静默。
        self.openwrt_backend.signal_refresh_callback = self.router_signals.refresh_from_worker
        # 抓包依赖安装同样独立编排，只借任务槽与 worker，不进本文件。
        self.dependency_service = OpenWrtDependencyService(self)
        from .router_management_service import RouterManagementService
        self.router_management = RouterManagementService(self)
        from .router_terminal_service import RouterTerminalService
        self.router_terminal = RouterTerminalService(self)
        self.router_terminal.activityChanged.connect(self._terminal_activity_changed)
        from .router_connection_state import RouterConnectionState
        self.router_connection_state = RouterConnectionState(self)
        from .openwrt.auto_capture_service import AutoCaptureService
        self.auto_capture = AutoCaptureService(self)
        self.auto_capture.messageGenerated.connect(self.messageGenerated)
        from modules.cache_maintenance import CacheMaintenance
        self._shutdown_maintenance = CacheMaintenance(self)
        self._shutdown_maintenance.warning.connect(self.messageGenerated)
        self._shutdown_maintenance.activityChanged.connect(self._shutdown_activity_changed)
        self._shutdown_started = False

    @property
    def is_busy(self):
        """返回探测、启动、抓包或停止是否正在进行，无副作用。"""

        return ((self._shutting_down and self.router_terminal.active) or self._shutdown_maintenance.active or bool(self._openwrt_workers)
                or bool(self._probe_thread and self._probe_thread.is_alive()) or (hasattr(self, "auto_capture") and self.auto_capture.is_busy)) or self.state in {
            CaptureSessionState.CHECKING,
            CaptureSessionState.STARTING,
            CaptureSessionState.CAPTURING,
            CaptureSessionState.STOPPING,
        }

    def _stash_result_prefs(self, options):
        """暂存本次任务的结果偏好，结束处理器在主线程读取。

        参数 options 是任务启动配置。只存内存，不写配置；不经过 worker 回调参数，
        避免改动各结果模型。调用方是各任务启动方法，必须在 worker 启动前调用。
        """

        self._openwrt_auto_open = (
            bool(options.get("auto_open_dir", True)),
            str(options.get("resolved_output_dir") or ""),
        )
        self._pmkid_keep_raw = bool(options.get("keep_raw_capture", True))

    def _notify_task_result(self, title, text):
        """任务终局弹窗通知，由页面在主线程显示。

        参数 title/text 是标题与正文。文本绝不含候选密码等敏感内容；
        用户主动取消不调用（取消是已知动作，不打扰）。
        """

        self.taskFinishedNotice.emit(str(title), str(text))

    def _maybe_auto_open_dir(self, produced):
        """任务成功且有文件落盘时，按开关自动打开结果目录。

        参数 produced 表示本次确实有文件落盘（无产物时不打扰用户）。
        成功打开不播报（目录自己会弹出来）；打开失败才提示一句。
        """

        try:
            enabled, directory = self._openwrt_auto_open
        except (AttributeError, TypeError, ValueError):
            return
        if not (enabled and produced and directory):
            return
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        except Exception as exc:
            self.messageGenerated.emit(f"结果目录已生成，但自动打开失败：{exc}", "warning")

    def set_verified_history(self, history, *, initial_only=False):
        """从配置载入已抓可用包名单，只收合法 BSSID 条目，不发信号。

        参数 history 是配置中的 verified_bssids 原始字典。页面构造/重建时调用；
        最终清洗由配置管理器落盘时执行，这里只做键规范化，避免脏数据进内存。
        """

        if initial_only and self._verified_history_loaded:
            return
        self._verified_history_loaded = True
        from modules.config_services.capture_settings_manager import CaptureSettingsManager

        self._verified_history = CaptureSettingsManager._verified_history(history)
        self.verified_results.availability.clear()
        self.verified_results.revision += 1

    def verified_history_snapshot(self):
        """返回内存名单的深拷贝，供页面收集落盘，不暴露内部对象。"""

        return deepcopy(self._verified_history)

    def record_verified_result(self, bssid, ssid, cap_path, hc22000_path, hccapx_path=""):
        """记一条已抓出可用包的目标，主线程调用。

        参数 bssid/ssid 是目标标识，cap_path/hc22000_path/hccapx_path 是本次产物
        （可能为空，例如关闭保留原始后只剩转换产物）。同 BSSID 重复抓取时更新信号名、
        时间和文件，first_seen 保留首次。调用后发 verifiedHistoryChanged，由页面转存落盘。
        """

        key = str(bssid or "").strip().upper()
        if not key:
            return
        self._verified_history_loaded = True
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        previous = self._verified_history.get(key, {})
        self.verified_results.revision += 1
        self._verified_history[key] = {
            "ssid": str(ssid or previous.get("ssid", "")),
            "first_seen": str(previous.get("first_seen", "") or now),
            **self.verified_results.record_paths(previous, (cap_path, hc22000_path, hccapx_path)),
        }
        self.verified_results.availability[key] = "present"
        # 最多保留 200 条，超了删最旧的，与配置管理器清洗上限一致。
        if len(self._verified_history) > 200:
            ordered = sorted(
                self._verified_history.items(),
                key=lambda item: str(item[1].get("first_seen", "")),
            )
            self._verified_history = dict(ordered[-200:])
        self.verifiedHistoryChanged.emit(self.verified_history_snapshot())

    def _apply_verified_history(self, point, existing_selection):
        """扫描回显单个 AP：保留勾选，命中已抓名单的标“已有可用包”。"""

        updated = point.with_selected(point.bssid.upper() in existing_selection)
        if (
            updated.capture_state != AccessPointCaptureState.CAPTURED
            and (point.bssid.upper() in self._verified_history or any(
                old.bssid.upper() == point.bssid.upper()
                and old.capture_state == AccessPointCaptureState.CAPTURED
                for old in self.openwrt_snapshot.access_points
            ))
        ):
            updated = replace(updated, capture_state=AccessPointCaptureState.CAPTURED)
        return self.verified_results.decorate(updated)

    def clear_verified_history(self):
        """清空已抓可用包名单，返回清掉的条数，主线程调用。

        本次会话产物快照里有已验证产物的目标保持“已有可用包”，其余靠记忆标绿的
        打回“待处理”；快照和名单变更都发信号，页面转存落盘并刷新表格。
        """

        self._verified_history_loaded = True
        removed = len(self._verified_history)
        self._verified_history = {}
        self.verified_results.availability.clear()
        self.verified_results.revision += 1
        kept = {
            str(artifact.bssid or "").strip().upper()
            for artifact in self.openwrt_backend.snapshot.artifacts
            if artifact.verified and str(artifact.bssid or "").strip()
        }
        snapshot = self.openwrt_backend.snapshot
        access_points = tuple(
            point
            if point.capture_state != AccessPointCaptureState.CAPTURED
            or point.bssid.upper() in kept
            else replace(point, capture_state=AccessPointCaptureState.PENDING, result_availability="")
            for point in snapshot.access_points
        )
        self.openwrt_backend.snapshot = replace(snapshot, access_points=access_points)
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        self.verifiedHistoryChanged.emit({})
        return removed

    def clear_verified_result(self, bssid):
        """清除单个目标的已抓状态，打回待处理，返回是否清掉，主线程调用。

        包括只有本次会话结果、没有历史记录的目标。只重置目标状态和历史记忆，
        保留产物快照及文件；快照和名单变更发信号，由页面保存配置并刷新表格。
        """

        key = str(bssid or "").strip().upper()
        snapshot = self.openwrt_backend.snapshot
        if not key or not any(
            point.bssid.upper() == key
            and point.capture_state == AccessPointCaptureState.CAPTURED
            for point in snapshot.access_points
        ):
            return False
        self._verified_history.pop(key, None)
        self.verified_results.availability.pop(key, None)
        self.verified_results.revision += 1
        access_points = tuple(
            replace(point, capture_state=AccessPointCaptureState.PENDING, result_availability="")
            if point.bssid.upper() == key
            and point.capture_state == AccessPointCaptureState.CAPTURED
            else point
            for point in snapshot.access_points
        )
        self.openwrt_backend.snapshot = replace(snapshot, access_points=access_points)
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        self.verifiedHistoryChanged.emit(self.verified_history_snapshot())
        return True

    def probe_windows_capabilities_async(self):
        """在后台执行 Windows 抓包能力探测。

        返回值：True 表示创建了新探测线程；已有探测或服务退出时返回 False。
        副作用：启动守护线程并通过 Qt Signal 回传结果，不阻塞主线程。
        """

        if self._shutting_down or not self._probe_lock.acquire(blocking=False):
            return False
        self._probe_cancel.clear()
        self._set_state(CaptureSessionState.CHECKING)

        def run_probe():
            try:
                result = self.windows_backend.probe(cancel_event=self._probe_cancel)
                if not self._shutting_down:
                    self.windows_capabilities = result
                    self.capabilitiesChanged.emit(result)
                    level = "success" if result.ready else "warning"
                    text = "Windows 本机抓包环境可用。" if result.ready else "Windows 本机抓包环境未满足全部条件。"
                    self.windowsMessageGenerated.emit(text, level)
            except Exception as exc:
                if not self._shutting_down:
                    self.windowsMessageGenerated.emit(
                        f"检测 Windows 抓包环境失败：{exc}", "error"
                    )
            finally:
                self._probe_lock.release()
                if not self._shutting_down:
                    self._set_state(CaptureSessionState.IDLE)

        self._probe_thread = Thread(target=run_probe, daemon=True, name="WinBoxCaptureProbe")
        self._probe_thread.start()
        return True

    def validate_openwrt_settings(self, settings):
        """委托 OpenWrt 后端校验连接设置，不联网、不保存配置。"""

        return self.openwrt_backend.validate_settings(settings)

    @property
    def openwrt_snapshot(self):
        """返回页面可回放的 OpenWrt 快照，不暴露后端协作者。"""

        return self.openwrt_backend.snapshot

    def set_openwrt_access_points(self, access_points):
        """替换 AP 模型并通知所有已创建页面；不执行扫描。"""

        snapshot = self.openwrt_backend.set_access_points(access_points)
        self.openwrtSnapshotChanged.emit(snapshot)

    def set_openwrt_selected_bssids(self, bssids):
        """更新共享目标集合并广播快照；后端任务只读取该稳定选择。"""

        snapshot = self.openwrt_backend.set_selected_bssids(bssids)
        self.openwrtSnapshotChanged.emit(snapshot)

    def set_privacy_mask(self, mask_bssid=False, mask_ssid=False):
        """\u517c\u5bb9\u65e7\u5165\u53e3\uff1b\u4efb\u52a1\u6d88\u606f\u4fdd\u7559\u539f\u59cb\u6807\u8bc6\u3002"""
        self._mask_bssid = self._mask_ssid = False

    def _stash_privacy_mask(self, settings):
        """\u663e\u793a\u5f00\u5173\u4e0d\u8fdb\u5165\u6267\u884c\u5feb\u7167\u3002"""
        self._mask_bssid = self._mask_ssid = False

    def _masked_target(self, ssid, bssid):
        """\u7ed3\u6784\u5316\u5feb\u7167\u4fdd\u7559\u539f\u59cb\u76ee\u6807\u6807\u8bc6\u3002"""
        return str(ssid or bssid or "")

    def _on_task_state_changed(self, state, task_type, session_id):
        """把通用任务控制器状态同步到服务和 OpenWrt 页面快照。"""

        self._set_state(CaptureSessionState(state))
        try:
            normalized_type = (
                CaptureTaskType.NONE
                if state in {"idle", "failed"}
                else CaptureTaskType(task_type)
            )
        except ValueError:
            normalized_type = CaptureTaskType.NONE
        progress = self.openwrt_backend.snapshot.progress
        if state == "stopping" and normalized_type == CaptureTaskType.PASSWORD_VERIFY:
            progress = CaptureProgress(
                "正在停止猜密码", 0, 0, "正在恢复路由器配置，请等待清理完成。", True,
            )
        connection_changes = {}
        if normalized_type == CaptureTaskType.CONNECT and state == "starting":
            # 登录任务与连接状态必须同步发布，避免页面按旧的未连接状态清空当次密码。
            connection_changes = dict(
                connection_state=OpenWrtConnectionState.CONNECTING,
                connection_detail="正在登录路由器...",
                last_error="",
            )
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            **connection_changes,
            task_type=normalized_type,
            task_state=state,
            pause_state=(self.task_controller.pause_control.state
                         if self.task_controller.pause_control is not None else ""),
            session_id=session_id,
            progress=CaptureProgress() if state in {"idle", "failed"} else progress,

        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)

    def _on_task_pause_changed(self, session_id):
        """主线程回放当前暂停状态，忽略旧会话排队通知，不携带候选内容。"""
        snapshot = self.openwrt_backend.snapshot
        control = self.task_controller.pause_control
        if (snapshot.session_id != session_id or control is None
                or snapshot.task_type != CaptureTaskType.PASSWORD_VERIFY):
            return
        self.openwrt_backend.snapshot = replace(snapshot, pause_state=control.state)
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)

    def set_openwrt_password_paused(self, paused):
        """请求在线验证暂停或继续，由 worker 在候选边界响应。"""
        return self.task_controller.set_paused(paused)

    def connect_openwrt_async(self, settings, expected_fingerprint=""):
        """在服务持有的 QThread 中连接 OpenWrt 并执行只读探测。

        返回值：成功启动返回 True；已有任务或服务退出时返回 False。密码从服务内存读取，
        不进入 settings、signal 或日志。副作用：可能建立 SSH/SFTP 连接。
        """

        if self._shutting_down or self.task_controller.busy:
            return False
        # 先冻结当次登录输入，后续同步状态通知不得改变后台认证的凭据。
        settings_copy = dict(settings)
        password = self._openwrt_password
        session_id = uuid4().hex
        cancel_event = self.task_controller.begin(
            CaptureTaskType.CONNECT.value,
            session_id,
            CaptureSessionState.STARTING,
        )
        if cancel_event is None:
            return False
        network_ready = getattr(self, "network_ready", None)

        def operation(event, progress=None):
            if network_ready is not None:
                network_ready(event)
            return self.openwrt_backend.connect(
                settings_copy,
                password,
                expected_fingerprint=expected_fingerprint,
                cancel_event=event,
                progress_callback=progress,
            )

        self._start_openwrt_worker(
            session_id,
            operation,
            cancel_event,
            self._on_openwrt_connect_succeeded,
            progress_slot=self._on_openwrt_connect_progress,
        )
        return True

    def disconnect_openwrt_async(self):
        """在后台关闭 OpenWrt SSH/SFTP，避免主线程同步清理网络资源。"""

        if self._shutting_down or self.task_controller.busy:
            return False
        session_id = uuid4().hex
        cancel_event = self.task_controller.begin(
            CaptureTaskType.CONNECT.value,
            session_id,
            CaptureSessionState.STOPPING,
        )
        if cancel_event is None:
            return False
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            connection_state=OpenWrtConnectionState.DISCONNECTING,
            connection_detail="正在断开 OpenWrt...",
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        self._start_openwrt_worker(
            session_id,
            lambda _event: self.openwrt_backend.disconnect(),
            cancel_event,
            self._on_openwrt_disconnect_succeeded,
        )
        return True

    def scan_openwrt_wifi_async(self, settings):
        """在后台执行已获主线程确认的 OpenWrt Wi-Fi 扫描。

        返回 True 表示 worker 已启动。副作用：短时修改无线运行态；后端保证清理临时
        monitor、PID 和工作目录，页面不能直接调用后端。
        """

        if self._shutting_down or self.task_controller.busy:
            return False
        if self.openwrt_backend.snapshot.connection_state != OpenWrtConnectionState.CONNECTED:
            return False
        session_id = uuid4().hex
        cancel_event = self.task_controller.begin(
            CaptureTaskType.WIFI_SCAN.value, session_id, CaptureSessionState.STARTING
        )
        if cancel_event is None:
            return False
        options = dict(settings)
        self._stash_privacy_mask(settings)
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            progress=CaptureProgress("正在扫描 Wi-Fi", 0, 0, "正在创建临时 monitor 接口。", True),
            last_error="",
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        band = "5G" if options.get("band") == "5G" else "2.4G"
        seconds = options.get("scan_seconds", 24)
        self.messageGenerated.emit(
            f"开始扫描 Wi-Fi 信号：频段 {band}，约 {seconds} 秒。",
            "info",
        )
        audit_input = self.verified_results.freeze()

        def operation(event, progress, message):
            progress(check_verified_results(*audit_input, event))
            if event.is_set():
                raise InterruptedError("扫描已取消。")
            return self.openwrt_backend.scan_wifi(session_id, options, event, progress, message)

        self._start_openwrt_worker(
            session_id,
            operation,
            cancel_event,
            self._on_openwrt_scan_succeeded,
            self._on_openwrt_scan_failed,
            self._on_openwrt_scan_progress,
            self.messageGenerated.emit,
        )
        return True

    def capture_openwrt_pmkid_async(self, settings, selected_bssids=()):
        """在后台执行主线程已授权的被动 PMKID 原始包采集。

        返回 True 表示任务已启动。手动过滤模式必须传入已选择 BSSID；本方法不创建
        弹窗。后端只保留原始 PCAPNG，不在此阶段声明已获得有效 PMKID。
        """

        if self._shutting_down or self.task_controller.busy:
            return False
        if self.openwrt_backend.snapshot.connection_state != OpenWrtConnectionState.CONNECTED:
            return False
        mode = "global" if settings.get("mode") == "global" else "filter"
        active_request = bool(settings.get("pmkid_active_beacon",False))
        from .openwrt.hcx_capabilities import validate_pmkid_mode,HcxCompatibilityError
        try:
            validate_pmkid_mode(mode,active_request)
        except HcxCompatibilityError as exc:
            self.messageGenerated.emit(str(exc),"warning")
            return False
        targets = tuple(selected_bssids) if mode == "filter" else ()
        if mode == "filter" and not targets:
            return False
        if active_request:
            selected = {p.bssid.upper() for p in self.openwrt_backend.snapshot.access_points if p.selected}
            if not {str(value).upper() for value in targets}.issubset(selected):
                self.messageGenerated.emit("主动请求的目标选择已变化，请重新勾选授权目标。","warning")
                return False
        task_type = (
            CaptureTaskType.PMKID_GLOBAL
            if mode == "global"
            else CaptureTaskType.PMKID_FILTER
        )
        session_id = uuid4().hex
        cancel_event = self.task_controller.begin(
            task_type.value,
            session_id,
            CaptureSessionState.STARTING,
        )
        if cancel_event is None:
            return False
        options = dict(settings)
        self._stash_privacy_mask(settings)
        self._stash_result_prefs(options)
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            progress=CaptureProgress(
                "正在采集 PMKID",
                0,
                0,
                "正在核对工具版本和所选功能组合。",
                True,
            ),
            last_error="",
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        band_text = "5G" if options.get("band") == "5G" else "2.4G"
        duration = int(options.get("pmkid_duration_seconds",0))
        duration_note = f"限时约 {duration} 秒" if duration else "不限时"
        if mode == "global":
            scope_text = "全局模式"
        else:
            known = {
                point.bssid.upper(): self._masked_target(point.ssid, point.bssid)
                for point in self.openwrt_backend.snapshot.access_points
            }
            picked = [known.get(str(bssid).upper(), bssid) for bssid in targets[:3]]
            scope_text = "、".join(picked)
            if len(targets) > 3:
                scope_text += f"等 {len(targets)} 个目标"
        beacon_note = "，目标主动请求模式" if bool(options.get("pmkid_active_beacon", False)) else "，被动监听模式"
        self.messageGenerated.emit(
            f"准备 PMKID 采集：{scope_text}，{band_text}频段，{duration_note}{beacon_note}。"
            "先检查工具再开始采集；只保存已确认可用的结果。",
            "info",
        )
        self._start_openwrt_worker(
            session_id,
            lambda event, progress, message: self.openwrt_backend.capture_pmkid(
                session_id,
                options,
                targets,
                event,
                progress,
                message,
            ),
            cancel_event,
            self._on_openwrt_pmkid_succeeded,
            self._on_openwrt_pmkid_failed,
            self._on_openwrt_pmkid_progress,
            self.messageGenerated.emit,
        )
        return True

    def capture_openwrt_handshake_async(self, settings, selected_bssids=()):
        """在后台执行主线程已授权的单目标定向 H 任务。

        返回 True 表示任务已启动。目标必须来自最近一次 S 快照且恰好选择一个；去认证
        与否由 settings 的 active_deauth 决定（页面层已提前确认）。产物只标记为未验证
        的原始 CAP，本方法不创建弹窗。
        """

        if self._shutting_down or self.task_controller.busy:
            return False
        if self.openwrt_backend.snapshot.connection_state != OpenWrtConnectionState.CONNECTED:
            return False
        selected = {str(value).upper() for value in selected_bssids}
        targets = tuple(
            point
            for point in self.openwrt_backend.snapshot.access_points
            if point.bssid.upper() in selected
        )
        if len(targets) != 1:
            return False
        target = targets[0]
        if not target.channel:
            return False
        session_id = uuid4().hex
        cancel_event = self.task_controller.begin(
            CaptureTaskType.HANDSHAKE.value,
            session_id,
            CaptureSessionState.STARTING,
        )
        if cancel_event is None:
            return False
        options = dict(settings)
        self._stash_privacy_mask(settings)
        self._stash_result_prefs(options)
        deauth_on = bool(options.get("active_deauth", False))
        self._handshake_active_deauth = deauth_on
        mode_text = "主动断连" if deauth_on else "被动等待"
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            access_points=self._set_capture_state(
                self.openwrt_backend.snapshot.access_points,
                target.bssid,
                AccessPointCaptureState.PARTIAL,
            ),
            progress=CaptureProgress(
                f"正在定向抓取握手（{mode_text}）",
                0,
                0,
                "正在创建临时 monitor 接口。",
                True,
            ),
            last_error="",
            capturing_bssids=(target.bssid.upper(),),
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        target_name = self._masked_target(target.ssid, target.bssid)
        idle = options.get("idle_timeout_seconds", 180) or 180
        client_note = "当前无客户端时先等待，后续发现仍可能发送去认证帧。" if deauth_on else ""
        self.messageGenerated.emit(
            f"开始对 {target_name}（{target.band} 信道 {target.channel}）定向采集："
            f"{mode_text}，限时约 {idle} 秒。{client_note}",
            "info",
        )
        self._start_openwrt_worker(
            session_id,
            lambda event, progress, message: self.openwrt_backend.capture_handshake_passive(
                session_id,
                options,
                target,
                event,
                progress,
                message,
            ),
            cancel_event,
            self._on_openwrt_handshake_succeeded,
            lambda summary, detail, bssid=target.bssid: self._on_openwrt_handshake_failed(
                summary,
                detail,
                bssid,
            ),
            self._on_openwrt_handshake_progress,
            self.messageGenerated.emit,
        )
        return True

    def probe_openwrt_takeover_async(self, settings, selected_bssids=()):
        """在后台只读普查定向目标的射频占用，供主线程按新鲜结果确认接管。

        返回 probe_id（空串表示不能启动）。普查占用唯一任务槽、不改目标状态、
        不创建接口；完成/失败/取消都经 takeoverProbeFinished 回主线程，调用方凭
        probe_id 认领，过期结果直接丢弃。本方法不创建弹窗。
        """

        if self._shutting_down or self.task_controller.busy:
            return ""
        if self.openwrt_backend.snapshot.connection_state != OpenWrtConnectionState.CONNECTED:
            return ""
        selected = {str(value).upper() for value in selected_bssids}
        targets = tuple(
            point
            for point in self.openwrt_backend.snapshot.access_points
            if point.bssid.upper() in selected and point.channel
        )
        if not targets:
            return ""
        probe_id = uuid4().hex
        cancel_event = self.task_controller.begin(
            CaptureTaskType.HANDSHAKE.value,
            probe_id,
            CaptureSessionState.STARTING,
        )
        if cancel_event is None:
            return ""
        options = dict(settings)
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            progress=CaptureProgress(
                "正在检测射频占用", 0, 0, "只读普查，不修改路由器配置。", True,
            ),
            last_error="",
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        self._takeover_probe_id = probe_id
        self._start_openwrt_worker(
            probe_id,
            lambda event: self.openwrt_backend.probe_handshake_takeover(
                targets, options, event,
            ),
            cancel_event,
            lambda entries, pid=probe_id, ev=cancel_event: self._on_takeover_probe_succeeded(
                pid, entries, ev.is_set(),
            ),
            lambda summary, detail, pid=probe_id, ev=cancel_event: self._on_takeover_probe_failed(
                pid, summary, ev.is_set(),
            ),
        )
        return probe_id

    def _on_takeover_probe_succeeded(self, probe_id, entries, cancelled=False):
        """预检完成：释放任务槽，新鲜结果经信号回主线程，过期/取消直接丢弃。"""

        self.task_controller.finish()
        if probe_id != self._takeover_probe_id:
            return
        self._takeover_probe_id = ""
        if cancelled:
            self.takeoverProbeFinished.emit(probe_id, (), "已取消", True)
            return
        self.takeoverProbeFinished.emit(probe_id, tuple(entries or ()), "", False)

    def _on_takeover_probe_failed(self, probe_id, summary, cancelled):
        """预检失败/取消：释放任务槽，不碰连接快照，只把原因带回主线程。"""

        self.task_controller.finish()
        if probe_id != self._takeover_probe_id:
            return
        self._takeover_probe_id = ""
        if cancelled:
            self.takeoverProbeFinished.emit(probe_id, (), "已取消", True)
            return
        self.takeoverProbeFinished.emit(probe_id, (), str(summary or "射频占用检测失败"), False)

    def capture_openwrt_handshakes_async(self, settings, selected_bssids=()):
        """在后台对多个已扫描目标逐个执行定向任务，最多循环指定轮数。

        返回 True 表示任务已启动。目标来自最近一次 S 快照，至少选择一个；去认证
        与接管与单目标共用同一套已确认开关。本方法不创建弹窗。产物按目标分别标记。
        """

        if self._shutting_down or self.task_controller.busy:
            return False
        if self.openwrt_backend.snapshot.connection_state != OpenWrtConnectionState.CONNECTED:
            return False
        selected = {str(value).upper() for value in selected_bssids}
        targets = tuple(
            point
            for point in self.openwrt_backend.snapshot.access_points
            if point.bssid.upper() in selected and point.channel
        )
        if not targets:
            return False
        session_id = uuid4().hex
        cancel_event = self.task_controller.begin(
            CaptureTaskType.HANDSHAKE.value,
            session_id,
            CaptureSessionState.STARTING,
        )
        if cancel_event is None:
            return False
        options = dict(settings)
        self._stash_privacy_mask(settings)
        self._stash_result_prefs(options)
        names = [self._masked_target(point.ssid, point.bssid) for point in targets[:3]]
        scope_text = "、".join(names)
        if len(targets) > 3:
            scope_text += f"等 {len(targets)} 个目标"
        self._handshake_batch_targets = tuple(point.bssid.upper() for point in targets)
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            access_points=tuple(
                point.with_selected(point.bssid.upper() in selected)
                for point in self.openwrt_backend.snapshot.access_points
            ),
            progress=CaptureProgress(
                f"准备多目标抓取（{len(targets)} 个目标）",
                0,
                0,
                "正在创建临时 monitor 接口。",
                True,
            ),
            last_error="",
            capturing_bssids=(targets[0].bssid.upper(),),
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        if bool(options.get("handshake_rounds_infinite", False)):
            rounds_text = "不限轮数（抓到可用包、连续两轮确认无进展或任务失败时停止）"
        else:
            try:
                rounds_value = max(1, min(int(options.get("handshake_rounds", _MAX_HANDSHAKE_ROUNDS)), 99))
            except (TypeError, ValueError):
                rounds_value = _MAX_HANDSHAKE_ROUNDS
            rounds_text = f"最多 {rounds_value} 轮"
        mode_text = (
            "主动断连（当前无客户端时先等待，后续发现仍可能发送去认证帧）"
            if bool(options.get("active_deauth", False)) else "被动等待"
        )
        self.messageGenerated.emit(
            f"开始多目标定向采集：{scope_text}，{rounds_text}，{mode_text}。",
            "info",
        )
        self._start_openwrt_worker(
            session_id,
            lambda event, progress, message: self.openwrt_backend.capture_handshakes_passive(
                session_id,
                options,
                targets,
                event,
                progress,
                message,
                target_callback=progress,
                group_same_channel=True,
            ),
            cancel_event,
            self._on_openwrt_handshakes_succeeded,
            lambda summary, detail: self._on_openwrt_handshakes_failed(
                summary, detail, tuple(point.bssid for point in targets)
            ),
            self._on_openwrt_handshakes_progress,
            self._on_openwrt_handshakes_message,
        )
        return True

    def _merge_openwrt_handshake_results(self, batch):
        """合并批次/增量产物与状态，返回说明和有效目标数；不释放槽或发终局通知。"""

        access_points = self.openwrt_backend.snapshot.access_points
        artifacts = self.openwrt_backend.snapshot.artifacts
        lines = []
        good = 0
        for entry in batch.retained_results + batch.results:
            name = self._masked_target(entry.target.ssid, entry.target.bssid)
            if entry.result is not None and entry.result.verdict in _CONVERTIBLE_VERDICTS:
                good += 1
                access_points = self._set_capture_state(
                    access_points, entry.target.bssid, AccessPointCaptureState.CAPTURED
                )
                # 原始包被按设置删除时路径为空，不建空路径产物。
                if entry.result.cap_path:
                    artifacts = artifacts + (
                        CaptureArtifact(
                            "handshake_cap",
                            entry.result.cap_path,
                            bssid=entry.target.bssid,
                            ssid=entry.target.ssid,
                            verified=True,
                        ),
                    )
                if entry.result.hc22000_path:
                    artifacts = artifacts + (
                        CaptureArtifact(
                            "handshake_22000",
                            entry.result.hc22000_path,
                            bssid=entry.target.bssid,
                            ssid=entry.target.ssid,
                            verified=True,
                        ),
                    )
                if entry.result.hccapx_path:
                    artifacts = artifacts + (
                        CaptureArtifact(
                            "handshake_hccapx",
                            entry.result.hccapx_path,
                            bssid=entry.target.bssid,
                            ssid=entry.target.ssid,
                            verified=True,
                        ),
                    )
                made = []
                if entry.result.hc22000_path:
                    made.append("22000")
                if entry.result.hccapx_path:
                    made.append("HCCAPX")
                conv_note = f"已生成 {'、'.join(made)}。" if made else ""
                # 有转换产物、无原始 CAP，说明原始包已按“保留原始”设置删除。
                converted = entry.result.hc22000_path or entry.result.hccapx_path
                raw_note = (
                    "原始包已按“保留原始”设置删除，只留转换产物。"
                    if converted and not entry.result.cap_path
                    else ""
                )
                lines.append(f"{name}：{entry.result.verdict_detail}{conv_note}{raw_note}")
                # 可跑包结论记入已抓名单，重扫后状态列继续显示。
                previous = self._verified_history.get(entry.target.bssid.upper(), {})
                if any(previous.get(key) != value for key, value in (
                    ("cap_path", entry.result.cap_path), ("hc22000_path", entry.result.hc22000_path),
                    ("hccapx_path", entry.result.hccapx_path),
                )):
                    self.record_verified_result(
                        entry.target.bssid, entry.target.ssid, entry.result.cap_path,
                        entry.result.hc22000_path, entry.result.hccapx_path,
                    )
            elif entry.result is not None and entry.result.verdict == "none":
                access_points = self._set_capture_state(
                    access_points, entry.target.bssid, AccessPointCaptureState.NO_RESULT
                )
                lines.append(f"{name}：{entry.result.verdict_detail or '无有效结果，未保留文件。'}")
            elif entry.result is not None and entry.result.verdict == "partial":
                # 有 EAPOL 但凑不出可跑包组合：后端已决定不落盘，这里只标
                # “有反应、值得再试”，不再创建空路径产物，避免结果目录堆积
                # 无意义文件；verdict_detail 结尾已说明“未保留文件”。
                access_points = self._set_capture_state(
                    access_points, entry.target.bssid, AccessPointCaptureState.PARTIAL
                )
                lines.append(f"{name}：{entry.result.verdict_detail}")
            elif entry.result is not None:
                access_points = self._set_capture_state(
                    access_points, entry.target.bssid, AccessPointCaptureState.PARTIAL
                )
                # 开关关闭导致 unknown 未保留时路径为空，不建空路径产物。
                if entry.result.cap_path:
                    artifacts = artifacts + (
                        CaptureArtifact(
                            "handshake_cap",
                            entry.result.cap_path,
                            bssid=entry.target.bssid,
                            ssid=entry.target.ssid,
                            verified=False,
                        ),
                    )
                    lines.append(f"{name}：{entry.result.verdict_detail}")
                else:
                    suffix = ("按设置未保留文件。"
                              if not str(entry.result.verdict_detail).endswith("未保留文件。") else "")
                    lines.append(f"{name}：{entry.result.verdict_detail}{suffix}")
            else:
                access_points = self._set_capture_state(
                    access_points,
                    entry.target.bssid,
                    AccessPointCaptureState.PENDING
                    if batch.cancelled
                    else AccessPointCaptureState.FAILED,
                )
                lines.append(f"{name}：{entry.error or '未完成'}")
            if entry.stop_reason == "no_progress":
                access_points = self._set_capture_state(
                    access_points, entry.target.bssid, AccessPointCaptureState.NO_PROGRESS)
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            access_points=access_points,
            artifacts=tuple(dict.fromkeys(artifacts)),
            capturing_bssids=tuple(
                bssid for bssid in self.openwrt_backend.snapshot.capturing_bssids
                if str(bssid).upper() not in {entry.target.bssid.upper() for entry in batch.results}),
            # 任务中途已置位的沿用；本批有可跑包结论的兜底置位。
            has_verified_result=self.openwrt_backend.snapshot.has_verified_result or good > 0,
            last_error="",
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        return lines, sum(entry.result is not None and entry.result.verdict in _CONVERTIBLE_VERDICTS
                          for entry in batch.results)

    def _on_openwrt_handshakes_succeeded(self, batch):
        """先保留全部产物，再区分正常结束、取消和清理失败；取消不弹框或开目录。"""

        cancelled = batch.cancelled or self.openwrt_snapshot.task_state == "stopping"
        batch = replace(batch, cancelled=cancelled)
        lines, good = self._merge_openwrt_handshake_results(batch)
        cancelled = cancelled or self.openwrt_snapshot.task_state == "stopping"
        errors = tuple(dict.fromkeys(batch.cleanup_errors))
        detail = "；".join(filter(None, (batch.note, *(error for error in errors if error not in batch.note))))
        failed = (batch.status not in {"completed", "cancelled"} or bool(errors)
                  or any(entry.error or entry.error_code for entry in batch.results))
        if failed:
            self.openwrt_backend.snapshot = self._task_failure_snapshot(detail)
        self._handshake_batch_targets = ()
        self.openwrt_backend.snapshot = replace(
            self.openwrt_snapshot, cleanup_errors=errors, capturing_bssids=(),
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_snapshot)
        total = max(batch.planned_total, len(batch.results))
        stalled = sum(entry.stop_reason == "no_progress" for entry in batch.results)
        unknown = any(entry.result is not None and entry.result.verdict == "unknown"
                      for entry in batch.results)
        ending = ("已取消" if cancelled else "失败" if failed else "结束" if good else
                  "结束，部分内容尚未验证" if unknown else "无有效结果，已结束")
        head = f"多目标定向采集{ending}：{good}/{total} 个已出可跑包结果。" + detail
        unfinished = total - len(batch.results)
        if unfinished:
            head += f"；{unfinished} 个目标尚未返回完整结果。"
        if stalled:
            head += f"{stalled} 个目标无进展，已停止重复采集。"
        self.messageGenerated.emit(
            head + ("\n" + "\n".join(lines) if lines else ""),
            "error" if failed else "info" if cancelled else "success" if good else "warning",
        )
        # 弹窗按目标数裁剪：超过 8 个只放前 8 条，其余看信息窗。
        shown = lines[:8]
        if len(lines) > len(shown):
            shown = shown + [f"等共 {len(lines)} 个目标，详情见信息窗。"]
        if not cancelled:
            self._notify_task_result(
                "定向抓包结果", head + ("\n" + "\n".join(shown) if shown else "")
            )
        # 至少一个目标有文件落盘（原始 CAP、22000 或 HCCAPX）才考虑自动打开目录。
        produced = any(
            entry.result is not None
            and (
                entry.result.cap_path
                or entry.result.hc22000_path
                or entry.result.hccapx_path
            )
            for entry in batch.results
        )
        if not cancelled and not failed:
            self._maybe_auto_open_dir(produced)
        self.task_controller.finish(failed=failed)

    def _on_openwrt_handshakes_failed(self, summary, detail, target_bssids):
        """整批灾难性失败时保留连接，按取消与否标记全部目标。"""

        snapshot = self._task_failure_snapshot(summary)
        cancelled = "已取消" in str(summary) or "SSH 连接已断开" in str(summary)
        wanted = set(str(value).upper() for value in target_bssids)
        access_points = tuple(
            replace(
                point,
                capture_state=AccessPointCaptureState.PENDING
                if cancelled
                else AccessPointCaptureState.FAILED,
            )
            if point.bssid.upper() in wanted
            else point
            for point in snapshot.access_points
        )
        level = "info" if cancelled else "error"
        snapshot = replace(snapshot, access_points=access_points, capturing_bssids=())
        self._handshake_batch_targets = ()
        self.openwrt_backend.snapshot = snapshot
        self.openwrtSnapshotChanged.emit(snapshot)
        self.messageGenerated.emit(f"多目标定向任务未完成：{summary}", level)
        if level != "info":
            self._notify_task_result("定向抓包结果", f"多目标定向任务未完成：{summary}")
        self.task_controller.finish(failed=level == "error")

    def capture_openwrt_password_async(self, settings, selected_bssids=()):
        """在后台对已选目标逐个在线试密码，高风险，调用前页面必须已确认。

        返回 True 表示任务已启动。字典路径为空直接提示不启动。
        产物只标记已找到密码的结果文件，密码内容永不进快照或消息。
        """

        if self._shutting_down or self.task_controller.busy:
            return False
        if self.openwrt_backend.snapshot.connection_state != OpenWrtConnectionState.CONNECTED:
            return False
        selected = {str(value).upper() for value in selected_bssids}
        targets = tuple(
            point
            for point in self.openwrt_backend.snapshot.access_points
            if point.bssid.upper() in selected
        )
        if not targets:
            return False
        if not dictionary_paths(settings):
            self.messageGenerated.emit("密码字典未设置，请先在抓包设置中选择字典文件。", "warning")
            return False
        session_id = uuid4().hex
        cancel_event = self.task_controller.begin(
            CaptureTaskType.PASSWORD_VERIFY.value,
            session_id,
            CaptureSessionState.STARTING,
        )
        if cancel_event is None:
            return False
        options = dict(settings)
        self._stash_privacy_mask(settings)
        self._stash_result_prefs(options)
        names = [self._masked_target(point.ssid, point.bssid) for point in targets[:3]]
        scope_text = "、".join(names)
        if len(targets) > 3:
            scope_text += f"等 {len(targets)} 个目标"
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            progress=CaptureProgress(
                "准备在线验证",
                0,
                0,
                "正在创建临时工作目录。",
                True,
            ),
            last_error="",
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        self.messageGenerated.emit(
            f"开始在线密码验证：{scope_text}。路由器无线配置会被临时修改，结束自动恢复。",
            "info",
        )
        self._start_openwrt_worker(
            session_id,
            lambda event, progress, message, control=self.task_controller.pause_control: self.openwrt_backend.verify_passwords(
                session_id,
                options,
                targets,
                event,
                progress,
                message,
                pause_control=control,
            ),
            cancel_event,
            self._on_openwrt_password_succeeded,
            lambda summary, detail: self._on_openwrt_password_failed(summary, detail),
            self._on_openwrt_password_progress,
            self._on_openwrt_password_message,
        )
        return True

    def _on_openwrt_password_succeeded(self, packed):
        """汇总验证结局：成功只报结果文件路径，密码永不进消息。"""

        results, restore_errors = packed
        if self.openwrt_backend.snapshot.task_state == CaptureSessionState.STOPPING.value:
            summary = "在线密码验证已取消。"
            if restore_errors:
                summary += f"配置恢复异常：{restore_errors}"
            self._on_openwrt_password_failed(summary, "")
            return
        artifacts = self.openwrt_backend.snapshot.artifacts
        good = 0
        for entry in results:
            if entry.found:
                good += 1
                artifacts = artifacts + (
                    CaptureArtifact(
                        "password_result",
                        entry.password_file,
                        bssid=entry.bssid,
                        ssid=entry.ssid,
                        verified=True,
                    ),
                )
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            artifacts=artifacts,
            has_verified_result=self.openwrt_backend.snapshot.has_verified_result or good > 0,
            last_error="",
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        total = len(results)
        skipped = sum(not entry.found and entry.tried == 0 for entry in results)
        head = (
            f"在线密码验证结束：共 {total} 个目标，找到 {good} 个，"
            f"未找到 {total - good - skipped} 个，跳过 {skipped} 个。"
        )
        if restore_errors:
            head += f"配置恢复异常：{restore_errors}"
        self.messageGenerated.emit(head, "success" if good else "warning")
        # 弹窗只报文件路径，密码永不出现。
        found_files = [entry.password_file for entry in results if entry.found][:5]
        notice = head
        if found_files:
            notice += "\n密码已写入：\n" + "\n".join(found_files)
            if good > len(found_files):
                notice += f"\n等共 {good} 个文件，详情见信息窗。"
        self._notify_task_result("猜密码结果", notice)
        self._maybe_auto_open_dir(good > 0)
        self.task_controller.finish(failed=not good)

    def _on_openwrt_password_failed(self, summary, detail):
        """验证失败保留连接；取消与 SSH 中断按 info 处理。"""

        snapshot = self._task_failure_snapshot(summary)
        self.openwrt_backend.snapshot = snapshot
        self.openwrtSnapshotChanged.emit(snapshot)
        level = "info" if "已取消" in str(summary) or "SSH 连接已断开" in str(summary) else "error"
        self.messageGenerated.emit(f"在线密码验证未完成：{summary}", level)
        if level != "info":
            self._notify_task_result("猜密码结果", f"在线密码验证未完成：{summary}")
        self.task_controller.finish(failed=level == "error")

    def _on_openwrt_password_progress(self, update):
        """发布逐密码进度：标题带第几个目标与第几个候选。"""

        snapshot = self.openwrt_backend.snapshot
        if (snapshot.task_type != CaptureTaskType.PASSWORD_VERIFY
                or snapshot.task_state == CaptureSessionState.STOPPING.value):
            return
        if snapshot.task_state == CaptureSessionState.STARTING.value:
            self.task_controller.set_state(CaptureSessionState.CAPTURING)
            snapshot = self.openwrt_backend.snapshot
        total_targets = max(1, int(update.target_total or 1))
        index = min(max(0, int(update.target_index or 0)), total_targets - 1)
        label = str(update.target_label or "")
        title = f"正在验证第{index + 1}/{total_targets}个"
        if label:
            title += f"：{label}"
        total = max(1, int(update.total or 0))
        tried = min(max(0, int(update.tried or 0)), total)
        self.openwrt_backend.snapshot = replace(
            snapshot,
            progress=CaptureProgress(
                title,
                tried,
                total,
                f"已试 {tried}/{total} 个候选。",
                False,
            ),
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)

    def _on_openwrt_password_message(self, text, level):
        """\u8f6c\u53d1\u539f\u59cb\u6807\u8bc6\uff0c\u4e0d\u63a5\u6536\u5bc6\u7801\u5185\u5bb9\u3002"""
        self.messageGenerated.emit(str(text), str(level))

    def capture_openwrt_wps_async(self, settings, selected_bssids=(), mode="second"):
        """在后台对已选目标逐个跑 Pin，调用前页面必须已确认。

        参数 mode 是 second/normal/offline/combo。返回 True 表示任务已启动。
        PIN 与密码只进结果文件，永不进快照或消息。
        """

        if mode not in ("normal", "second", "offline", "combo"):
            return False
        if self._shutting_down or self.task_controller.busy:
            return False
        if self.openwrt_backend.snapshot.connection_state != OpenWrtConnectionState.CONNECTED:
            return False
        selected = {str(value).upper() for value in selected_bssids}
        targets = tuple(
            point
            for point in self.openwrt_backend.snapshot.access_points
            if point.bssid.upper() in selected
        )
        if not targets:
            return False
        session_id = uuid4().hex
        cancel_event = self.task_controller.begin(
            CaptureTaskType.WPS.value,
            session_id,
            CaptureSessionState.STARTING,
        )
        if cancel_event is None:
            return False
        options = dict(settings)
        self._stash_privacy_mask(settings)
        self._stash_result_prefs(options)
        names = [self._masked_target(point.ssid, point.bssid) for point in targets[:3]]
        scope_text = "、".join(names)
        if len(targets) > 3:
            scope_text += f"等 {len(targets)} 个目标"
        mode_text = {"normal": "常规 Pin", "second": "秒 Pin", "offline": "离线 Pin"}.get(mode, "组合 Pin")
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            progress=CaptureProgress(
                f"准备{mode_text}",
                0,
                0,
                "正在创建临时 monitor 接口。",
                True,
            ),
            last_error="",
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        self.messageGenerated.emit(
            f"开始{mode_text}：{scope_text}。耗时较长，部分路由器会锁定 WPS。",
            "info",
        )
        self._start_openwrt_worker(
            session_id,
            lambda event, progress, message: self.openwrt_backend.run_wps(
                session_id,
                options,
                targets,
                mode,
                event,
                progress,
                message,
            ),
            cancel_event,
            self._on_openwrt_wps_succeeded,
            lambda summary, detail: self._on_openwrt_wps_failed(summary, detail),
            self._on_openwrt_wps_progress,
            self._on_openwrt_wps_message,
        )
        return True

    def _on_openwrt_wps_succeeded(self, results):
        """汇总 Pin 结局：成功只报结果文件路径，PIN 与密码永不进消息。"""

        artifacts = self.openwrt_backend.snapshot.artifacts
        pins = 0
        psks = 0
        for entry in results:
            if entry.psk_ok:
                psks += 1
                pins += 1
            elif entry.pin_ok:
                pins += 1
            if entry.result_file:
                artifacts = artifacts + (
                    CaptureArtifact(
                        "wps_result",
                        entry.result_file,
                        bssid=entry.bssid,
                        ssid=entry.ssid,
                        verified=bool(entry.pin_ok),
                    ),
                )
            if entry.wpc_file:
                artifacts = artifacts + (
                    CaptureArtifact(
                        "wps_wpc",
                        entry.wpc_file,
                        bssid=entry.bssid,
                        ssid=entry.ssid,
                        verified=False,
                    ),
                )
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            artifacts=artifacts,
            has_verified_result=self.openwrt_backend.snapshot.has_verified_result or pins > 0,
            last_error="",
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        total = len(results)
        head = f"WPS 结束：{total} 个目标拿到 {pins} 个 PIN，其中 {psks} 个连带密码。"
        self.messageGenerated.emit(head, "success" if pins else "warning")
        found_files = [entry.result_file for entry in results if entry.result_file][:5]
        notice = head
        if found_files:
            notice += "\n结果已写入：\n" + "\n".join(found_files)
        self._notify_task_result("WPS 结果", notice)
        self._maybe_auto_open_dir(bool(found_files))
        self.task_controller.finish(failed=not pins)

    def _on_openwrt_wps_failed(self, summary, detail):
        """WPS 失败保留连接；取消与 SSH 中断按 info 处理。"""

        snapshot = self._task_failure_snapshot(summary)
        self.openwrt_backend.snapshot = snapshot
        self.openwrtSnapshotChanged.emit(snapshot)
        level = "info" if "已取消" in str(summary) or "SSH 连接已断开" in str(summary) else "error"
        self.messageGenerated.emit(f"WPS 任务未完成：{summary}", level)
        if level != "info":
            self._notify_task_result("WPS 结果", f"WPS 任务未完成：{summary}")
        self.task_controller.finish(failed=level == "error")

    def _on_openwrt_wps_progress(self, update):
        """发布逐目标 Pin 进度：标题带第几个目标与阶段。"""

        snapshot = self.openwrt_backend.snapshot
        if snapshot.task_type != CaptureTaskType.WPS:
            return
        if snapshot.task_state == CaptureSessionState.STARTING.value:
            self.task_controller.set_state(CaptureSessionState.CAPTURING)
            snapshot = self.openwrt_backend.snapshot
        total_targets = max(1, int(update.target_total or 1))
        index = min(max(0, int(update.target_index or 0)), total_targets - 1)
        label = str(update.target_label or "")
        title = f"正在 Pin 第{index + 1}/{total_targets}个"
        if label:
            title += f"：{label}"
        phase = str(update.phase or "")
        if phase:
            title += f"（{phase}）"
        total = max(1, int(update.total or 0))
        elapsed = min(max(0, int(update.elapsed or 0)), total)
        self.openwrt_backend.snapshot = replace(
            snapshot,
            progress=CaptureProgress(
                title,
                elapsed,
                total,
                f"已跑 {elapsed}/{total} 秒。",
                False,
            ),
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)

    def _on_openwrt_wps_message(self, text, level):
        """把逐目标 Pin 播报转发到常规页信息窗，属于任务过程消息。"""

        self.messageGenerated.emit(str(text), str(level))

    def _on_openwrt_handshakes_progress(self, update):
        """发布多目标进度：标题带第 X/Y 个与轮次。"""

        snapshot = self.openwrt_backend.snapshot
        if snapshot.task_type != CaptureTaskType.HANDSHAKE:
            return
        if isinstance(update, HandshakeTargetResult):
            self._merge_openwrt_handshake_results(
                HandshakeBatchResult(snapshot.task_state == "stopping", (update,)))
            return
        if snapshot.task_state == "stopping":
            return
        if snapshot.task_state == CaptureSessionState.STARTING.value:
            self.task_controller.set_state(CaptureSessionState.CAPTURING)
            snapshot = self.openwrt_backend.snapshot
        total = max(1, int(update.target_total or 1))
        index = min(max(0, int(update.target_index or 0)), total - 1)
        label = str(update.target_label or "")
        current_bssid = str(getattr(update, "target_bssid", "") or "").strip().upper()
        group_bssids = tuple(str(key).upper() for key in getattr(update, 'target_bssids', ()) or ())
        capturing = group_bssids or ((current_bssid,) if current_bssid else snapshot.capturing_bssids)
        if update.stage != "capturing":
            self.openwrt_backend.snapshot = replace(snapshot, progress=CaptureProgress(
                {"analyzing": "检查握手内容", "saving": "转换并保存结果", "cleaning": "清理临时资源"}.get(update.stage, "处理结果"),
                0, 0, "正在处理，请等待完成。", True,
            ))
            self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
            return
        if total > 1 or int(update.round_no or 1) > 1:
            title = f"同信道采集 {len(group_bssids)} 个目标" if len(group_bssids) > 1 else f"正在抓第{index + 1}/{total}个"
            if label:
                title += f"：{label}"
            title += f"（第{max(1, int(update.round_no or 1))}轮）"
        else:
            title = "正在定向抓取握手"
        remaining = max(0, int(update.total) - int(update.elapsed))
        self.openwrt_backend.snapshot = replace(
            snapshot,
            progress=CaptureProgress(
                title,
                int(update.elapsed),
                int(update.total),
                f"剩余约 {remaining} 秒。",
                False,
            ),
            capturing_bssids=capturing,
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)

    def _on_openwrt_handshakes_message(self, text, level):
        """把多目标逐目标播报转发到常规页信息窗，属于任务过程消息。

        后端保证 success 级别与“该目标已出可跑包结论、可用文件已落盘”一一对应，
        因此见到它就把会话级已出可用包标记置位，让取结果入口在任务结束前变绿。
        """

        if self.openwrt_snapshot.task_state == "stopping" and str(level) == "success":
            level = "info"
        self.messageGenerated.emit(str(text), str(level))
        if str(level) != "success":
            return
        snapshot = self.openwrt_backend.snapshot
        if snapshot.task_type != CaptureTaskType.HANDSHAKE:
            return
        if snapshot.has_verified_result:
            return
        self.openwrt_backend.snapshot = replace(snapshot, has_verified_result=True)
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)

    def cancel_openwrt_task(self):
        """请求当前 OpenWrt worker 正常清理并停止，不强制终止 QThread。

        返回 True 表示已发出取消请求。副作用：任务状态进入 STOPPING；worker 会在 finally
        中停止登记 PID、删除临时 monitor 和远端工作目录。
        """

        return self.task_controller.cancel()

    def reject_openwrt_fingerprint(self):
        """用户拒绝首次指纹时恢复未连接快照，不启动网络操作。"""

        self._pending_fingerprint_request = None
        self._openwrt_password = ""
        snapshot = self.openwrt_backend.disconnect()
        self.openwrtSnapshotChanged.emit(snapshot)
        self.settingsMessageGenerated.emit("已取消连接，未信任 OpenWrt 主机指纹。", "warning")

    def _start_openwrt_worker(
        self,
        session_id,
        operation,
        cancel_event,
        success_slot,
        failure_slot=None,
        progress_slot=None,
        message_slot=None,
    ):
        """创建服务所有的 QThread/worker，并集中连接完成与清理信号。"""

        from .task_completion import start_capture_task
        start_capture_task(
            self, session_id, operation, cancel_event,
            success_slot, failure_slot or self._on_openwrt_worker_failed,
            progress_slot, message_slot,
        )

    def _on_openwrt_connect_progress(self, progress_text):
        """主线程接收连接子阶段进度通知并更新快照。"""
        if self.openwrt_backend.snapshot.connection_state == OpenWrtConnectionState.CONNECTING:
            self.openwrt_backend.snapshot = replace(
                self.openwrt_backend.snapshot,
                connection_detail=str(progress_text),
            )
            self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)

    def _on_openwrt_connect_succeeded(self, result):
        """在主线程处理连接结果；两种指纹确认都不视为失败。"""

        if result.status in {"fingerprint_required", "fingerprint_changed"}:
            self._pending_fingerprint_request = result
            self.openwrt_backend.snapshot = replace(
                self.openwrt_backend.snapshot,
                connection_state=(
                    OpenWrtConnectionState.FINGERPRINT_CHANGED
                    if result.status == "fingerprint_changed"
                    else OpenWrtConnectionState.FINGERPRINT_REQUIRED
                ),
                connection_detail=result.detail,
                host_fingerprint=result.fingerprint,
            )
            self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
            # 先释放当前连接任务，再让页面弹窗。Qt 同线程信号默认直接调用槽函数；
            # 如果反过来，用户确认后立即重连会被仍占用的任务互斥器拒绝。
            self.task_controller.finish()
            self.openwrtFingerprintRequired.emit(result)
            return
        self._pending_fingerprint_request = None
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            connection_state=OpenWrtConnectionState.CONNECTED,
            connection_detail=result.detail,
            host_fingerprint=result.fingerprint,
            device=result.device,
            dependencies=result.dependencies,
            last_error="",
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        self.settingsMessageGenerated.emit("OpenWrt 路由器登录成功，设备和依赖检测已完成。", "success")
        self.task_controller.finish()
        # 登录成功后自动刷新信号管理列表，无需用户手动点击。
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self.router_signals.refresh)
        except Exception:
            pass

    def _on_openwrt_disconnect_succeeded(self, snapshot):
        """在主线程回放断开后的初始状态。"""

        self._pending_fingerprint_request = None
        self._openwrt_password = ""
        self.openwrtSnapshotChanged.emit(snapshot)
        self.settingsMessageGenerated.emit("已断开 OpenWrt 路由器。", "info")
        self.task_controller.finish()

    def _on_openwrt_scan_succeeded(self, result):
        """将后台解析出的 AP 快照发布给所有页面。

        扫描 CSV 只是会话临时目录中的中间文件（成功后会被清理），表格才是扫描产物，
        因此不再将其记为持久产物，避免产物路径指向已清理的临时文件。
        """

        existing_selection = {item.bssid.upper() for item in self.openwrt_backend.snapshot.access_points if item.selected}
        # 重扫回显：历史上已抓出可用包的 BSSID 直接标“已有可用包”，勾选保留逻辑不变。
        access_points = tuple(
            self._apply_verified_history(point, existing_selection)
            for point in result.access_points
        )
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            access_points=access_points,
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        if access_points:
            self.messageGenerated.emit(
                f"Wi-Fi 扫描完成，发现 {len(access_points)} 个信号。",
                "success",
            )
            # 逐条播报扫描到的信号明细（与 AP 表同一口径），方便用户不翻表格
            # 就能在信息窗确认每个信号的名称、强度、信道、加密和客户端数。
            for position, point in enumerate(access_points, start=1):
                name = point.ssid or "<隐藏网络>"
                power = "未知" if point.power is None else str(point.power)
                channel = f" 信道{point.channel}" if point.channel else ""
                encryption = f" {point.encryption}" if point.encryption else ""
                clients = f" · {len(point.clients)}个客户端" if point.clients else ""
                wps = " · WPS" if point.wps_supported else ""
                bssid_text = point.bssid
                self.messageGenerated.emit(
                    f"{position}. {name} {bssid_text} 信号{power}{channel}{encryption}{clients}{wps}",
                    "info",
                )
        else:
            self.messageGenerated.emit(
                "Wi-Fi 扫描完成，但没有发现信号。建议增加扫描时间、确认频段，或检查路由器无线网卡状态。",
                "warning",
            )
        self.task_controller.finish()

    def _on_openwrt_scan_progress(self, update):
        """在 Qt 主线程发布倒计时和增量 AP 结果，不让 worker 直接修改页面。"""

        snapshot = self.openwrt_backend.snapshot
        if snapshot.task_type != CaptureTaskType.WIFI_SCAN:
            return
        if isinstance(update, VerifiedResultCheck):
            self.verified_results.apply(update)
            return
        if snapshot.task_state == CaptureSessionState.STARTING.value:
            self.task_controller.set_state(CaptureSessionState.CAPTURING)
            snapshot = self.openwrt_backend.snapshot
        existing_selection = {point.bssid.upper() for point in snapshot.access_points if point.selected}
        access_points = snapshot.access_points
        if update.access_points:
            access_points = tuple(
                self._apply_verified_history(point, existing_selection)
                for point in update.access_points
            )
        remaining = max(0, int(update.total) - int(update.elapsed))
        self.openwrt_backend.snapshot = replace(
            snapshot,
            access_points=access_points,
            progress=CaptureProgress(
                "正在扫描 Wi-Fi",
                int(update.elapsed),
                int(update.total),
                f"剩余约 {remaining} 秒，已发现 {len(access_points)} 个信号。",
                False,
            ),
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)

    def _on_openwrt_scan_failed(self, summary, detail):
        """扫描失败不关闭已验证的 SSH 会话，保留失败原因便于用户重试。"""

        self.openwrt_backend.snapshot = self._task_failure_snapshot(summary)
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        level = "info" if "已取消" in str(summary) else "error"
        self.messageGenerated.emit(f"Wi-Fi 扫描未完成：{summary}", level)
        self.task_controller.finish(failed=level == "error")

    def _on_openwrt_pmkid_progress(self, update):
        """在 Qt 主线程发布 PMKID 有限时长进度。"""

        snapshot = self.openwrt_backend.snapshot
        if snapshot.task_type not in {
            CaptureTaskType.PMKID_GLOBAL,
            CaptureTaskType.PMKID_FILTER,
        }:
            return
        if snapshot.task_state == CaptureSessionState.STARTING.value:
            self.task_controller.set_state(CaptureSessionState.CAPTURING)
            snapshot = self.openwrt_backend.snapshot
        if snapshot.task_state == CaptureSessionState.STOPPING.value and not getattr(update, "note", ""):
            return
        remaining = max(0, int(update.total) - int(update.elapsed))
        unlimited = int(update.total) <= 0
        elapsed = max(0, int(update.elapsed))
        size = getattr(update, "captured_bytes", None)
        data_note = ("暂未确认收到采集数据。" if size is None else
                     "尚未收到采集数据。" if size <= 0 else
                     f"采集文件 {size / 1024:.1f} KB；是否可用由后台检查确认。")
        note = getattr(update, "note", "")
        if note:
            if snapshot.progress.detail != note:
                self.messageGenerated.emit(note, "info")
        elif elapsed == 0 or elapsed // 30 > int(snapshot.progress.current) // 30:
            self.messageGenerated.emit(f"PMKID 正在采集，已运行 {elapsed} 秒。{data_note}", "info")
        self.openwrt_backend.snapshot = replace(
            snapshot,
            progress=CaptureProgress(
                "正在收尾" if getattr(update,"note","") else "正在采集 PMKID",
                int(update.elapsed),
                int(update.total),
                note or ((f"不限时，已采集 {elapsed} 秒。" if unlimited else f"剩余约 {remaining} 秒。") + data_note),
                unlimited,
            ),
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)

    def _on_openwrt_pmkid_succeeded(self, result):
        """保存 PMKID 原始产物与逐 AP 的 22000，可跑包组合进已抓名单。"""

        cancelled = result.cancelled or self.openwrt_snapshot.task_state == "stopping"
        completed = set(result.completed_bssids)
        converted = {bssid.upper(): (ssid, path) for bssid, ssid, path in result.hc22000_files}
        known = {p.bssid.upper(): p.ssid for p in self.openwrt_snapshot.access_points}
        artifacts = self.openwrt_backend.snapshot.artifacts
        if result.pcapng_path:
            artifacts += (CaptureArtifact("pmkid_pcapng", result.pcapng_path, verified=False),)
        access_points = self.openwrt_snapshot.access_points
        for bssid in sorted(completed):
            ssid, hc_path = converted.get(bssid, (known.get(bssid, ""), ""))
            artifacts += (CaptureArtifact("pmkid_pcapng", result.pcapng_path,
                                          bssid=bssid, ssid=ssid, verified=True),)
            access_points = self._set_capture_state(access_points, bssid, AccessPointCaptureState.CAPTURED)
            self.record_verified_result(bssid, ssid, result.pcapng_path, hc_path)
        for _bssid, _ssid, hc_path in result.hc22000_files:
            artifacts = artifacts + (
                CaptureArtifact(
                    "pmkid_22000", hc_path, bssid=_bssid, ssid=_ssid, verified=True
                ),
            )
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            artifacts=tuple(dict.fromkeys(artifacts)),
            access_points=access_points,
            has_verified_result=self.openwrt_backend.snapshot.has_verified_result
            or bool(completed),
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        # PMKID 原始包是唯一全面证据，开关关闭也删不得，如实说明。
        keep_note = (
            "（“保留原始”已关闭，但 PMKID 原始包仍保留。）"
            if not self._pmkid_keep_raw
            else ""
        )
        convert_note = ""
        if result.hc22000_files:
            convert_note = f"已生成 {len(result.hc22000_files)} 个 22000，可用 hashcat -m 22000 跑包。"
        elif result.hc22000_note:
            convert_note = result.hc22000_note
        early_note = str(result.early_reason or "")
        cancelled = cancelled or self.openwrt_snapshot.task_state == "stopping"
        ending = "已取消" if cancelled else "已完成" if completed else "无有效结果" if result.verdict in {"none", "partial"} else "结束，内容尚未验证"
        end_text = (
            f"PMKID 任务{ending}：{result.verdict_detail}"
            + (f"原始包已保留：{result.pcapng_path}。{keep_note}" if result.pcapng_path else "")
            + f"{convert_note}{early_note}"
        )
        self.messageGenerated.emit(
            end_text, "info" if cancelled else "success" if completed else "warning"
        )
        # 出可跑包组合才弹窗；纯原始包只有信息窗播报，避免打扰。
        if completed and not cancelled:
            self._notify_task_result("PMKID 结果", end_text)
        if not cancelled:
            self._maybe_auto_open_dir(bool(result.pcapng_path or result.hc22000_files))
        self.task_controller.finish()

    def _on_openwrt_pmkid_failed(self, summary, detail):
        """PMKID 失败保留 SSH 会话，并显示可重试的结构化原因。"""

        self.openwrt_backend.snapshot = self._task_failure_snapshot(summary)
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        level = "info" if "已取消" in str(summary) else "error"
        self.messageGenerated.emit(f"PMKID 任务未完成：{summary}", level)
        self.task_controller.finish(failed=level == "error")
        if (level == "error" and self.openwrt_backend.ssh_client.connected
                and ("_WextInitializationError" in detail or
                     ("HcxCompatibilityError" in detail and "\u4e3b\u52a8\u53d1\u5c04" not in summary
                      and ("API" in summary or "\u7248\u672c\u672a\u77e5" in summary)))):
            self.compatibleToolSuggested.emit()

    def _on_openwrt_handshake_progress(self, update):
        """在 Qt 主线程发布 H 模块有限时长进度。"""

        snapshot = self.openwrt_backend.snapshot
        if snapshot.task_type != CaptureTaskType.HANDSHAKE:
            return
        if snapshot.task_state == CaptureSessionState.STARTING.value:
            self.task_controller.set_state(CaptureSessionState.CAPTURING)
            snapshot = self.openwrt_backend.snapshot
        remaining = max(0, int(update.total) - int(update.elapsed))
        deauth_on = bool(getattr(self, "_handshake_active_deauth", False))
        mode_text = "主动断连" if deauth_on else "被动等待"
        tail = (
            "按间隔定向断连中，完成后只保留含可跑包组合的原始 CAP。"
            if deauth_on
            else "当前未执行主动断连，完成后只保留含可跑包组合的原始 CAP。"
        )
        self.openwrt_backend.snapshot = replace(
            snapshot,
            progress=CaptureProgress(
                f"正在定向抓取握手（{mode_text}）",
                int(update.elapsed),
                int(update.total),
                f"剩余约 {remaining} 秒；{tail}",
                False,
            ),
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)

    def _on_openwrt_handshake_succeeded(self, result):
        """保存 H 产物并按 EAPOL 结构结论诚实报告，不虚构有效握手。"""

        cancelled = self.openwrt_snapshot.task_state == "stopping"

        def finish(text, level, produced=False):
            """结果回放可能触发取消，终局统一复核后再发送通知和后置动作。"""
            self.openwrt_backend.snapshot = replace(
                self.openwrt_backend.snapshot, capturing_bssids=(),
            )
            self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
            stopped = cancelled or self.openwrt_snapshot.task_state == "stopping"
            if stopped:
                text = "定向采集已取消；已返回的结果仍保留。" + text
            self.messageGenerated.emit(text, "info" if stopped else level)
            if not stopped and self.openwrt_snapshot.task_state != "stopping":
                self._notify_task_result("定向抓包结果", text)
                if produced and self.openwrt_snapshot.task_state != "stopping":
                    self._maybe_auto_open_dir(True)
            self.task_controller.finish()

        rounds_note = (
            f"期间共发送 {result.deauth_rounds} 轮去认证。" if result.deauth_rounds else ""
        )
        if result.hc22000_path:
            convert_note = (
                f"已生成 22000：{result.hc22000_path}，可用 hashcat -m 22000 跑包。"
            )
        elif result.hc22000_note:
            convert_note = result.hc22000_note
        else:
            convert_note = ""
        if result.hccapx_path:
            hcx_note = (
                f"已生成 HCCAPX：{result.hccapx_path}，可用 hashcat -m 2500 跑包。"
            )
        elif result.hccapx_note:
            hcx_note = result.hccapx_note
        else:
            hcx_note = ""
        if result.verdict in _CONVERTIBLE_VERDICTS:
            artifacts = self.openwrt_backend.snapshot.artifacts
            # 原始包被按设置删除时路径为空，不建空路径产物。
            if result.cap_path:
                artifacts = artifacts + (
                    CaptureArtifact(
                        "handshake_cap",
                        result.cap_path,
                        bssid=result.bssid,
                        ssid=result.ssid,
                        verified=True,
                    ),
                )
            if result.hc22000_path:
                artifacts = artifacts + (
                    CaptureArtifact(
                        "handshake_22000",
                        result.hc22000_path,
                        bssid=result.bssid,
                        ssid=result.ssid,
                        verified=True,
                    ),
                )
            if result.hccapx_path:
                artifacts = artifacts + (
                    CaptureArtifact(
                        "handshake_hccapx",
                        result.hccapx_path,
                        bssid=result.bssid,
                        ssid=result.ssid,
                        verified=True,
                    ),
                )
            self.openwrt_backend.snapshot = replace(
                self.openwrt_backend.snapshot,
                access_points=self._set_capture_state(
                    self.openwrt_backend.snapshot.access_points,
                    result.bssid,
                    AccessPointCaptureState.CAPTURED,
                ),
                artifacts=artifacts,
                has_verified_result=True,
            )
            self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
            # 可跑包结论记入已抓名单，重扫后状态列继续显示。
            self.record_verified_result(
                result.bssid,
                result.ssid,
                result.cap_path,
                result.hc22000_path,
                result.hccapx_path,
            )
            # 可跑包、有转换产物、原始 CAP 为空，说明原始包已按“保留原始”设置删除。
            converted = result.hc22000_path or result.hccapx_path
            raw_note = (
                "原始包已按“保留原始”设置删除，只留转换产物。"
                if converted and not result.cap_path
                else ""
            )
            end_text = f"定向采集结束：{result.verdict_detail}{rounds_note}{convert_note}{hcx_note}{raw_note}"
            finish(end_text, "success", True)
            return
        if result.verdict == "none":
            self.openwrt_backend.snapshot = replace(
                self.openwrt_backend.snapshot,
                access_points=self._set_capture_state(
                    self.openwrt_backend.snapshot.access_points,
                    result.bssid,
                    AccessPointCaptureState.NO_RESULT,
                ),
            )
            self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
            end_text = f"定向采集结束：本次包中未发现 EAPOL，无有效结果，未保留文件。{rounds_note}"
            finish(end_text, "warning")
            return
        if result.verdict == "partial":
            # 有 EAPOL 但凑不出可跑包组合：后端已决定不落盘，只标“有反应、
            # 值得再试”，不建产物；verdict_detail 结尾已说明“未保留文件”。
            self.openwrt_backend.snapshot = replace(
                self.openwrt_backend.snapshot,
                access_points=self._set_capture_state(
                    self.openwrt_backend.snapshot.access_points,
                    result.bssid,
                    AccessPointCaptureState.PARTIAL,
                ),
            )
            self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
            end_text = f"定向采集结束：{result.verdict_detail}{rounds_note}"
            finish(end_text, "warning")
            return
        # 文件格式无法解析：有零散帧才保守保留（不能证明无价值），全空的后端已
        # 自动删除，这里只标状态、不建空路径产物。
        artifacts = self.openwrt_backend.snapshot.artifacts
        if result.cap_path:
            artifacts = artifacts + (
                CaptureArtifact(
                    "handshake_cap",
                    result.cap_path,
                    bssid=result.bssid,
                    ssid=result.ssid,
                    verified=False,
                ),
            )
            end_text = f"定向采集结束：原始包已保留：{result.cap_path}。文件格式无法解析，内容尚未验证。{rounds_note}"
        else:
            suffix = ("按“保留原始”设置未保留文件。"
                      if not str(result.verdict_detail).endswith("未保留文件。") else "")
            end_text = f"定向采集结束：{result.verdict_detail}{suffix}{rounds_note}"
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            access_points=self._set_capture_state(
                self.openwrt_backend.snapshot.access_points,
                result.bssid,
                AccessPointCaptureState.PARTIAL,
            ),
            artifacts=artifacts,
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        finish(end_text, "warning", bool(result.cap_path))

    def _on_openwrt_handshake_failed(self, summary, detail, target_bssid):
        """H 任务失败时保留连接和目标选择，并显示可重试原因。"""

        snapshot = self._task_failure_snapshot(summary)
        level = "info" if "已取消" in str(summary) else "error"
        snapshot = replace(
            snapshot,
            access_points=self._set_capture_state(
                snapshot.access_points,
                target_bssid,
                AccessPointCaptureState.PENDING
                if level == "info"
                else AccessPointCaptureState.FAILED,
            ),
            capturing_bssids=(),
        )
        self.openwrt_backend.snapshot = snapshot
        self.openwrtSnapshotChanged.emit(snapshot)
        self.messageGenerated.emit(f"定向任务未完成：{summary}", level)
        if level != "info":
            self._notify_task_result("定向抓包结果", f"定向任务未完成：{summary}")
        self.task_controller.finish(failed=level == "error")

    @staticmethod
    def _set_capture_state(access_points, bssid, state):
        """返回更新单个目标状态的新 AP 元组，不修改共享不可变模型。"""

        normalized = str(bssid).upper()
        return tuple(
            replace(point, capture_state=state,
                    result_availability="present" if state == AccessPointCaptureState.CAPTURED else "")
            if point.bssid.upper() == normalized
            else point
            for point in access_points
        )

    def _task_failure_snapshot(self, summary):
        """生成任务失败快照；底层连接失效时同步撤销页面的已连接状态。

        参数：summary 是 worker 返回的用户摘要。返回更新后的不可变快照，不发信号。
        副作用：无远程操作；只读取底层 transport 活动状态，防止文件传输超时关闭连接后
        页面仍开放后续动作。
        """

        changes = {"last_error": str(summary)}
        if not self.openwrt_backend.ssh_client.connected:
            changes.update(
                connection_state=OpenWrtConnectionState.DISCONNECTED,
                connection_detail="与 OpenWrt 的连接已断开，请重新登录后再试。",
            )
        return replace(self.openwrt_backend.snapshot, **changes)

    def _on_openwrt_worker_failed(self, summary, detail):
        """把连接/探测异常转换为 FAILED 快照和用户可读提示。"""

        self.openwrt_backend.disconnect()
        self._openwrt_password = ""
        self.openwrt_backend.snapshot = replace(
            self.openwrt_backend.snapshot,
            connection_state=OpenWrtConnectionState.FAILED,
            connection_detail=str(summary),
            last_error=str(summary),
        )
        self.openwrtSnapshotChanged.emit(self.openwrt_backend.snapshot)
        self.settingsMessageGenerated.emit(
            f"路由器连接或登录未完成：{summary}",
            "error",
        )
        self.task_controller.finish(failed=True)

    def _cleanup_openwrt_worker(self, session_id):
        """线程结束后释放服务强引用，页面生命周期不参与该过程。"""

        self._openwrt_workers.pop(str(session_id), None)
        if self._shutting_down and not self._openwrt_workers:
            self.shutdown()
        else:
            self.activityChanged.emit()

    def set_openwrt_password(self, password):
        """仅在当前进程内保存 SSH 密码；不写配置、不记录日志、不发送信号。"""

        self._openwrt_password = str(password or "")

    def openwrt_password(self):
        """返回当前进程内的 SSH 密码，供后续 OpenWrt 后端连接时读取。"""

        return self._openwrt_password

    @staticmethod
    def _openwrt_credential_target(settings):
        """按地址、端口和用户名生成 Windows 凭据键，不包含密码。"""

        host = str(settings.get("host", "")).strip().casefold()
        port = int(settings.get("port", 22))
        user = str(settings.get("user", "root")).strip().casefold()
        return f"RouterManager:OpenWrt:{host}:{port}:{user}"

    def load_saved_openwrt_password(self, settings):
        """读取当前路由器保存的密码并同步到进程内存；未启用时返回空。"""

        if not bool(settings.get("remember_password", False)):
            return ""
        from modules.platform_services.windows_credentials import read_generic_password

        password = read_generic_password(self._openwrt_credential_target(settings))
        self._openwrt_password = password
        return password

    def save_openwrt_password_preference(self, settings):
        """按“记住密码”状态写入或删除 Windows 凭据，不把密码写入 JSON。"""

        from modules.platform_services.windows_credentials import (
            delete_generic_password,
            write_generic_password,
        )

        target = self._openwrt_credential_target(settings)
        if bool(settings.get("remember_password", False)) and self._openwrt_password:
            write_generic_password(target, settings.get("user", "root"), self._openwrt_password)
        else:
            delete_generic_password(target)

    def forget_saved_openwrt_password(self, settings):
        """Delete only the persistent credential; retain the current session password."""

        from modules.platform_services.windows_credentials import delete_generic_password

        delete_generic_password(self._openwrt_credential_target(settings))

    def _set_state(self, state):
        """更新会话状态并通知页面和常驻运行时重新评估活动。"""

        if self.state == state:
            return
        self.state = state
        self.stateChanged.emit(state.value)
        self.activityChanged.emit()

    def _terminal_activity_changed(self):
        """终端状态参与常驻活动与退出回收，不占抓包互斥槽。"""
        if self._shutting_down and not self.router_terminal.active:self.shutdown()
        self.activityChanged.emit()

    def shutdown(self):
        """应用退出时停止所有后端并阻止新的后台探测。

        返回值：无。副作用：请求本机与 OpenWrt 后端停止；后端 stop 必须幂等。
        """

        self._shutting_down = True
        shutdown_event=getattr(self.openwrt_backend,"shutdown_event",None)
        if shutdown_event is not None:
            shutdown_event.set()
        for attribute in ("common_bundle_download", "capture_bundle_download", "common_bundle_export", "capture_bundle_export"):
            bundle = getattr(self, attribute, None)
            if bundle is not None:
                bundle.cancel()
        self.router_management.shutdown()
        self.router_terminal.stop()
        self._probe_cancel.set()
        self.task_controller.cancel()
        if self._openwrt_workers or self.router_terminal.active or self._shutdown_started:
            # 所有模式均先等待 worker 的 finally；不能提前关闭其共享连接。
            return
        self._shutdown_started = True
        from .shutdown_cleanup import finish_backends
        self._shutdown_maintenance.start(lambda cancel: finish_backends(self, cancel))

    def _shutdown_activity_changed(self):
        """后台停止完成后才发布退出就绪，不在 GUI 线程等待。"""
        if self._shutdown_maintenance.active:
            self.activityChanged.emit()
            return
        self._openwrt_password = ""
        self.state = CaptureSessionState.IDLE
        self.activityChanged.emit()
