"""严格空闲射频的只读准入边界，不依赖页面或旧设备快照。"""

from .command_builder import quote_argument
from .phy_channels import iter_phy_channels


class IdleRadioUnavailable(RuntimeError):
    """无可核实空闲射频；批次不得把这种策略拒绝加入重试轮次。"""


def require_idle_phy(command_runner, phy, cancel_event=None, owned_interface=""):
    """Require no enabled/running business or foreign capture, allowing inert interfaces.

    owned_interface excludes only the caller's journaled interface on revalidation.
    """

    from .radio_usage import read_radio_usage, usage_for_phy
    try:
        usage = usage_for_phy(read_radio_usage(command_runner, cancel_event,
                              owned_interface=owned_interface), phy, runner=command_runner, cancel_event=cancel_event)
    except (InterruptedError, TimeoutError):
        raise
    except RuntimeError as exc:
        raise IdleRadioUnavailable(str(exc)) from exc
    if not usage.free:
        raise IdleRadioUnavailable(usage.reason)


def phy_supports_channel(command_runner, phy, channel, cancel_event=None):
    """只读核实目标信道可用性；未知、超时或取消不允许猜测选卡。"""
    import re
    from .radio_diagnostics import RadioSelectionDiagnostics
    journal = getattr(command_runner, "radio_diagnostics", None)
    def record(reason, frequency=0):
        if isinstance(journal, RadioSelectionDiagnostics):
            journal.channel_check(phy, channel, reason, frequency)

    if not re.fullmatch(r"phy\d+", phy) or not isinstance(channel, int) or channel <= 0:
        raise ValueError("无线网卡或目标信道无效。")
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("无线信道能力检查已取消。")
    result = command_runner.run(
        "probe_phy_channel", f"iw phy {quote_argument(phy)} info",
        timeout=10, cancel_event=cancel_event,
    )
    if result.cancelled or (cancel_event is not None and cancel_event.is_set()):
        raise RuntimeError("无线信道能力检查已取消。")
    if result.timed_out or result.exit_code != 0:
        raise RuntimeError("无法核实无线网卡的目标信道能力，请刷新无线信息后再试。")
    from .device_probe import _parse_supported_modes
    modes = _parse_supported_modes(result.stdout)
    if modes and "monitor" not in modes:
        record("monitor_mode_not_advertised")
        return False
    entries = list(iter_phy_channels(result.stdout))
    if not entries:
        record("channel_list_unparsed")
        raise RuntimeError("无线网卡未提供信道清单，无法安全选择；请刷新无线信息。")
    frequency = 2484 if channel == 14 else 2407 + 5 * channel if channel < 14 else 5000 + 5 * channel
    matching = [(mhz, number, flags) for mhz, number, flags in entries
                if number == channel and mhz == frequency]
    supported = any("disabled" not in flags.lower() for _, _, flags in matching)
    reason = "available" if supported else "channel_disabled" if matching else "channel_not_listed"
    record(reason, frequency)
    return supported
