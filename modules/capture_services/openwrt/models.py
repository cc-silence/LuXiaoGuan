"""OpenWrt 抓包页面和后端共享的不可变数据模型。"""

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional, Tuple


class CaptureCleanupError(RuntimeError):
    """主体错误与清理错误并存，供扫描/捕获及编排器传递结构化清理信息。"""

    def __init__(self, message, cleanup_errors, primary_error=None):
        super().__init__(message)
        self.cleanup_errors = tuple(dict.fromkeys(cleanup_errors))
        self.primary_message = str(primary_error) if primary_error is not None else str(message)
        self.primary_error = primary_error if isinstance(primary_error, BaseException) else None


class CaptureTaskType(str, Enum):
    """OpenWrt 后端支持的互斥任务类型。"""

    NONE = "none"
    CONNECT = "connect"
    DEVICE_PROBE = "device_probe"
    DEPENDENCY_CHECK = "dependency_check"
    DEPENDENCY_INSTALL = "dependency_install"
    DEPENDENCY_UNINSTALL = "dependency_uninstall"
    WIFI_SCAN = "wifi_scan"
    PMKID_GLOBAL = "pmkid_global"
    PMKID_FILTER = "pmkid_filter"
    HANDSHAKE = "handshake"
    AUTO_HANDSHAKE = "auto_handshake"
    WPS = "wps"
    PASSWORD_VERIFY = "password_verify"
    RECOVERY = "recovery"
    ROUTER_MANAGEMENT = "router_management"
    WIRELESS_SETUP = "wireless_setup"


