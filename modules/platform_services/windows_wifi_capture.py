"""Windows Wi-Fi 握手抓取能力探测。"""

import locale
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

from modules.platform_services.runtime import is_windows, subprocess_creationflags


_MONITOR_LABELS = (
    "Network monitor mode",
    "网络监视模式",
    "网络监视器模式",
    "网络监控模式",
    "监视器模式",
)
_UNSUPPORTED_WORDS = ("not supported", "unsupported", "不支持")
_SUPPORTED_WORDS = ("supported", "支持")


class CapabilityProbeCancelled(RuntimeError):
    """应用退出时取消 Windows 能力探测，不作为用户可见故障。"""


@dataclass(frozen=True)
class WirelessAdapterCapability:
    """单个 Windows 无线网卡的监控能力摘要，无文件或系统副作用。"""

    name: str
    description: str
    monitor_mode_supported: bool | None = None


@dataclass(frozen=True)
class WindowsCaptureCapabilities:
    """Windows 本机握手抓取能力快照，供服务层安全跨线程传递。"""

    npcap_installed: bool = False
    dumpcap_path: str = ""
    tshark_path: str = ""
    adapters: Tuple[WirelessAdapterCapability, ...] = field(default_factory=tuple)
    ready: bool = False
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def unchecked(cls):
        """返回尚未执行系统探测时的空快照，不读取系统或文件。"""

        return cls(reasons=("尚未检测 Windows 抓包环境。",))


