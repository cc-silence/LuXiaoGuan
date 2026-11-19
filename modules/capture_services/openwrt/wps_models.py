"""Shared immutable WPS results and progress."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WpsTargetResult:
    """单个目标的 Pin 结局：PIN 与密码只写结果文件，不进消息与快照。"""

    bssid: str
    ssid: str
    mode: str = ""
    pin_ok: bool = False
    psk_ok: bool = False
    tried_seconds: int = 0
    result_file: str = ""
    wpc_file: str = ""
    note: str = ""
    # 系统 WPS 不可用的结构化原因：basic_wpad / missing_tool / association ...
    # 页面用 kind 决定是否提供“升级完整版 wpad”，reason 用于主日志提示。
    unavailable_kind: str = ""
    unavailable_reason: str = ""
    # 仅用于主线程弹窗与凭据库写入；repr 不显示，消息与快照也不读取。
    pin: str = field(default="", repr=False)
    pin_saved: bool = field(default=False, repr=False)


@dataclass(frozen=True)
class WpsUpdate:
    """Progress may show a submitted candidate; recovered secrets stay in result files."""

    elapsed: int
    total: int
    target_label: str = ""
    target_index: int = 0
    target_total: int = 0
    phase: str = ""
    current_pin: str = ""
    status: str = ""
    target_bssid: str = ""
    candidate_elapsed: float = 0.0
    average_per_minute: float | None = None


