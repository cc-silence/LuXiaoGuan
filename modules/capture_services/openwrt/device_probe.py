"""OpenWrt 系统和无线能力探测。"""

import re
import time
from dataclasses import replace

from .device_identity import BOARD_COMMAND, PHY_COMMAND, board_identity, package_architecture, parse_phy_capabilities, writable_storage_mb
from .radio_identity import radio_phy_mapping, status_radio_ifnames, uci_radio_bands

from modules.capture_services.openwrt.command_builder import quote_argument
from modules.capture_services.openwrt.models import OpenWrtDeviceInfo, OpenWrtRadioInfo


_IWDEV_MARKER = "__WINBOX_IWDEV__"


def _iwdev_text(wireless_text):
    """从合并输出中截取 iw dev 的专属段落，找不到则返回空串。"""

    match = re.search(rf"^{re.escape(_IWDEV_MARKER)}\r?\n(.*)$", wireless_text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def _parse_iwdev(text):
    """解析 `iw dev` 输出，返回 (phy -> 接口元组, phy -> 信道)。

    只在驱动输出标准段时填充；无法解析时返回空字典，调用方据此保守判断。
    """

    phy_interfaces = {}
    phy_channels = {}
    current_phy = ""
    current_interface = ""
    for line in text.splitlines():
        match = re.match(r"\s*phy#(\d+)", line)
        if match:
            current_phy = "phy" + match.group(1)
            if current_phy not in phy_interfaces:
                phy_interfaces[current_phy] = []
            current_interface = ""
            continue
        match = re.match(r"\s*Interface\s+(\S+)", line)
        if match and current_phy:
            current_interface = match.group(1)
            if current_interface and current_interface not in phy_interfaces[current_phy]:
                phy_interfaces[current_phy].append(current_interface)
            continue
        match = re.match(r"\s*channel\s+(\d+)\s*\(", line)
        if match and current_phy and current_interface:
            phy_channels[current_phy] = int(match.group(1))
    return {key: tuple(value) for key, value in phy_interfaces.items()}, phy_channels


def read_expected_phys(command_runner, cancel_event=None):
    """从 sysfs 读取本机无线 phy 清单，作为 `iw dev` 完整性的独立基准。

    sysfs readdir 是原子的，切不断；`iw dev` 经 nl80211 dump，可能从尾部截断
    （本项目实测某设备永远先吐 phy#1，截断时固定丢 phy#0）。读取失败返回空集合，
    调用方此时退回旧行为，不因此拒绝任务。"""

    try:
        result = command_runner.run(
            "list_phys",
            "ls /sys/class/ieee80211/ 2>/dev/null",
            timeout=8,
            cancel_event=cancel_event,
        )
    except Exception:
        return frozenset()
    if result.exit_code != 0:
        return frozenset()
    return frozenset(
        token for token in (result.stdout or "").split() if re.fullmatch(r"phy\d+", token)
    )


def read_iface_types(command_runner, cancel_event=None):
    """读取各无线接口类型，返回 {接口名: 小写类型}，失败返回空字典。

    只读 `iw dev` 一次，用于判断某 phy 是否全是 STA 接口；读不到时调用方保守处理。
    """

    try:
        result = command_runner.run(
            "list_iface_types", "iw dev 2>/dev/null", timeout=10, cancel_event=cancel_event
        )
    except Exception:
        return {}
    if result.cancelled or result.exit_code != 0:
        return {}
    types = {}
    current = ""
    for line in (result.stdout or "").splitlines():
        match = re.match(r"\s*Interface\s+(\S+)", line)
        if match:
            current = match.group(1)
            continue
        match = re.match(r"\s*type\s+(\S+)", line)
        if match and current:
            types[current] = match.group(1).lower()
    return types


def _parse_supported_modes(text):
    """解析 `iw phy info` 的 Supported interface modes 段，无该段返回 None。

    只认该段内 `* 名称` 行；段结束于首个非空非星号行。返回小写模式名列表。
    """

    modes = None
    for line in (text or "").splitlines():
        if modes is None:
            if line.strip() == "Supported interface modes:":
                modes = []
            continue
        match = re.match(r"^\s*\*\s*(.+?)\s*$", line)
        if match:
            modes.append(match.group(1).lower())
            continue
        if not line.strip():
            continue
        break
    return modes


def probe_monitor_capability(command_runner, phy, cancel_event=None):
    """只读返回 True/False/None；未知能力不等于支持。"""

    name = str(phy or "").strip()
    if not re.fullmatch(r"phy\d+", name):
        return False, f"无线 phy 名称不合法：{name or '<空>'}"
    try:
        result = command_runner.run(
            "probe_monitor_capability",
            f"iw phy {quote_argument(name)} info 2>/dev/null",
            timeout=10,
            cancel_event=cancel_event,
        )
    except Exception as exc:
        return None, f"能力探测失败，未确认监听支持：{type(exc).__name__}"
    if result.cancelled:
        raise RuntimeError("探测 monitor 能力已取消。")
    if result.timed_out or result.exit_code != 0:
        return None, "无法读取无线能力，请检查连接和 iw 工具后重新检测。"
    modes = _parse_supported_modes(result.stdout or "")
    if modes is None:
        return None, "固件未提供可识别的无线模式能力，未确认监听支持。"
    if "monitor" in modes:
        return True, "该 phy 支持 monitor 模式。"
    shown = ", ".join(modes) if modes else "无"
    return False, f"该 phy 不支持 monitor 模式（支持：{shown}）。"


_MONITOR_STATUS_TEXT = {
    "ready": "正常",
    "down": "存在但被关闭",
    "missing": "不存在",
    "wrong-type": "存在但不是 monitor 类型",
    "unreadable": "存在但读不出详情",
}


def monitor_status_text(status):
    """返回 monitor 状态的中文说明，未知状态原样返回。"""

    return _MONITOR_STATUS_TEXT.get(str(status or ""), str(status or ""))


def read_monitor_status(command_runner, interface, cancel_event=None):
    """读取 monitor 接口当前状态，返回 ready/down/missing/wrong-type/unreadable。

    ready=存在且是 monitor 类型；down=存在但被关闭；missing=不存在；
    wrong-type=存在但不是 monitor；unreadable=存在但读不出详情（保守报告，不猜）。
    全部只读，不修改任何状态。取消时抛异常。
    """

    name = str(interface or "").strip()
    if not name:
        return "missing"
    try:
        info = command_runner.run(
            "read_monitor_status",
            f"iw dev {quote_argument(name)} info 2>/dev/null",
            timeout=8,
            cancel_event=cancel_event,
        )
    except Exception:
        return "unreadable"
    if info.cancelled:
        raise RuntimeError("读取 monitor 状态已取消。")
    if info.exit_code == 0:
        if "type monitor" in (info.stdout or ""):
            return "ready"
        return "wrong-type"
    try:
        link = command_runner.run(
            "read_monitor_link",
            f"ip link show dev {quote_argument(name)} 2>/dev/null",
            timeout=8,
            cancel_event=cancel_event,
        )
    except Exception:
        return "unreadable"
    if link.cancelled:
        raise RuntimeError("读取 monitor 状态已取消。")
    if link.exit_code != 0:
        return "missing"
    if re.search(r"\bstate\s+DOWN\b", link.stdout or ""):
        return "down"
    return "unreadable"


def monitor_lost_note(command_runner, interface, cancel_event=None):
    """抓包工具意外退出时复核 M 接口状态，异常则返回定因后缀。

    只读、不修改状态；复核失败或接口正常返回空串，绝不污染原始错误。调用方把
    返回值直接拼到原错误信息后面即可。
    """

    try:
        status = read_monitor_status(command_runner, interface, cancel_event)
    except Exception:
        return ""
    if status == "ready":
        return ""
    if status == "missing":
        return (
            "；抓包中途 monitor 接口已丢失（可能被无线重载或驱动异常删除），"
            "请保持抓包期间不改动路由器无线后重试。"
        )
    if status == "down":
        return "；抓包中途 monitor 接口被关闭，请检查是否有其他程序改动无线后重试。"
    return f"；抓包中途 monitor 接口{monitor_status_text(status)}，请检查无线驱动后重试。"


def sta_link_down(command_runner, interface, cancel_event=None):
    """判断 STA 接口是否处于未连接状态，未明确显示未连接时按已连接处理。

    参数 interface 是远端接口名（内部引用转义）。`iw dev <iface> link` 明确打出
    Not connected 才返回 True；命令失败或输出含糊一律返回 False，调用方保守拒绝。
    """

    try:
        result = command_runner.run(
            "check_sta_link",
            f"iw dev {quote_argument(interface)} link 2>&1",
            timeout=10,
            cancel_event=cancel_event,
        )
    except Exception:
        return False
    if result.cancelled:
        return False
    return "Not connected" in (result.stdout or "")


def read_iwdev_complete(command_runner, command_name, cancel_event=None, attempts=4):
    """读取并解析 `iw dev`，直到包含 sysfs 基准的全部 phy 或用完次数。

    返回值：(phy_interfaces, phy_channels, diag)。diag 含 excerpt（原始输出前 200
    字符）、raw（完整原始输出）、expected（基准清单）、attempts（实际读取次数）、
    fallback（是否走单接口重建）、note（单行诊断指纹）。基准缺失时无法校验，第一
    次能读就直接返回（旧行为）；多次仍缺 phy 则走单接口重建兜底。取消时抛异常。"""

    expected = read_expected_phys(command_runner, cancel_event)
    last = ({}, {})
    excerpt = ""
    raw = ""
    done = 0
    for _attempt in range(max(1, int(attempts))):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("读取无线接口已取消。")
        result = command_runner.run(
            command_name,
            "iw dev",
            timeout=12,
            cancel_event=cancel_event,
        )
        if result.exit_code != 0:
            raise RuntimeError("无法读取 OpenWrt 无线接口，不能安全创建 monitor 接口。")
        raw = result.stdout or ""
        excerpt = raw.strip().replace("\r\n", "\n")[:200]
        last = _parse_iwdev(raw)
        done += 1
        if not expected or expected <= set(last[0]):
            break
        time.sleep(1)
    fallback = False
    if expected and not expected <= set(last[0]):
        rebuilt = read_iwdev_fallback(command_runner, cancel_event)
        if rebuilt[0]:
            last = (rebuilt[0], rebuilt[1])
            fallback = True
    note_parts = [
        "基准:" + (",".join(sorted(expected)) if expected else "无"),
        f"读取{done}次",
    ]
    if fallback:
        note_parts.append("单接口重建")
    diag = {
        "excerpt": excerpt,
        "raw": raw,
        "expected": sorted(expected),
        "attempts": done,
        "fallback": fallback,
        "note": "[" + "|".join(note_parts) + "]",
    }
    return last[0], last[1], diag


def read_iwdev_fallback(command_runner, cancel_event=None):
    """逐接口重建 phy 映射，供完整 dump 持续缺 phy 时兜底。

    枚举本机带 phy80211 的网卡，逐个读取所属 phy 与当前信道；接口名全部来自远端
    列表本身，非法字符直接跳过，不拼接任何外部输入。任何失败返回空结果，不抛异常，
    调用方按无可用 radio 处理。"""

    script = (
        "for __iface in $(ls /sys/class/net/ 2>/dev/null); do "
        "case \"$__iface\" in *[!A-Za-z0-9_.:@-]*) continue;; esac; "
        "[ -e \"/sys/class/net/$__iface/phy80211/name\" ] || continue; "
        "__phy=$(cat \"/sys/class/net/$__iface/phy80211/name\" 2>/dev/null); "
        "__ch=$(iw dev \"$__iface\" info 2>/dev/null | "
        "sed -n 's/^[[:space:]]*channel \\([0-9]*\\).*/\\1/p' | head -n 1); "
        "echo \"$__iface|$__phy|${__ch:-0}\"; "
        "done"
    )
    try:
        result = command_runner.run(
            "iwdev_fallback", script, timeout=20, cancel_event=cancel_event
        )
    except Exception:
        return {}, {}
    if result.exit_code != 0:
        return {}, {}
    phy_interfaces = {}
    phy_channels = {}
    for line in (result.stdout or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        interface, phy, channel_text = parts
        if not re.fullmatch(r"phy\d+", phy) or not interface:
            continue
        try:
            channel = int(channel_text)
        except (TypeError, ValueError):
            channel = 0
        phy_interfaces.setdefault(phy, []).append(interface)
        if channel > 0:
            phy_channels[phy] = channel
    return (
        {key: tuple(value) for key, value in phy_interfaces.items()},
        phy_channels,
    )


class OpenWrtDeviceProbe:
    """通过只读命令生成 OpenWrtDeviceInfo，不修改无线或网络配置。

    参数：command_runner 是已连接的 OpenWrtCommandRunner。返回值是不可变设备模型。
    副作用：会在远端执行只读 shell 命令；每条命令有超时并支持取消。
    """

    def __init__(self, command_runner):
        self.command_runner = command_runner

    def probe(self, cancel_event=None):
        """读取版本、架构、内存、存储、radio 和 SFTP 能力。"""

        system = self._run(
            "system_info",
            "cat /etc/openwrt_release 2>/dev/null; "
            "printf '\\n__WINBOX_UNAME__='; uname -m; "
            "printf '__WINBOX_KERNEL__='; uname -r; "
            "printf '__WINBOX_HOSTNAME__='; hostname 2>/dev/null; printf '\\n'; "
            "printf '__WINBOX_MODEL__='; cat /tmp/sysinfo/model 2>/dev/null; printf '\\n'; "
            "cat /proc/meminfo 2>/dev/null; "
            "printf '__WINBOX_PACKAGE_MANAGER__='; "
            "if command -v opkg >/dev/null 2>&1; then printf opkg; "
            "elif command -v apk >/dev/null 2>&1; then printf apk; else printf unknown; fi; printf '\\n'; " + BOARD_COMMAND,
            cancel_event,
        )
        if system.exit_code != 0 or system.cancelled or system.timed_out:
            raise RuntimeError(self._result_error(system, "读取 OpenWrt 系统信息失败"))
        wireless = self._run(
            "wireless_info",
            "uci show wireless 2>/dev/null; "
            "iwinfo 2>/dev/null; "
            "ip -o link 2>/dev/null; "
            "printf '\\n__WINBOX_SNAP_WIFISTATUS__\\n'; wifi status 2>/dev/null",
            cancel_event,
        )
        storage = self._run("storage_info", "df -k /overlay 2>/dev/null || df -k / 2>/dev/null", cancel_event)
        if wireless.cancelled or storage.cancelled:
            raise RuntimeError("OpenWrt 设备探测已取消。")
        if wireless.timed_out or storage.timed_out:
            raise RuntimeError("读取 OpenWrt 无线或存储信息超时，请检查 SSH 连接和设备负载。")
        # 精简固件可能没有 iwinfo/ip -o；这不应把有效 SSH 登录误报为认证失败。
        # 无线字段保留未知，后续依赖/能力状态会明确禁用相应抓包按钮。
        if storage.exit_code != 0:
            raise RuntimeError(self._result_error(storage, "读取 OpenWrt 存储信息失败"))
        # `iw dev` 单独读取并做 sysfs 完整性校验：它经 nl80211 dump，偶发从尾部截断
        # （缺 phy 或缺信道），面板的占用/空闲显示依赖它，不能用一次的结果。
        tools = self._run("wireless_tools", "command -v iw >/dev/null 2>&1 && printf PRESENT || printf MISSING", cancel_event)
        from .command_runner import require_command_completion
        require_command_completion(tools)
        if tools.exit_code != 0:
            raise RuntimeError("无法确认无线工具状态，请检查连接。")
        if tools.stdout.strip() == "MISSING":
            # A missing wireless utility must not disable file management or the shell.
            diag = {"raw": "", "expected": ()}
        else:
            _ifaces, _channels, diag = read_iwdev_complete(
                self.command_runner, "wireless_iwdev", cancel_event
            )
        wireless_text = wireless.stdout + "\n__WINBOX_IWDEV__\n" + diag["raw"]
        device = self._parse(system.stdout, wireless_text, storage.stdout, diag["expected"])
        if (
            not device.version
            or not device.target
        ):
            raise RuntimeError(
                "目标 SSH 主机不是可识别的 OpenWrt，或固件缺少 release/target/架构信息。"
            )
        return self._with_radio_capabilities(device, wireless_text, cancel_event)

    def _with_radio_capabilities(self, device, wireless_text, cancel_event):
        """登录时一次批量读取实际驱动，不按型号猜监听支持。"""
        interfaces, _channels = _parse_iwdev(_iwdev_text(wireless_text))
        mapping = radio_phy_mapping(status_radio_ifnames(wireless_text), interfaces)
        if not mapping:
            return device
        try:
            result = self._run("wireless_capabilities", PHY_COMMAND, cancel_event)
            from .command_runner import require_command_completion
            require_command_completion(result)
        except InterruptedError:
            raise
        except Exception:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("设备能力检测已取消。")
            return device
        if result.exit_code != 0:
            return device
        capabilities = parse_phy_capabilities(result.stdout or "", _parse_supported_modes)
        radios = []
        for radio in device.radios:
            capability = capabilities.get(mapping.get(radio.name), {})
            radios.append(replace(radio, driver=capability.get("driver", ""),
                                  monitor_supported=capability.get("monitor")))
        return replace(device, radios=tuple(radios))

    def _run(self, name, command, cancel_event):
        """统一使用 15 秒只读探测命令，避免设备无响应时无限等待。"""

        return self.command_runner.run(name, command, timeout=15, cancel_event=cancel_event)

    @staticmethod
    def _result_error(result, prefix):
        """把命令结果转换为不含密码的诊断文本。"""

        detail = (result.stderr or result.stdout).strip()
        return f"{prefix}；退出码：{result.exit_code}；{detail}" if detail else f"{prefix}；退出码：{result.exit_code}"

    @staticmethod
    def _parse(system_text, wireless_text, storage_text, expected_phys=()):
        """解析常见 OpenWrt/BusyBox 输出，解析失败的字段保留未知。

        参数 expected_phys 保留旧构造接口兼容；射频配置只按实际运行接口映射。
        未获得唯一归属时状态保留未知，选卡由后台另行核实硬件和接口清单。
        """

        def value(pattern, text):
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            return match.group(1).strip().strip("'\"") if match else ""

        board = board_identity(system_text)
        version = value(r"DISTRIB_RELEASE=['\"]?([^'\"\r\n]+)", system_text)
        revision = value(r"DISTRIB_REVISION=['\"]?([^'\"\r\n]+)", system_text)
        target = value(r"DISTRIB_TARGET=['\"]?([^'\"\r\n]+)", system_text)
        package_arch = value(r"DISTRIB_ARCH=['\"]?([^'\"\r\n]+)", system_text)
        hostname = value(r"__WINBOX_HOSTNAME__=([^\r\n]+)", system_text)
        architecture = value(r"__WINBOX_UNAME__=([^\r\n]+)", system_text)
        kernel_version = value(r"__WINBOX_KERNEL__=([^\r\n]+)", system_text)
        package_manager = value(r"__WINBOX_PACKAGE_MANAGER__=([^\r\n]+)", system_text)
        version = version or board.get("version", "")
        revision = revision or board.get("revision", "")
        target = target or board.get("target", "")
        package_arch = package_arch or board.get("package_architecture", "") or package_architecture(system_text)
        hostname = hostname or board.get("hostname", "")
        kernel_version = kernel_version or board.get("kernel", "")
        mem_match = re.search(r"MemTotal:\s+(\d+)\s+kB", system_text, re.IGNORECASE)
        memory_mb = int(mem_match.group(1)) // 1024 if mem_match else None
        free_match = re.search(r"MemAvailable:\s+(\d+)\s+kB", system_text, re.IGNORECASE)
        free_memory_mb = int(free_match.group(1)) // 1024 if free_match else None
        free_storage_mb = writable_storage_mb(storage_text)
        # SFTP 是否可用必须以 Paramiko 实际打开子系统的结果为准，不能猜安装路径。
        sftp_available = None
        # 从 iw dev 解析每个 phy 上活动的接口与信道，用于判断哪些 radio 空闲。
        phy_interfaces, phy_channels = _parse_iwdev(_iwdev_text(wireless_text))
        status_radios = status_radio_ifnames(wireless_text)
        configured = re.findall(r"(?m)^wireless\.([A-Za-z0-9_]+)=['\"]?wifi-device['\"]?\s*$", wireless_text)
        radio_names = sorted(set(configured) | set(status_radios))
        mapping = radio_phy_mapping(status_radio_ifnames(wireless_text), phy_interfaces)
        radios = []
        radio_bands = uci_radio_bands(wireless_text)
        for name in radio_names:
            band = radio_bands.get(name, "")
            normalized_bands = {"2g": ("2.4G",), "5g": ("5G",), "6g": ("6G",)}.get(band, ())
            # 配置序号与硬件枚举顺序无关；无唯一运行接口归属时保留未知。
            phy = mapping.get(name, "")
            interfaces = tuple(phy_interfaces.get(phy, ()))
            channel = int(phy_channels.get(phy, 0) or 0)
            is_free = not interfaces if phy in phy_interfaces else None
            radios.append(
                OpenWrtRadioInfo(
                    name=name,
                    bands=tuple(normalized_bands),
                    monitor_supported=None,
                    interfaces=interfaces,
                    channel=channel,
                    free=is_free,
                )
            )
        return OpenWrtDeviceInfo(
            hostname=hostname,
            model=value(r"__WINBOX_MODEL__=([^\r\n]+)",system_text) or board.get("model", ""),
            board_name=board.get("board_name", ""),
            distribution=value(r"DISTRIB_ID=['\"]?([^'\"\r\n]+)", system_text) or board.get("distribution", ""),
            version=version,
            revision=revision,
            target=target,
            architecture=architecture,
            package_architecture=package_arch,
            package_manager=package_manager,
            kernel_version=kernel_version,
            memory_mb=memory_mb,
            free_memory_mb=free_memory_mb,
            free_storage_mb=free_storage_mb,
            sftp_available=sftp_available,
            radios=tuple(radios),
        )
