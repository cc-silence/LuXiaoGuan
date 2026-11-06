"""Verify owned WPS monitor tuning through iw/nl80211, not legacy WEXT."""
import re
from .command_builder import normalize_channel, quote_argument
from .command_runner import require_command_completion
from .monitor_ownership import owner_guard

_CHANNEL = re.compile(r"\bchannel\s+(\d+)\s*\(\s*(\d+)\s+MHz\s*\)", re.I)


def _read_channel(runner, record, cancel_event):
    result = runner.run("read_wps_channel", owner_guard(record)+
        f"iw dev {quote_argument(record.interface)} info", timeout=8, cancel_event=cancel_event)
    require_command_completion(result)
    if result.exit_code != 0:
        raise RuntimeError("无法读取本任务监听接口，已停止启动 Reaver。")
    if not re.search(r"^\s*type\s+monitor\s*$", result.stdout or "", re.M):
        raise RuntimeError("Pin 临时接口不是监听模式，已停止启动 Reaver。")
    match = _CHANNEL.search(result.stdout or "")
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def ensure_wps_channel(runner, record, target, cancel_event=None):
    """One owned tune and readback; no retries, radio guessing or other-interface edits."""
    channel = normalize_channel(target.channel)
    expected_band = str(target.band or "")
    if expected_band not in {"2.4G", "5G"}:
        raise RuntimeError("Pin 目标与临时接口的频段不一致，请重新扫描。")
    def matches(actual):
        observed_band = "2.4G" if 2400 <= actual[1] < 2500 else "5G" if 4900 <= actual[1] < 5900 else ""
        return actual[0] == channel and observed_band == expected_band
    before = _read_channel(runner, record, cancel_event)
    if matches(before):
        return
    result = runner.run("tune_wps_channel", owner_guard(record)+
        f"iw dev {quote_argument(record.interface)} set channel {channel}",
        timeout=8, cancel_event=cancel_event)
    require_command_completion(result)
    after = _read_channel(runner, record, cancel_event)
    if not matches(after):
        detail = ((result.stderr or result.stdout or "").strip()[:300] if result.exit_code else "")
        actual = str(after[0]) if after[0] else "未确认"
        raise RuntimeError(f"Pin 网卡未切到目标信道 {channel}（实际：{actual}）；未启动 Reaver。" + detail)
