"""严格空闲射频的只读准入边界，不依赖页面或旧设备快照。"""

from .command_builder import quote_argument
from .phy_channels import iter_phy_channels


class IdleRadioUnavailable(RuntimeError):
    """无可核实空闲射频；批次不得把这种策略拒绝加入重试轮次。"""


def require_idle_phy(command_runner, phy, cancel_event=None, owned_interface=""):
    """核实硬件存在且无其他无线接口，失败或未知时拒绝。

    owned_interface 仅供恢复本任务登记的接口时排除自身；不根据 STA 关联状态
    判断空闲。命令只读、有界且可取消，不输出接口名或网络敏感标识。
    """

    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("核实空闲无线网卡已取消。")
    command = (
        f"__phy={quote_argument(phy)}; __owned={quote_argument(owned_interface)}; "
        '[ -d "/sys/class/ieee80211/$__phy" ] && '
        '[ -r /sys/class/net ] && [ -x /sys/class/net ] || exit 1; '
        'for __net in /sys/class/net/*; do '
        '[ -e "$__net" ] || continue; '
        '[ -e "$__net/phy80211" ] || [ -L "$__net/phy80211" ] || continue; '
        '__name=$(cat "$__net/phy80211/name") || exit 1; '
        '[ -n "$__name" ] || exit 1; '
        'if [ "$__name" = "$__phy" ] && [ "${__net##*/}" != "$__owned" ]; '
        'then printf "busy\\n"; exit 0; fi; '
        'done; printf "idle\\n"'
    )
    try:
        result = command_runner.run("check_idle_radio", command, timeout=10, cancel_event=cancel_event)
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("核实空闲无线网卡已取消。") from exc
        raise IdleRadioUnavailable(
            f"{phy} 空闲状态读取失败，无法安全使用；请检查设备连接并刷新无线信息。"
        ) from exc
    if result.cancelled or (cancel_event is not None and cancel_event.is_set()):
        raise RuntimeError("核实空闲无线网卡已取消。")
    if result.timed_out or result.exit_code != 0 or result.stdout.strip() not in {"idle", "busy"}:
        raise IdleRadioUnavailable(
            f"{phy} 空闲状态无法核实（硬件或接口清单读取失败），仅使用空闲网卡模式已拒绝使用。"
            "请刷新设备信息并检查无线状态，或更换频段/设备。"
        )
    if result.stdout.strip() == "busy":
        raise IdleRadioUnavailable(
            f"{phy} 存在其他无线接口（未连接 STA 也算占用），仅使用空闲网卡模式已拒绝使用；"
            "不会接管或停用原业务。请更换有空闲射频的频段/设备。"
        )


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
