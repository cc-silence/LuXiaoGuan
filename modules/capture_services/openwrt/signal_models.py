"""路由器信号管理的白名单快照，不保存无线密码或原始配置。"""

from dataclasses import dataclass
from .signal_details import SignalDetails
from .wireless_status_models import WirelessStatus


@dataclass(frozen=True)
class RouterSignal:
    """单个 wifi-iface 的配置及运行摘要；纯数据，不执行操作。"""

    section: str
    ssid: str
    mode: str
    radio: str
    band: str
    disabled: bool
    radio_disabled: bool
    running: str
    reason: str = ""
    details: SignalDetails | None = None
    runtime_note: str = ""
    label: str = ""
    managed_bridge: bool = False
    runtime_ssid: str = ""
    runtime_band: str = ""


@dataclass(frozen=True)
class SignalInventory:
    """一次完整读取的结果；revision 用于拒绝过期页面发起的修改。"""

    rows: tuple[RouterSignal, ...] = ()
    revision: str = ""
    pending_changes: bool = False


@dataclass(frozen=True)
class SignalFailure:
    """后台失败的结构化结果；code 供服务层分支，message 只含脱敏摘要。"""

    code: str = "internal"
    message: str = ""


@dataclass(frozen=True)
class SignalSnapshot:
    """常驻服务回放数据；stale 时保留旧显示但禁止操作，不猜测启停结果。

    installing 只在信号依赖自动安装运行时为真，页面据此把刷新按钮变为取消。
    busy_label 是他任务忙碌时的短文案（如“抓包中”），页面据此把刷新按钮改成
    “XX，无法使用”；空闲时为空。
    """

    inventory: SignalInventory = SignalInventory()
    connected: bool = False
    busy: bool = False
    stale: bool = True
    installing: bool = False
    detail: str = "尚未读取路由器无线配置。"
    busy_label: str = ""

    pending_changes: bool = False
    wireless_status: WirelessStatus = WirelessStatus()
    wireless_status_stale: bool = True
