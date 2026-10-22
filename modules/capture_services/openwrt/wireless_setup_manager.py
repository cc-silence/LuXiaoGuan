"""无线管理后台执行器：原生扫描、一次认证、系统回滚及白名单保存。"""
from dataclasses import asdict, replace
import hashlib
import json
import re
import secrets
import threading
import time

from .signal_manager import RouterSignalManager, SignalError
from .wireless_security import encryption_base
from .wireless_radio_modes import supported_htmodes, default_htmode
from .wireless_options import AP_ADVANCED
from .wireless_dependencies import WIRELESS_HELPER_PACKAGES
from .signal_package_feed import default_cache_dir
from .wireless_setup_commands import PROBE, wireless_command
from .wireless_setup_models import WirelessEnvironment, WirelessRequest, StoredWirelessPassword, parse_scan, validate_request

_SAFE_PACKAGES = frozenset((*WIRELESS_HELPER_PACKAGES, "relayd"))


def supports_interfaces(capabilities, access_points, stations):
    """按同一条驱动组合的分组容量判断，不把不同组合拼成不存在的能力。"""
    modes = capabilities.split("Supported interface modes:", 1)
    if len(modes) != 2:
        return False
    supported = modes[1].split("Band ", 1)[0].split("valid interface combinations:", 1)[0]
    if access_points and not re.search(r"(?m)^\s*\* AP\s*$", supported):
        return False
    if stations and not re.search(r"(?m)^\s*\* managed\s*$", supported):
        return False
    if access_points + stations <= 1:
        return True
    if "valid interface combinations:" not in capabilities:
        return False
    area = capabilities.split("valid interface combinations:", 1)[1]
    for combination in re.split(r"\n\s*\*\s*(?=#\{)", area):
        groups = re.findall(r"#\{\s*([^}]+)\s*\}\s*<=\s*(\d+)", combination)
        total = re.search(r"total\s*<=\s*(\d+)", combination)
        if not groups or not total or access_points + stations > int(total[1]):
            continue
        found_ap = not access_points
        found_sta = not stations
        fits = True
        for names, limit in groups:
            names = {part.strip() for part in names.split(",")}
            count = access_points * ("AP" in names) + stations * ("managed" in names)
            found_ap |= "AP" in names
            found_sta |= "managed" in names
            fits &= count <= int(limit)
        if found_ap and found_sta and fits:
            return True
    return False