def _decode_command_output(data):
    """按 Windows 当前代码页解码 netsh 输出，失败时保留可诊断文本。"""

    if isinstance(data, str):
        return data
    encodings = []
    if is_windows():
        try:
            import ctypes

            # netsh 属于控制台程序，输出通常使用 OEM 代码页；它可能和 Python 的
            # locale 首选 ANSI 代码页不同，中文系统常见 OEM CP936、locale CP1252。
            encodings.append(f"cp{ctypes.windll.kernel32.GetOEMCP()}")
        except (AttributeError, OSError, ValueError):
            pass
    encodings.extend([locale.getpreferredencoding(False), "utf-8", "gbk"])
    for encoding in dict.fromkeys(encodings):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _run_netsh(*args, cancel_event=None):
    """隐藏控制台运行 netsh，并允许应用退出时终止子进程。"""

    process = subprocess.Popen(
        ["netsh", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess_creationflags(),
    )
    deadline = time.monotonic() + 15
    while process.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
            raise CapabilityProbeCancelled("Windows 抓包环境检测已取消。")
        if time.monotonic() >= deadline:
            process.kill()
            process.wait()
            raise TimeoutError("netsh 无线能力检测超时。")
        time.sleep(0.05)
    output, _ = process.communicate()
    return _decode_command_output(output)


def _channel_to_band(channel):
    """把 netsh 信道号换算成频段；6G 信道号与 2.4G 重叠，本函数不管 6G。

    参数 channel 是信道文本。返回 2.4G/5G，无法判断返回空串。
    """

    try:
        number = int(str(channel or "").strip().split()[0])
    except (TypeError, ValueError, IndexError):
        return ""
    if 1 <= number <= 14:
        return "2.4G"
    if number >= 32:
        return "5G"
    return ""


def get_connected_wifi_band(diagnostics=None):
    """返回本机当前 Wi-Fi 连接的 (频段, 信号名, 信道)，只读不改系统。

    有线连接、Wi-Fi 未连接或读取失败时返回 ("", "", "")，调用方直接放行。
    中英文 netsh 输出都认（State/状态、SSID、Channel/信道）；多网卡只看已连接的。
    """

    try:
        report = _run_netsh("wlan", "show", "interfaces")
    except Exception as exc:
        if diagnostics is not None:
            diagnostics.record("local_wifi", operation="same_band_check", source="windows_netsh",
                status="failed", exception_type=type(exc).__name__, reason="probe_failed")
        return "", "", ""
    for block in str(report or "").replace("\r\n", "\n").split("\n\n"):
        connected = False
        channel = ""
        ssid = ""
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().casefold()
            value = value.strip()
            if key in ("state", "状态"):
                connected = value.casefold() in ("connected", "已连接")
            elif key == "ssid":
                ssid = value
            elif key in ("channel", "信道"):
                channel = value
        if connected:
            band = _channel_to_band(channel)
            if diagnostics is not None:
                diagnostics.record("local_wifi", operation="same_band_check", source="windows_netsh",
                    status="completed", band=band, channel=int(channel) if channel.isdecimal() else None,
                    reason="connected_interface")
            return band, ssid, channel
    if diagnostics is not None:
        diagnostics.record("local_wifi", operation="same_band_check", source="windows_netsh",
            status="completed", reason="no_connected_interface")
    return "", "", ""


def _find_wireshark_tool(filename):
    """从标准安装目录和 PATH 查找 Wireshark 命令行工具，不修改环境变量。"""

    candidates = []
    for env_name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = os.environ.get(env_name)
        if root:
            candidates.append(Path(root) / "Wireshark" / filename)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    for part in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(part.strip('"')) / filename
        if candidate.is_file():
            return str(candidate)
    return ""


def _npcap_installed():
    """通过 Npcap 标准文件和驱动服务注册表路径判断是否已安装。"""

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    file_candidates = (
        system_root / "System32" / "Npcap" / "wpcap.dll",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Npcap" / "NPFInstall.exe",
    )
    if any(path.is_file() for path in file_candidates):
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\npcap"):
            return True
    except (ImportError, FileNotFoundError, OSError):
        return False


def _parse_driver_descriptions(report):
    """按接口提取 netsh 驱动描述，支持中英文及多无线网卡报告。"""

    descriptions = {}
    current_name = ""
    for raw_line in report.splitlines():
        line = raw_line.strip()
        lower = line.casefold()
        if not line or ":" not in line:
            continue
        value = line.split(":", 1)[1].strip()
        if "interface name" in lower or "接口名称" in line:
            current_name = value
        elif current_name and (
            "description" in lower or line.startswith("驱动程序") or line.startswith("说明") or line.startswith("描述")
        ):
            descriptions[current_name.casefold()] = value
    return descriptions


def _parse_monitor_support(report, cancel_event=None):
    """从中英文 netsh 能力报告中提取接口和监控模式支持结果。"""

    records = []
    current = None
    for raw_line in report.splitlines():
        line = raw_line.strip()
        lower = line.casefold()
        if not line:
            continue
        if ("interface name" in lower or "接口名称" in line) and ":" in line:
            if current is not None:
                records.append(current)
            current = {
                "name": line.split(":", 1)[1].strip() or "WLAN",
                "support": None,
            }
        if any(label.casefold() in lower for label in _MONITOR_LABELS) and ":" in line:
            if current is None:
                current = {"name": "WLAN", "support": None}
            value = line.split(":", 1)[1].strip().casefold()
            if any(word in value for word in _UNSUPPORTED_WORDS):
                current["support"] = False
            elif any(word in value for word in _SUPPORTED_WORDS):
                current["support"] = True
    if current is not None:
        records.append(current)
    driver_descriptions = _parse_driver_descriptions(
        _run_netsh("wlan", "show", "drivers", cancel_event=cancel_event)
    )
    if not records:
        records = [{"name": name, "support": None} for name in driver_descriptions] or [
            {"name": "WLAN", "support": None}
        ]
    return tuple(
        WirelessAdapterCapability(
            record["name"],
            driver_descriptions.get(record["name"].casefold(), record["name"]),
            record["support"],
        )
        for record in records
    )


def probe_windows_wifi_capture(cancel_event=None):
    """探测 Windows 本机抓取原始 802.11 握手包的前置条件。

    返回值：WindowsCaptureCapabilities。副作用：只读取安装路径、注册表和 netsh
    能力报告，不切换网卡模式、不安装驱动、不启动抓包。非 Windows 系统返回不可用原因。
    """

    if not is_windows():
        return WindowsCaptureCapabilities(reasons=("Windows 本机抓包仅在 Windows 上可用。",))
    npcap = _npcap_installed()
    dumpcap = _find_wireshark_tool("dumpcap.exe")
    tshark = _find_wireshark_tool("tshark.exe")
    report = _run_netsh("wlan", "show", "wirelesscapabilities", cancel_event=cancel_event)
    adapters = _parse_monitor_support(report, cancel_event=cancel_event)
    monitor_supported = any(item.monitor_mode_supported is True for item in adapters)
    reasons = []
    if not npcap:
        reasons.append("未安装 Npcap；安装时必须勾选原始 802.11 和监控模式支持。")
    if not dumpcap:
        reasons.append("未找到 dumpcap.exe；请安装 Wireshark 命令行工具。")
    if not tshark:
        reasons.append("未找到 tshark.exe；无法验证握手包和展示抓包摘要。")
    if not monitor_supported:
        if any(item.monitor_mode_supported is False for item in adapters):
            reasons.append("当前无线网卡或 Windows 驱动明确不支持网络监控模式。")
        else:
            reasons.append("Windows 未能确认无线网卡支持网络监控模式。")
    ready = npcap and bool(dumpcap) and bool(tshark) and monitor_supported
    return WindowsCaptureCapabilities(
        npcap_installed=npcap,
        dumpcap_path=dumpcap,
        tshark_path=tshark,
        adapters=adapters,
        ready=ready,
        reasons=tuple(reasons),
    )
