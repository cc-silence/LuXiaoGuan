"""Bounded Reaver observations; recovered secrets never become progress text."""
from dataclasses import dataclass
import re

_TRY = re.compile(r"Trying pin\s+[\"']?([0-9]{8}|[0-9]{4})(?![0-9])", re.I)
_EXCHANGE = re.compile(r"received (?:m[1357]|identity|wsc nack|eap_failure)\b", re.I)
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# reaver -K 依赖独立的 pixiewps 包；缺它时离线 Pin 无法进行，但其余在线候选仍可继续。
PIXIE_TOOL_MISSING = "Pixiewps 不可用，未完成计算"


@dataclass(frozen=True)
class WpsObservation:
    current_pin: str = ""
    status: str = ""
    stage: str = ""


def analyze(text):
    candidate = status = stage = ""
    for line in _ANSI.sub("", str(text or "")[-16384:]).splitlines():
        match = _TRY.search(line)
        lower = line.lower()
        if match:
            candidate = match.group(1)
            stage, status = "pin", "正在尝试"
        elif "winbox_watchdog_" in lower:
            reasons = {
                "lease_expired": "\u8fdc\u7aef\u4efb\u52a1\u5931\u53bb\u7eed\u671f\uff0c\u5df2\u7531\u4fdd\u62a4\u673a\u5236\u505c\u6b62",
                "size_limit": "\u4efb\u52a1\u65e5\u5fd7\u8fbe\u5230\u8d44\u6e90\u4e0a\u9650\uff0c\u5df2\u4fdd\u62a4\u505c\u6b62",
                "tmp_low": "\u8def\u7531\u5668\u4e34\u65f6\u7a7a\u95f4\u4e0d\u8db3\uff0c\u5df2\u4fdd\u62a4\u505c\u6b62",
                "workspace_limit": "\u4efb\u52a1\u4e34\u65f6\u6587\u4ef6\u8fbe\u5230\u8d44\u6e90\u4e0a\u9650\uff0c\u5df2\u4fdd\u62a4\u505c\u6b62",
            }
            stage = "tool_error"
            status = next((label for code, label in reasons.items() if "winbox_watchdog_" + code in lower), "\u8fdc\u7aef\u4fdd\u62a4\u673a\u5236\u5df2\u505c\u6b62\u4efb\u52a1")
        elif "pixiewps" in lower and any(token in lower for token in ("not found", "no such file", "permission denied")):
            stage, status = "tool_error", PIXIE_TOOL_MISSING
        elif "pixiewps fail" in lower or "wps pin not found" in lower:
            stage, status = "pixie_failed", "Pixie Dust 本次计算未找到 PIN"
        elif "executing pixiewps" in lower:
            stage, status = "pixie", "正在执行 Pixie Dust 计算"
        elif "received beacon" in lower:
            stage, status = "association", "已收到目标广播，准备关联"
        elif "associated with" in lower:
            stage, status = "exchange", "已关联目标，等待 WPS 交互"
        elif _EXCHANGE.search(line):
            stage, status = "exchange", "正在交换 WPS 数据"
        elif "rate limiting" in lower or "wps locked" in lower:
            stage, status = "blocked", "目标限流或 WPS 锁定"
        elif "wps turned off" in lower:
            stage, status = "blocked", "目标报告 WPS 已关闭"
        elif "failed to associate" in lower:
            stage, status = "association", "无法关联目标"
        elif "waiting for beacon" in lower:
            stage, status = "beacon", "等待目标广播"
        elif "transaction failed" in lower or "failed connections in a row" in lower:
            stage, status = "exchange", "WPS 交互失败，正在重试"
        elif "failed to initialize" in lower:
            stage, status = "tool_error", "工具初始化失败"
        elif "already been tested" in lower:
            status = "指定 Pin 已在历史进度中试过"
        elif "quitting after" in lower and "pixiewps" not in lower:
            status = "工具已到达本轮尝试上限"
    return WpsObservation(candidate, status, stage)


def observe(text):
    observation = analyze(text)
    return observation.current_pin, observation.status


def pixie_result_note(stage):
    """Describe only the observed stage, never infer unsupported hardware."""
    if stage == "pixie_failed":
        return "Pixie Dust 本次计算未找到可用 PIN。"
    if stage == "tool_error":
        return "工具运行异常，本次未完成 Pixie Dust 测试。"
    if stage == "pixie":
        return "Pixie Dust 计算尚未返回可用结果。"
    return "本次未确认完成 Pixie Dust 计算，不能据此判定目标无法秒 Pin。"