class WirelessSetupManager:
    """所有远程工作只在 worker 内执行；构造时不连接设备。"""
    def __init__(self, runner):
        self.runner = runner
        self.signal = RouterSignalManager(runner)
        self._proof = None

    def _call(self, action, cancel, *, timeout=25, **values):
        """只解析结构化白名单响应，错误不携带密码或设备原始正文。"""
        command = wireless_command(action, self.signal._lua_bin, **values)
        text = self.signal._execute("wireless_setup_" + action, command, timeout, cancel)
        try:
            result = json.loads(text)
            if result.get("ok") is not True:
                code = str(result.get("code", "internal"))
                messages = {"scan_native": "当前驱动未能完成临时扫描，请核对无线驱动和 iwinfo 组件。", "scan_interface_busy": "存在同名临时扫描接口，为避免影响其他任务已停止。", "scan_recovery": "临时扫描接口的清理未确认，请保持设备在线并导出日志，勿连续重试。", "save_large": "配置超过安全保存上限，未执行保存。", "save_commit": "保存失败，已尝试恢复原配置，请重新读取核对。", "save_unsupported": "当前系统不支持独立保存而不应用，未执行保存。", "save_backup": "配置备份失败，未执行保存。", "save_recovery": "保存或恢复未确认，请停止重试并核对路由器 /tmp/rouxiaoguan-save.* 备份。", "orphan_runtime": "无法完整确认接口运行状态，未执行清理。", "orphan_large": "遗留接口数量超过检查上限，未执行清理。", "orphan_selection": "所选接口已变化或不可安全清理，请重新检查。", "radio": "无线网卡或接口不能明确对应，请刷新后重试。", "bridge_shared": "所选局域网已有中继配置，请编辑原连接，避免多条中继抢占同一网络。",
                            "bridge_state": "中继停用状态已被其他管理工具改变，请重新编辑并保存。",
                            "bridge_transition": "切换两种桥接前，请先保存为独立子网，恢复原有地址分配。",
                            "bridge_zone": "中继需要包含所选局域网的防火墙区域：允许输入、输出与转发，并关闭地址伪装。",
                            "bridge_static": "IPv4 中继需要一个已有固定管理地址的局域网。",
                            "bridge_retarget": "更换桥接局域网前，请先切回独立子网并保存。",
                            "dhcp_changed": "地址分配配置已变化或不可读，未保存桥接；请重新读取。",
                            "bridge_owner": "中继配置所属关系已变化，未修改其他配置。",
                            "bridge_backup": "缺少可用的中继恢复记录，请先核对路由器配置。",
                            "changed": "配置已经变化，请关闭其他管理工具并重新读取。",
                            "pending": "路由器有待提交改动，可在已配置信号页查看；仅凭提示不能判断改动来源。",
                            "unconfirmed": "无线运行或配置保存未确认，已停止确认保存并尝试恢复。",
                            "zone": "没有找到指定的外网防火墙区域，请在其他设置中选择。",
                            "network": "无法唯一识别网络接口，请刷新并选择现有网络。",
                            "pending_read": "无法完整读取待提交改动，未执行提交。",
                            "pending_unsupported": "改动不在标准全局暂存区，请在创建改动的原网页或工具中提交。",
                            "pending_large": "改动超过预览上限，请在原管理工具中处理。",
                            "pending_backup": "配置备份未完成，未执行提交。",
                            "pending_commit": "提交失败，已恢复原配置和待提交改动。",
                            "pending_recovery": "提交失败且恢复未确认，请停止重试并核对路由器 /tmp/rouxiaoguan-uci.* 备份。",
                            "identity": "设备身份参数无效。",
                            "identity_backup": "没有可用的原身份备份，或备份已损坏。",
                            "identity_duplicate": "MAC 地址与本机其他接口重复，请重新生成。",
                            "identity_changed": "身份配置已被其他工具改动，未覆盖现有配置。",
                            "identity_network": "请先恢复原身份并保存，再切换共用网络的桥接方式。",
                            "shared_network": "这条中继共用了局域网或其他无线接口的网络，不能直接改成上级出口；请重新扫描加入独立连接。",
                            "password": "缺少可用密码，请重新输入。",
                            "recovery": "系统回滚结果未确认，请等待 90 秒后重新登录核对。"}
                raise SignalError(code, messages.get(code, "路由器无线管理请求失败（" + re.sub(r"[^a-zA-Z0-9_.-]", "", code)[:90] + "），请检查组件及权限。"))
            if not isinstance(result.get("result"), dict):
                raise ValueError()
            return result["result"]
        except (ValueError, TypeError, AttributeError):
            raise SignalError("response", "无线管理返回了不完整的数据，请重新读取。") from None

    def read_password(self, section, revision, cancel):
        if not isinstance(section, str) or not re.fullmatch(r"[A-Za-z0-9_]+", section) or not re.fullmatch(r"[a-fA-F0-9]{192}", revision):
            raise ValueError("Invalid wireless password target")
        result = self._call("password", cancel, section=section, revision=revision, timeout=10)
        value = result.get("password")
        if not isinstance(value, str) or len(value)>256 or any(ord(c)<32 for c in value):
            raise SignalError("response", "无法读取已保存的无线密码。")
        return StoredWirelessPassword(value)

    def review_pending(self, cancel):
        base = self.signal.probe_dependencies(cancel)
        if not all(base.get(key) for key in ("lua", "uci", "ubus", "jsonc", "root", "sha256sum")):
            raise SignalError("dependencies", "缺少读取配置所需组件或管理员权限。")
        self.signal._lua_bin = base.get("lua_bin") or "lua"
        result = self._call("pending_review", cancel)
        rows, configs = result.get("rows"), result.get("configs")
        if rows == {}: rows = []
        if configs == {}: configs = []
        if (not isinstance(result.get("token"), str) or len(result["token"]) > 512 or
                not isinstance(rows, list) or len(rows) > 256 or
                not isinstance(configs, list) or any(c not in ("wireless", "network") for c in configs)):
            raise SignalError("response", "待提交改动预览数据无效。")
        for row in rows:
            if (not isinstance(row, dict) or row.get("config") not in configs or
                    any(not isinstance(row.get(k), str) or len(row[k]) > 128 or any(ord(ch) < 32 for ch in row[k])
                        for k in ("section", "option", "value"))):
                raise SignalError("response", "待提交改动预览数据无效。")
        return dict(result, rows=rows, configs=configs)

    def commit_pending(self, token, cancel):
        if cancel.is_set():
            raise InterruptedError("提交已取消。")
        if not isinstance(token, str) or not token or len(token) > 512:
            raise ValueError("Invalid pending configuration review")
        # Once started, finish the bounded save/recovery operation before shutdown.
        try:
            return self._call("pending_commit", threading.Event(), review_token=token, timeout=30)
        except SignalError as exc:
            if exc.code in ("transport", "disconnected", "timeout", "response"):
                raise SignalError("pending_uncertain", "提交结果未确认，请恢复连接后重新预览，并核对路由器 /tmp/rouxiaoguan-uci.* 中的备份。") from None
            raise

    def inspect(self, cancel, *, readonly=False):
        """只读检测程序、库、管理服务、网卡能力与无密码配置。"""
        base = self.signal.probe_dependencies(cancel)
        self.signal._lua_bin = base.get("lua_bin") or "lua"
        text = self.signal._execute("wireless_setup_probe", PROBE, 20, cancel)
        flags = {}
        expected = {"iw_runtime", "wpa_cli_runtime", "uci", "ubus", "iw", "wpa_cli", "hostapd", "wpa_supplicant", "rpcd",
                    "bus_uci", "bus_session", "bus_iwinfo", "bus_network.wireless",
                    "iwinfo_module", "rollback", "ap_sae", "ap_acs", "ap_11ac", "ap_11ax", "ap_11be", "ap_11r", "ap_eap", "ap_owe", "sta_owe", "sta_sae", "relayd"}
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep and key in expected and value in ("0", "1") and key not in flags:
                flags[key] = value == "1"
        if set(flags) != expected:
            raise SignalError("probe", "无线组件检测响应不完整，未推测缺失项或开始安装。")
        missing = []
        for key, package in (("lua", "lua"), ("uci", "libuci-lua"), ("ubus", "libubus-lua"), ("jsonc", "luci-lib-jsonc")):
            if not base[key]:
                missing.append(package)
        for key, package in (("iw", "iw"), ("wpa_cli", "wpa-cli"), ("rpcd", "rpcd")):
            if not flags[key]:
                missing.append(package)
        if not flags["iwinfo_module"] and not flags["bus_iwinfo"]:
            missing.append("rpcd-mod-iwinfo")
        issues = []
        for command in ("iw", "wpa_cli"):
            if flags[command] and not flags[command + "_runtime"]:
                issues.append(command + " \u7a0b\u5e8f\u5b58\u5728\u4f46\u542f\u52a8\u5931\u8d25\uff0c\u8bf7\u6838\u5bf9\u4f9d\u8d56\u5e93\u548c\u56fa\u4ef6\u517c\u5bb9\u6027\u3002")
        if not base["root"]:
            issues.append("需要路由器管理员权限才能应用和回滚设置。")
        if not base["sha256sum"]:
            issues.append("系统缺少配置校验工具，无法安全修改。")
        for key, text in (("uci", "缺少系统配置工具，无法安全修改无线。"),
                          ("ubus", "缺少系统管理总线，无法读取实际状态。"),
                          ("hostapd", "缺少发射信号组件，不自动替换系统无线软件。"),
                          ("wpa_supplicant", "缺少上级网络认证组件，不自动替换系统无线软件。"),
                          ("bus_network.wireless", "无线管理服务不可用，请核对固件和无线驱动。")):
            if not flags[key]:
                issues.append(text)
        if flags["rpcd"]:
            if not flags["bus_uci"] or not flags["bus_session"] or not flags["rollback"]:
                issues.append("当前管理服务未提供完整的配置回滚接口，请核对 rpcd 组件。")
            if flags["iwinfo_module"] and not flags["bus_iwinfo"]:
                issues.append("无线信息组件已安装但未加载，请重启管理服务后重新检测。")
        data = {"reload_needed": bool(flags["rpcd"] and (not flags["bus_uci"] or not flags["bus_session"] or flags["iwinfo_module"] and not flags["bus_iwinfo"]))}
        if not missing and not issues:
            data = self._call("scan_environment" if readonly else "inspect", cancel, timeout=40)
            if not re.fullmatch(r"[a-fA-F0-9]{192}", str(data.get("revision", ""))):
                raise SignalError("response", "设备配置版本标记无效。")
            for key in ("radios", "rows", "networks", "zones", "active"):
                if data.get(key) == {}:
                    data[key] = []
                if not isinstance(data.get(key), list) or len(data[key]) > 512:
                    raise SignalError("response", "设备配置清单结构或大小异常。")
            data["features"] = {key: flags[key] for key in ("ap_sae", "ap_acs", "ap_11ac", "ap_11ax", "ap_11be", "ap_11r", "ap_eap", "ap_owe", "sta_owe", "sta_sae", "relayd")}
            for radio in data["radios"]:
                radio["htmodes"] = list(supported_htmodes(radio, data["features"]))
            if not data["radios"]:
                issues.append("未发现无线网卡，请检查驱动或无线硬件。")
        fingerprint = str(getattr(getattr(self.runner, "ssh_client", None), "host_fingerprint", "") or "")
        return WirelessEnvironment(tuple(dict.fromkeys(missing)), tuple(issues), data, fingerprint)

    def install(self, approved, device, cancel, message):
        """重新核实后只补确认单中的缺项；包管理器预演拒绝升降级和替换。"""
        from .dependency_manager import OpenWrtDependencyManager
        before = self.inspect(cancel)
        wanted = tuple(package for package in (*before.missing, *(("relayd",) if "relayd" in approved and not before.data.get("features", {}).get("relayd") else ())) if package in approved)
        if set(approved) - _SAFE_PACKAGES or set(before.missing) - set(approved):
            raise SignalError("changed", "检测到新的缺失项，请重新查看安装清单。")
        if not wanted:
            return before
        installer = OpenWrtDependencyManager(self.runner)
        message("仅补充缺少的无线管理组件，保留已有无线软件。", "info")
        from .common_bundle_installer import install_from_local
        cache_dir = default_cache_dir()
        sftp_available = bool(getattr(device, "sftp_available", False))
        from .installation_sources import install_with_sources, report_verified
        source = install_with_sources(
            lambda: install_from_local(installer, wanted, device, cache_dir, sftp_available, cancel, message),
            lambda: installer._install_online(wanted, cancel, message),
            lambda: installer._install_offline(wanted, device, cache_dir, sftp_available, cancel, message), message)
        after = self.inspect(cancel)
        if after.data.get("reload_needed") and set(wanted) & {"rpcd", "rpcd-mod-iwinfo"}:
            message("重新加载新增的路由器管理组件；网页会话可能需要重新登录。", "info")
            command = """test -x /etc/init.d/rpcd || exit 1
/etc/init.d/rpcd restart >/dev/null 2>&1 || exit 1
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
 ready=1
 for object in uci session iwinfo;do
  ubus list "$object" 2>/dev/null | grep -Fx "$object" >/dev/null || ready=0
 done
 test "$ready" = 1 && exit 0
 sleep 1
done
exit 1
"""
            self.signal._execute("wireless_setup_reload", command, 25, cancel)
            after = self.inspect(cancel)
        ready = after.ready and ("relayd" not in wanted or after.data.get("features", {}).get("relayd", False))
        report_verified(source, ready, message)
        if "relayd" in wanted and not after.data.get("features", {}).get("relayd"):
            raise SignalError("installation", "relayd 安装后仍未检测到程序和启动脚本，未开始桥接。")
        return after

    def scan(self, seconds, cancel, message, *, radio=None, on_results=None):
        """Scan existing or temporary interfaces without changing saved configuration."""
        if type(seconds) is not int or not 5 <= seconds <= 120:
            raise ValueError("扫描时间需要在 5 到 120 秒之间。")
        message("正在读取无线网卡，准备扫描上级信号。", "info")
        env = self.inspect(cancel, readonly=True)
        self._require_ready(env)
        if radio is not None:
            selected = self._radio(env, radio)
            if selected.get("runtime_pending") is True:
                raise SignalError("radio", "无线网卡正在切换状态，请完成后再扫描。")
            radios = [selected]
        else:
            radios = [r for r in env.data["radios"] if not r.get("runtime_pending")]
        if not radios:
            raise SignalError("radio", "没有当前可用的无线网卡，请检查驱动或等待网卡状态切换完成。")
        message("开始扫描：" + "、".join(r["name"] for r in radios) + "，采集时间 " + str(seconds) + " 秒，每块网卡最多等待 20 秒完成扫描。", "info")
        deadline = time.monotonic() + seconds
        networks = {}
        published_at = None
        published = None
        completed = set()
        failures = []
        warned = set()
        first_pass = True
        while first_pass or time.monotonic() < deadline:
            for radio in radios:
                if cancel.is_set():
                    raise InterruptedError("扫描已取消。")
                remaining = deadline - time.monotonic()
                if not first_pass and remaining < 1:
                    break
                try:
                    interfaces = radio.get("interfaces") or []
                    interface = interfaces[0] if interfaces and radio.get("runtime_up") is not False else ""
                    message("正在扫描 " + radio["name"] + "（" + (interface or "临时扫描接口，不发射 AP") + "）。", "info")
                    target = {} if interface else {"expected_phy": self._radio(env, radio["name"])["phy"]}
                    result = self._call("scan", cancel, timeout=20,
                                        interface=interface, radio=radio["name"], **target)
                    def evidence(values):
                        journal = getattr(self.runner, "operation_diagnostics", None)
                        if journal is not None:
                            journal.record("workflow", operation="wireless_scan_parse", stage="parse",
                                status="failed" if values["result_rows"] and not values["accepted_rows"] else "completed", **values)
                        if values["rejected_rows"] and radio["name"] not in warned:
                            warned.add(radio["name"])
                            message(radio["name"] + "：已跳过 %d 条无法确认的扫描结果，只保留已核实的信号。" % values["rejected_rows"], "warning")
                    try:
                        scanned = parse_scan(result.get("results", []), radio["name"],
                                             channels=radio.get("channels", ()), on_evidence=evidence)
                    except ValueError as exc:
                        raise SignalError("scan_format", str(exc)) from None
                    for network in scanned:
                        key = (network.radio, network.bssid)
                        if key in networks or len(networks) < 1024:
                            networks[key] = network
                    completed.add(radio["name"])
                    now = time.monotonic()
                    if on_results is not None and (published_at is None or now - published_at >= 2):
                        batch = tuple(sorted(networks.values(), key=lambda n: n.power if n.power is not None else -999, reverse=True))
                        if batch != published:
                            on_results(batch)
                            published = batch
                        published_at = now
                except SignalError as exc:
                    if exc.code in ("cancelled", "disconnected", "transport", "scan_recovery", "scan_interface_busy"):
                        raise
                    failures.append(str(exc))
                    message(radio["name"] + "：" + str(exc), "warning")
            first_pass = False
            if cancel.wait(min(1, max(0, deadline-time.monotonic()))):
                raise InterruptedError("扫描已取消。")
        if not completed:
            raise SignalError("scan", failures[0] if failures else "扫描时间内未完成读取，请延长时间后重试。")
        if failures or len(completed) < len(radios):
            message("部分网卡未完成扫描，已显示其他网卡的实际结果。", "warning")
        return tuple(sorted(networks.values(), key=lambda n: n.power if n.power is not None else -999, reverse=True))

    @staticmethod
    def _require_ready(env):
        if not env.ready:
            raise SignalError("dependencies", "无线设置环境未就绪，请先检测并处理缺少的组件。")

    @staticmethod
    def _radio(env, name):
        values = [r for r in env.data["radios"] if r.get("name") == name]
        if len(values) != 1 or not re.fullmatch(r"phy\d+", str(values[0].get("phy", ""))):
            raise SignalError("radio", "无法唯一识别所选无线硬件，未按编号猜测，请刷新。")
        return values[0]

    def _wait_ready(self, request, section, cancel, bssid="", *, network="", temporary=False, expected_mac=""):
        """认证只看 COMPLETED 和所选接入点，不要求 IPv4 或访问外网。"""
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise InterruptedError("无线操作已取消。")
            status = self._call("status", cancel, radio=request.radio, section=section, mode=request.mode, timeout=8,
                                network=network, expected_mac=expected_mac, downstream=request.options.get("network", ""),
                                bridge_mode="routed" if temporary else request.options.get("bridge_mode", "routed"))
            if not temporary and request.options.get("bridge_mode") == "relayd":
                import ipaddress
                for upper in status.get("upstream4", []):
                    for lower in status.get("downstream4", []):
                        try:
                            up = ipaddress.IPv4Interface(str(upper["address"])+"/"+str(upper["mask"])).network
                            down = ipaddress.IPv4Interface(str(lower["address"])+"/"+str(lower["mask"])).network
                        except (KeyError, TypeError, ValueError):
                            raise SignalError("response", "路由器返回的桥接地址不完整。") from None
                        if up.overlaps(down):
                            raise SignalError("subnet", "IPv4 中继的本机管理网段与上级冲突，已停止保存并恢复。请先修改局域网管理网段。")
            if status.get("ready") is True and (not bssid or str(status.get("bssid", "")).lower() == bssid.lower()):
                return
            if cancel.wait(1):
                raise InterruptedError("无线操作已取消。")
        raise SignalError("authentication", "未在时限内确认信号启动，请检查信道、加密及驱动兼容性。" if request.mode == "ap" else "未在时限内完成无线连接；请检查密码、信号强度及加密兼容性，不能据此断定密码错误。")

    def _restore(self, sid, env, radio):
        """取消也给恢复独立期限；断线时由设备端 90 秒回滚保护兜底。"""
        cleanup = threading.Event()
        self._call("finish", cleanup, sid=sid, confirm=False, unapplied=True, timeout=15)
        wanted = {r["section"] for r in env.data["rows"] if r.get("device") == radio} & set(env.data["active"])
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            restored = self._call("revision", cleanup, timeout=8)
            revision = restored.get("revision")
            runtime = self._call("runtime", cleanup, radio=radio, timeout=8)
            active = runtime.get("active", [])
            original_radio = next((r for r in env.data["radios"] if r.get("name") == radio), {})
            settled = runtime.get("pending") is False and runtime.get("up") == original_radio.get("runtime_up")
            if (revision == env.data["revision"] and restored.get("dhcp_revision", "") == env.data.get("dhcp_revision", "") and wanted == set(active) and settled):
                for row in env.data["rows"]:
                    if row.get("section") not in wanted:
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        settled = False
                        break
                    status = self._call("status", cleanup, radio=radio, section=row["section"],
                                        mode=row.get("mode"), timeout=min(8, remaining))
                    expected = row.get("bssid", "") if row.get("mode") == "sta" else ""
                    if status.get("ready") is not True or (expected and str(status.get("bssid", "")).lower() != expected.lower()):
                        settled = False
                        break
                if settled:
                    return
            cleanup.wait(1)
        raise SignalError("recovery", "原配置恢复或无线重新运行尚未确认，请等待系统回滚后重新登录核对。")

    def _transaction(self, request, env, cancel, *, temporary, bssid="", enabled=None, delete=False, enable_parent=False):
        """只用系统独立会话暂存；确认前失败或取消均回滚，不回退裸写配置。"""
        self._require_ready(env)
        self._radio(env, request.radio)
        sid = self._call("session", cancel).get("sid", "")
        if not re.fullmatch(r"[a-fA-F0-9]{32}", sid):
            raise SignalError("session", "未能建立安全的配置会话。")
        error = None
        confirmed = False
        try:
            result = self._call("delete_stage" if delete else "stage" if enabled is None else "stage_toggle", cancel, timeout=30, enabled=enabled, enable_parent=enable_parent, sid=sid, token=secrets.token_hex(8),
                                revision=env.data["revision"], dhcp_revision=env.data.get("dhcp_revision", ""), values=asdict(request), temporary=temporary)
            if enabled is False or delete:
                deadline = time.monotonic() + 45
                while True:
                    if cancel.is_set(): raise InterruptedError("无线操作已取消。")
                    stopped = self._call("delete_status" if delete else "toggle_status", cancel, radio=request.radio, section=result["section"], timeout=8)
                    if stopped.get("ready") is True:
                        if not delete: break
                        runtime = self._call("runtime", cancel, radio=request.radio, timeout=8)
                        expected = set(env.data["active"]) & {r["section"] for r in env.data["rows"] if r.get("device") == request.radio and r["section"] != request.section}
                        if runtime.get("pending") is False and set(runtime.get("active", [])) == expected: break
                    if time.monotonic() >= deadline: raise SignalError("unconfirmed", "无线配置更改后的运行状态未确认。")
                    if cancel.wait(1): raise InterruptedError("无线操作已取消。")
            else:
                self._wait_ready(request, result["section"], cancel, bssid, network=result["network"], temporary=temporary, expected_mac=result.get("expected_mac", ""))
            if not temporary:
                if enabled is not None:
                    saved = self._call("toggle_saved_status", cancel, radio=request.radio,
                                       section=result["section"], enabled=enabled)
                    if saved.get("saved") is not True:
                        raise SignalError("unconfirmed", "无线开关保存未确认，正在恢复。")
                if enabled is None and not delete:
                    saved = self._call("saved_status", cancel, values=asdict(request), section=result["section"],
                                       network=result["network"], expected_mac=result.get("expected_mac"))
                    if saved.get("saved") is not True:
                        raise SignalError("unconfirmed", "无线运行但配置保存未确认，正在恢复。")
                if cancel.is_set():
                    raise InterruptedError("保存已取消。")
                self._call("finish", cancel, sid=sid, confirm=True, unapplied=False, timeout=10)
                confirmed = True
                return {"section": result["section"], "network": result["network"], "retained_network": result.get("retained_network", False)}
        except Exception as exc:
            error = exc
        finally:
            if not confirmed:
                try:
                    self._restore(sid, env, request.radio)
                except Exception as recovery:
                    raise SignalError("recovery", "操作未能确认完成；恢复状态也未确认。请等待 90 秒后重新登录核对，勿连续重试。") from recovery
        if error is not None:
            raise error
        return {"verified": True}

    def delete(self, section, revision, cancel, message):
        env = self.inspect(cancel)
        self._require_ready(env)
        if not isinstance(revision, str) or not re.fullmatch(r"(?:[a-fA-F0-9]{64}|[a-fA-F0-9]{192})", revision) or not env.data["revision"].startswith(revision):
            raise SignalError("changed", "配置已变化，请刷新后再删除。")
        old = next((r for r in env.data["rows"] if r["section"] == section), None)
        if not old or old.get("mode") not in ("ap", "sta"):
            raise SignalError("changed", "找不到可删除的信号配置。")
        request = WirelessRequest(old["mode"], old["device"], old.get("ssid", ""), section=section)
        message("正在删除信号并核对剩余无线状态。", "info")
        result = self._transaction(request, env, cancel, temporary=False, delete=True)
        self._proof = None
        return result

    def toggle(self, section, enabled, cancel, *, expected_revision="", enable_parent=False):
        env = self.inspect(cancel)
        self._require_ready(env)
        if expected_revision and not env.data["revision"].startswith(expected_revision):
            raise SignalError("changed", "配置已变化，请刷新。")
        old = next((row for row in env.data["rows"] if row["section"] == section), None)
        if not old or type(enabled) is not bool or old.get("mode") not in ("ap", "sta"):
            raise SignalError("changed", "无线配置已变化，请刷新。")
        radio = self._radio(env, old["device"])
        if enable_parent and (not enabled or radio.get("disabled") != "1"):
            raise SignalError("changed", "父网卡状态已变化，请刷新。")
        if enabled and radio.get("disabled") == "1" and not enable_parent:
            raise SignalError("parent_disabled", "父网卡已关闭，需确认后同时开启。")
        managed = old.get("rm_bridge") or old.get("rm_wds_backup")
        if not managed and not enable_parent:
            raise SignalError("changed", "请刷新后重新操作。")
        options = {}
        if managed:
            helper = next((n for n in env.data["networks"] if n["name"] == old.get("rm_bridge")), {})
            network = old.get("network")
            if isinstance(network, list): network = network[0] if len(network) == 1 else ""
            options = {"bridge_mode": "relayd" if helper else "wds", "network": helper.get("network", [network])[0]}
        request = WirelessRequest(old["mode"], old["device"], old.get("ssid", ""), section=section, options=options)
        return self._transaction(request, env, cancel, temporary=False, enabled=enabled, enable_parent=enable_parent)

    def verify(self, network, password, cancel, message):
        """只验证一个明确目标；成功后先恢复原网络，再签发短期内存凭据。"""
        self._proof = None
        if network.reason:
            raise ValueError(network.reason)
        env = self.inspect(cancel)
        self._require_ready(env)
        security = encryption_base(network.encryption)
        if security == "sae-mixed" and not env.data["features"]["sta_sae"]:
            network = replace(network, encryption="psk2")
        if security == "sae" and not env.data["features"]["sta_sae"]:
            raise SignalError("capability", "当前无线组件不支持 WPA3 连接；不会自动替换现有无线软件。")
        if security == "owe" and not env.data["features"].get("sta_owe"):
            raise SignalError("capability", "当前无线组件不支持 OWE 加密。")
        request = validate_request(WirelessRequest("sta", network.radio, network.ssid, password,
                                    options={"encryption": network.encryption, "bssid": network.bssid, **({"channel": str(network.channel)} if network.channel else {})}))
        radio = self._radio(env, network.radio)
        band = {"2.4G": "2g", "5G": "5g", "6G": "6g", "2g": "2g", "5g": "5g", "6g": "6g"}.get(network.band)
        configured_band = radio.get("band")
        if not configured_band:
            configured_band = {"11g": "2g", "11b": "2g", "11a": "5g"}.get(radio.get("hwmode"))
        if configured_band and band and configured_band != band:
            raise SignalError("capability", "所选网卡当前频段与上级信号不同，请使用相同频段的网卡扫描；不会暗中切换整块网卡的频段。")
        if not supports_interfaces(radio.get("capabilities", ""), 0, 1):
            raise SignalError("capability", "所选无线驱动未确认支持连接上级网络。")
        message("正在验证无线认证，完成后先恢复原有信号。", "info")
        self._transaction(request, env, cancel, temporary=True, bssid=network.bssid)
        token = secrets.token_hex(16)
        self._proof = (token, env.fingerprint, self._digest(request), time.monotonic()+300, network)
        return {"token": token, "network": network}

    @staticmethod
    def _digest(request):
        return hashlib.sha256(json.dumps([request.radio, request.ssid, request.password,
            request.options.get("encryption")], ensure_ascii=True).encode()).digest()

    def save(self, request, cancel, message, *, apply=True):
        """应用白名单字段并核实运行状态；原来的高级值只在显式修改时才改变。"""
        request = validate_request(request)
        env = self.inspect(cancel)
        self._require_ready(env)
        if request.revision and request.revision != env.data["revision"]:
            raise SignalError("changed", "配置已在编辑期间变化，请重新检测并打开设置。")
        if (request.mode == "sta" and encryption_base(request.options.get("encryption")) == "sae-mixed"
                and not env.data["features"].get("sta_sae")):
            request = replace(request, options={**request.options, "encryption": "psk2"})
        radio = self._radio(env, request.radio)
        current_mode = radio.get("htmode")
        if request.mode == "ap" and "htmode" not in request.options and not (isinstance(current_mode, str) and current_mode.strip()):
            fallback = default_htmode(radio, env.data["features"])
            if fallback is None:
                raise SignalError("capability", "未能确认此网卡支持的默认工作频率和带宽，请重新检测或手动选择。")
            request = replace(request, options={**request.options, "htmode": fallback})
        others = [r for r in env.data["rows"] if r.get("device") == request.radio and
                  r.get("section") != request.section and r.get("disabled") != "1"]
        ap_count = sum(r.get("mode") == "ap" for r in others) + (request.mode == "ap")
        sta_count = 1 if request.mode == "sta" else sum(r.get("mode") == "sta" for r in others)
        if not apply:
            ap_count, sta_count = int(request.mode == "ap"), int(request.mode == "sta")
        if apply and any(r.get("mode") not in ("ap", "sta") for r in others):
            raise SignalError("capability", "所选网卡还有其他模式的接口，当前无法确认组合能力，请使用另一块网卡。")
        if not supports_interfaces(radio.get("capabilities", ""), ap_count, int(sta_count)):
            raise SignalError("capability", "驱动未确认支持这组同时发射和连接的接口，请选择另一块网卡。")
        for spec in AP_ADVANCED:
            if spec.key in request.options and spec.feature and not env.data["features"].get(spec.feature):
                raise SignalError("capability", "设备未确认支持：" + spec.label)
        encryption = encryption_base(request.options.get("encryption", "psk2"))
        if encryption == "owe" and not env.data["features"].get("ap_owe" if request.mode == "ap" else "sta_owe"):
            raise SignalError("capability", "当前无线组件不支持 OWE 加密。")
        if encryption in ("sae", "sae-mixed") and not env.data["features"]["ap_sae" if request.mode == "ap" else "sta_sae"]:
            raise SignalError("capability", "当前无线组件未提供所选 WPA3 加密方式，请重新选择；不会自动替换无线软件。")
        if request.mode == "ap" and radio.get("band") == "6g" and encryption not in ("sae", "owe"):
            raise ValueError("6G 信号需要 WPA3 或 OWE 加密，请选择设备支持的模式。")
        if request.mode == "ap" and request.options.get("channel") == "auto" and not env.data["features"]["ap_acs"]:
            raise ValueError("当前无线组件不支持自动选信道，请选择具体信道。")
        channels = [str(c.get("channel")) for c in radio.get("channels", []) if request.mode == "sta" or not c.get("restricted")]
        if "channel" in request.options and request.options["channel"] not in (*channels, "auto"):
            raise ValueError("所选信道不在设备当前允许的范围内。")
        if "htmode" in request.options and request.options["htmode"] not in supported_htmodes(radio, env.data["features"]):
            raise ValueError("所选带宽不在设备支持的范围内。")
        if "country" in request.options and request.options["country"] not in [c.get("iso3166", c.get("code")) for c in radio.get("countries", [])]:
            raise ValueError("所选地区不在设备支持范围内。")
        if "txpower" in request.options and str(request.options["txpower"]) not in [str(p.get("dbm")) for p in radio.get("powers", [])]:
            raise ValueError("所选发射功率不在设备当前允许范围内。")
        if request.options.get("bridge_mode") == "relayd" and not env.data["features"].get("relayd"):
            raise SignalError("dependencies", "需要先安装 relayd 中继组件。")
        if request.options.get("bridge_mode") == "wds" or request.options.get("wds") == "1":
            if radio.get("type") != "mac80211":
                raise SignalError("capability", "未确认该无线驱动支持 WDS，不会盲目开启桥接。")
            selected = next((n for n in env.data["networks"] if n["name"] == request.options.get("network")), None)
            if request.mode == "sta" and selected is None:
                raise ValueError("请选择现有的桥接网络。")
        if not apply:
            from .save_only_manager import SaveOnlyManager
            message("正在保存配置，不重载网络或核实无线运行状态。", "info")
            return SaveOnlyManager(self).save(request, env, cancel, message)
        message("正在应用设置并核实无线运行状态，失败时尝试恢复原配置。", "info")
        result = self._transaction(request, env, cancel, temporary=False,
            bssid=request.options.get("bssid", "") if request.mode=="sta" else "")
        self._proof = None
        return result
