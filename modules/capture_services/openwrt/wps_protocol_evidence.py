"""Finite allowlisted protocol evidence; raw frames, PINs and keys are discarded."""
import re
from .wps_observation import analyze

_EVENTS = (
    ("associated with", "associated", "已关联"),
    ("sending eapol start request", "start_sent", "已发 EAPOL-Start"),
    ("received identity request", "identity_received", "收到身份请求"),
    ("sending identity response", "identity_sent", "已发身份响应"),
    ("received m1 message", "m1", "收到 M1"),
    ("received m3 message", "m3", "收到 M3"),
    ("received m5 message", "m5", "收到 M5"),
    ("received m7 message", "m7", "收到 M7"),
    ("received wsc nack", "nack", "收到 WSC NACK"),
    ("received eap_failure message", "eap_failure", "收到 EAP Failure"),
    ("successive start failures", "start_failure", "连续启动交互失败"),
)
_FAILURE = re.compile(r"WPS transaction failed\s*\(code:\s*(0x[0-9a-f]{1,2})\)", re.I)


class WpsProtocolEvidence:
    def __init__(self):
        self.current_pin = ""
        self.seen = set()
        self.failure_codes = []

    def feed(self, text):
        """Repeated log tails do not inflate counts or retain caller text."""
        candidate = analyze(text).current_pin
        if candidate:
            self.current_pin = candidate
        lower = str(text or "")[-16384:].lower()
        for token, key, _label in _EVENTS:
            if token in lower:
                self.seen.add(key)
        for match in _FAILURE.finditer(lower):
            code = match.group(1).upper()
            if code not in self.failure_codes and len(self.failure_codes) < 8:
                self.failure_codes.append(code)

    def describe(self):
        labels = [label for _token, key, label in _EVENTS if key in self.seen]
        labels.append("\u6700\u540e\u62a5\u544a\u7684\u5019\u9009 PIN " + self.current_pin if self.current_pin else "\u5de5\u5177\u672a\u62a5\u544a\u5019\u9009 PIN")
        if "identity_received" not in self.seen:
            labels.append("日志未记录身份请求")
        if "m1" not in self.seen:
            labels.append("日志未记录 M1")
        if self.failure_codes:
            labels.append("原生交互错误码 " + ",".join(self.failure_codes))
            if "0X02" in self.failure_codes:
                labels.append("\u63a5\u6536\u8d85\u65f6\uff08\u4e0d\u7b49\u4e8e PIN \u5df2\u88ab\u62d2\u7edd\uff09")
        return "交互证据：" + "、".join(labels)