class OpenWrtConnectionState(str, Enum):
    """SSH 会话状态。"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    FINGERPRINT_REQUIRED = "fingerprint_required"
    FINGERPRINT_CHANGED = "fingerprint_changed"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    FAILED = "failed"


class DependencyState(str, Enum):
    """单项路由器依赖状态。"""

    UNKNOWN = "unknown"
    CHECKING = "checking"
    READY = "ready"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    DISABLED = "disabled"


class AccessPointCaptureState(str, Enum):
    """AP 在当前会话中的抓获状态。"""

    PENDING = "pending"
    PARTIAL = "partial"
    CAPTURED = "captured"
    FAILED = "failed"
    NO_RESULT = "no_result"
    NO_PROGRESS = "no_progress"


@dataclass(frozen=True)
class ClientInfo:
    """扫描到的单个 Wi-Fi 客户端摘要。"""

    mac: str
    power: int | None = None


@dataclass(frozen=True)
class AccessPointInfo:
    """页面共享的单个 AP 数据，不包含密码或原始数据包正文。"""

    bssid: str
    ssid: str = ""
    power: int | None = None
    clients: Tuple[ClientInfo, ...] = field(default_factory=tuple)
    wps_supported: bool = False
    wps_locked: bool = False
    channel: int = 0
    band: str = "2.4G"
    encryption: str = ""
    selected: bool = False
    capture_state: AccessPointCaptureState = AccessPointCaptureState.PENDING
    result_availability: str = ""

    def with_selected(self, selected):
        """返回仅改变选择状态的新对象，避免页面直接修改后端模型。"""

        return replace(self, selected=bool(selected))


@dataclass(frozen=True)
class OpenWrtRadioInfo:
    """OpenWrt 单个 radio 的能力与占用摘要。

    interfaces 记录该 radio 上当前活动的无线接口名；channel 是其中用于表示业务信道的
    接口信道（无接口则为 0）。free 表示该 radio 上没有承载 AP/STA 业务接口，可以在其上
    用 iw 自由切换抓包信道而不影响路由器自身无线。
    """

    name: str
    interface: str = ""
    driver: str = ""
    bands: Tuple[str, ...] = field(default_factory=tuple)
    monitor_supported: bool | None = None
    interfaces: Tuple[str, ...] = field(default_factory=tuple)
    channel: int = 0
    free: Optional[bool] = False


@dataclass(frozen=True)
class OpenWrtDeviceInfo:
    """登录后探测到的路由器信息。"""

    hostname: str = ""
    version: str = ""
    revision: str = ""
    target: str = ""
    architecture: str = ""
    package_architecture: str = ""
    package_manager: str = ""
    kernel_version: str = ""
    memory_mb: int | None = None
    free_memory_mb: int | None = None
    free_storage_mb: int | None = None
    sftp_available: bool | None = None
    radios: Tuple[OpenWrtRadioInfo, ...] = field(default_factory=tuple)
    model: str = ""
    board_name: str = ""
    distribution: str = ""


@dataclass(frozen=True)
class DependencyInfo:
    """一个功能或外部命令的可用状态。"""

    key: str
    display_name: str
    state: DependencyState = DependencyState.UNKNOWN
    missing_packages: Tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""


@dataclass(frozen=True)
class CaptureProgress:
    """当前任务的通用进度模型。"""

    title: str = ""
    current: int = 0
    total: int = 0
    detail: str = ""
    indeterminate: bool = False


@dataclass(frozen=True)
class CaptureArtifact:
    """一次任务产生的原始包、22000、WPS 或诊断文件。"""

    kind: str
    path: str
    bssid: str = ""
    ssid: str = ""
    verified: bool = False
    availability: str = ""


@dataclass(frozen=True)
class OpenWrtConnectionResult:
    """后台 SSH 连接和只读探测的结构化结果。

    status 为 connected、fingerprint_required 或 fingerprint_changed；前两种指纹
    状态不作为普通失败，页面可在主线程确认后重新发起连接。fingerprint_changed
    额外携带 expected_fingerprint（已保存的旧指纹）供对比。
    """

    status: str
    detail: str = ""
    fingerprint: str = ""
    key_type: str = ""
    expected_fingerprint: str = ""
    device: OpenWrtDeviceInfo = field(default_factory=OpenWrtDeviceInfo)
    dependencies: Tuple[DependencyInfo, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OpenWrtSessionSnapshot:
    """页面销毁后仍由 CaptureService 保存的完整可显示状态。"""

    connection_state: OpenWrtConnectionState = OpenWrtConnectionState.DISCONNECTED
    connection_detail: str = "未连接"
    host_fingerprint: str = ""
    device: OpenWrtDeviceInfo = field(default_factory=OpenWrtDeviceInfo)
    dependencies: Tuple[DependencyInfo, ...] = field(default_factory=tuple)
    access_points: Tuple[AccessPointInfo, ...] = field(default_factory=tuple)
    task_type: CaptureTaskType = CaptureTaskType.NONE
    task_state: str = "idle"
    pause_state: str = ""
    session_id: str = ""
    progress: CaptureProgress = field(default_factory=CaptureProgress)
    artifacts: Tuple[CaptureArtifact, ...] = field(default_factory=tuple)
    # 本次连接会话是否已跑出可用包（可跑包 CAP/22000 已落盘）。多目标批次中单个
    # 目标的可用文件在任务结束前就已落盘，产物明细只在结束时汇总；该标记让取结果
    # 入口在抓包过程中就能变绿。断开连接后快照复位，标记自动清除。
    has_verified_result: bool = False
    last_error: str = ""
    auto_stage: str = ""
    auto_outcome: str = ""
    auto_summary: str = ""
    auto_scope: Tuple[str, ...] = field(default_factory=tuple)
    auto_skipped: tuple = ()
    cleanup_errors: tuple = ()
    # 正在抓包的目标 BSSID（大写归一化后比较），供 AP 表整行绿背景；
    # 任务结束、取消或失败时服务负责清空，页面只做展示不写回。
    capturing_bssids: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def initial(cls):
        """返回包含全部功能依赖占位项的初始快照。"""

        return cls(
            dependencies=(
                DependencyInfo("scan", "信号扫描"),
                DependencyInfo("handshake", "常规抓包"),
                DependencyInfo("pmkid", "PMKID"),
                DependencyInfo("wps", "秒 Pin"),
                DependencyInfo("password", "猜密码"),
                DependencyInfo("sftp", "SFTP 同步"),
                DependencyInfo("converter", "结果转换"),
            )
        )
