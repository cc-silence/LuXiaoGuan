"""路由器信号管理执行边界；不依赖 Qt 或页面。"""

import json

import re
import time
from uuid import uuid4
from modules.capture_services.capture_logging import LOGGER

from .radio_identity import valid_radio_name
from .command_builder import quote_argument

from .package_manager import PackageManager, package_error_reason, compare_opkg_versions

from .signal_commands import (
    _LUA_BINS,
    REMOTE_PACKAGE_DIR,
    SIGNAL_PACKAGES,
    signal_cleanup_command,
    signal_command,
    signal_list_installed_command,
    signal_probe_command,
)
from .signal_models import RouterSignal, SignalInventory
from .signal_package_feed import (
    default_cache_dir,
    download_profile_packages,
    is_safe_filename,
    normalize_profile,
    parse_installed,
    offline_cache_transaction,
)


class SignalError(RuntimeError):
    """携带路由器错误码的执行失败；仍是 RuntimeError，兼容旧调用方。"""

    def __init__(self, code, message):
        """保存 code 供服务层分支，界面只展示 message。"""
        super().__init__(message)
        self.code = code or "internal"


def _looks_like_network(message):
    """仅明确联网错误才转代下载，不用离线重试掩盖签名或依赖冲突。"""
    return "联网或 DNS" in message


_REASONS = {
    "mode": "此无线模式不支持启停，仅展示配置。",
    "anonymous": "匿名配置不能稳定定位；请在路由器管理界面处理。",
    "radio": "父 radio 无法唯一识别，请检查无线配置。",
    "disabled": "disabled 字段不是标准 0/1，拒绝猜测。",
}
_ERRORS = {
    "managed_bridge": "此连接需要同时处理中继与地址分配，请刷新后使用新版无线设置。",
    "dependencies": "缺少 lua、Lua uci/ubus 或 luci.jsonc；请点“安装信号依赖”自动安装。",
    "read": "无法完整读取 wireless；请检查读取权限及 sha256sum 工具。",
    "pending": "无线配置有待提交改动，点“查看待提交改动”核对。改动可能来自本软件、网页或脚本，未确定来源；提交或撤销后刷新即可，无需重新登录。",
    "runtime": "ubus 无线运行状态不可用；请检查 ubus 权限与 netifd 服务。",
    "changed": "wireless 与读取时不一致；请关闭其他配置工具并刷新后重试。",
    "unsupported": "配置类型或定位方式不支持；请在路由器管理界面处理。",
    "parent_disabled": "父网卡已关闭，请刷新后点“启用”，确认同时开启父网卡和当前信号。",
    "backup": "路由器备份创建或副本核对失败；请检查 /etc 权限、剩余空间和符号链接，未授权继续写入。",
    "write": "隔离 UCI 写入失败；请检查配置权限，刷新后核对状态。",
    "commit": "UCI 提交未确认；请重连并核对配置，不要连续点击启停。",
    "unconfirmed": "配置可能已提交，但重载或一致性核实失败；请重连并刷新核对。",
    "locked": "设备上仍有信号管理任务锁；请等待任务结束，遗留锁须由管理员确认无任务后清理。",
    "internal": "设备返回不支持的结构或内部错误；请在路由器管理界面检查，不继续修改。",
}


class RouterSignalManager:
    """复用已连接的 command_runner，后台读取或修改单个无线配置。"""

    def __init__(self, command_runner):
        """注入短命令执行器；构造不连接设备、不修改配置。"""
        self.runner = command_runner
        self._lua_bin = "lua"

    def probe_dependencies(self, cancel_event):
        """纯 shell 探针：逐项返回运行环境与安装前置是否可用。

        不依赖 lua 也能运行；输出只含固定键和数字，不解析敏感内容。
        overlay_kb 取不到时为 -1，调用方不得因此拦截。
        """
        try:
            data = json.loads(self._execute(
                "router_signal_probe", signal_probe_command(), 20, cancel_event,
            ))
            result = data["result"]
            result.setdefault("apk", 0)
            flags = {key: result[key] for key in (
                "lua", "uci", "ubus", "jsonc", "sha256sum", "opkg", "apk", "root")}
            if any(type(value) is not int or value not in (0, 1) for value in flags.values()):
                raise ValueError()
            overlay_kb = result.get("overlay_kb", -1)
            if type(overlay_kb) is not int:
                raise ValueError()
            lua_bin = result.get("lua_bin", "")
            if type(lua_bin) is not str or (lua_bin and lua_bin not in _LUA_BINS):
                raise ValueError()
            return {key: bool(value) for key, value in flags.items()} | {
                "overlay_kb": overlay_kb, "lua_bin": lua_bin}
        except (ValueError, TypeError, KeyError, AttributeError):
            raise SignalError("internal", "路由器依赖探针响应异常；请检查连接后刷新。") from None

    def _installation_environment(self, before):
        """在安装前统一核对包管理器、权限和空间。"""
        try:
            package_manager = PackageManager.from_probe(before)
        except ValueError as exc:
            raise SignalError("environment", str(exc)) from exc
        if not before["root"]:
            raise SignalError(
                "environment",
                "当前远程连接用户不是 root，无权安装软件包；请用 root 连接后重试。",
            )
        if before["overlay_kb"] < 0:
            raise SignalError("environment", "无法确认可写空间，未执行安装。")
        if 0 <= before["overlay_kb"] < 1024:
            raise SignalError(
                "environment",
                "路由器剩余空间不足 1MB，无法安装；请清理空间后重试。",
            )
        return package_manager

    def install_dependencies(self, cancel_event, message=None):
        """确认后在线安装白名单信号依赖，装完复测；缺项仍在则诚实报错。

        包名写死 4 个，不拼用户输入。先预检 opkg/root/空间，分“更新源”“装包”
        两步执行，每步失败都给出具体原因，不共用一句糊话。
        联网类失败转 offline_retry，由上层决定是否走离线安装。
        """
        tell = message or (lambda _text, _level: None)
        before = self.probe_dependencies(cancel_event)
        if all(before[key] for key in ("lua", "uci", "ubus", "jsonc")):
            return before
        package_manager = self._installation_environment(before)
        try:
            tell("正在更新软件源…", "info")
            self._execute_stage(
                "router_signal_update", package_manager.update(), 180, cancel_event, "更新软件源",
            )
            tell("正在安装信号依赖…", "info")
            plan = self._execute_stage("router_signal_package_plan",
                package_manager.change(SIGNAL_PACKAGES, simulate=True), 60, cancel_event,
                "检查依赖安装计划")
            try:
                package_manager.verify_plan(plan, SIGNAL_PACKAGES)
            except ValueError as exc:
                raise SignalError("environment", str(exc)) from exc
            self._execute_stage(
                "router_signal_install", package_manager.change(SIGNAL_PACKAGES), 240, cancel_event, "安装信号依赖",
            )
        except SignalError as exc:
            if exc.code == "transport" and _looks_like_network(str(exc)):
                raise SignalError("offline_retry", str(exc)) from None
            raise
        after = self.probe_dependencies(cancel_event)
        missing = [key for key in ("lua", "uci", "ubus", "jsonc") if not after[key]]
        if missing:
            raise SignalError(
                "dependencies",
                "安装后仍缺少：" + "、".join(missing) + "；请检查路由器联网、软件源和剩余空间后重试。",
            )
        return after

    def install_auto(self, device, cache_dir, cancel_event, message=None, uploader=None):
        """Use the common offline bundle, then router/PC network installation.

        Device identity is matched exactly; offline resources are downloaded
        separately and are never embedded in the application.
        """
        from .dependency_manager import OpenWrtDependencyManager
        from .common_bundle_installer import install_from_local
        from .installation_sources import install_with_sources, report_verified
        tell = message or (lambda *_: None)
        source = install_with_sources(
            lambda: install_from_local(OpenWrtDependencyManager(self.runner), SIGNAL_PACKAGES, device,
                cache_dir, bool(getattr(device, "sftp_available", False)), cancel_event, tell, uploader),
            lambda: self.install_dependencies(cancel_event, tell),
            lambda: self.install_offline(device, cache_dir, cancel_event, tell, uploader), tell)
        after = self.probe_dependencies(cancel_event)
        ready = all(after[key] for key in ("lua", "uci", "ubus", "jsonc"))
        report_verified(source, ready, tell)
        if not ready:
            raise SignalError("dependencies", "安装后信号依赖仍未就绪，请导出诊断日志。")
        return after

    @offline_cache_transaction(lambda result: all(result.get(key) for key in ("lua", "uci", "ubus", "jsonc")))
    def install_offline(self, device, cache_dir, cancel_event, message=None, uploader=None):
        """离线安装：本机按档案下载闭包→SFTP 传包→路由器本地安装→复测。

        device 需 version/package_architecture/package_manager 三字段；
        uploader 为空则用执行器的 SSH 客户端，可注入替身单测。
        """
        if str(getattr(device, "package_manager", "") or "").strip() == "apk":
            return self._install_offline_apk(device, cache_dir, cancel_event, message, uploader)
        try:
            profile = normalize_profile(
                getattr(device, "version", ""),
                getattr(device, "package_architecture", ""),
                getattr(device, "package_manager", ""),
            )
        except ValueError as exc:
            raise SignalError("environment", str(exc)) from None
        installed_text = self._execute_stage(
            "router_signal_list_installed", signal_list_installed_command(),
            30, cancel_event, "读取已装软件包",
        )
        try:
            paths = download_profile_packages(
                profile, parse_installed(installed_text),
                cache_dir or default_cache_dir(), cancel_event=cancel_event,
                message=message, version_check=lambda a, op, b: compare_opkg_versions(self.runner, a, op, b, cancel_event),
            )
        except ValueError as exc:
            raise SignalError("environment", str(exc)) from None
        except RuntimeError as exc:
            raise SignalError("transport", f"本机下载安装包失败：{exc}") from None
        return self._install_ipk_paths(paths, cancel_event, message, uploader)

    def _install_ipk_paths(self, paths, cancel_event, message=None, uploader=None):
        """Transfer PC-downloaded packages, simulate installation, clean up and verify."""
        if not paths:
            return self.probe_dependencies(cancel_event)
        push = uploader or self._sftp_upload
        tell = message or (lambda _text, _level: None)
        self._execute_stage(
            "router_signal_mkdir", "mkdir -p " + REMOTE_PACKAGE_DIR,
            30, cancel_event, "准备传包目录",
        )
        try:
            total = len(paths)
            for position, path in enumerate(paths, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    raise SignalError("cancelled", "信号管理已取消；请刷新核对设备状态。")
                tell(f"正在传输 {path.name}（第 {position}/{total} 个）…", "info")
                push(str(path), REMOTE_PACKAGE_DIR + "/" + path.name)
            if cancel_event is not None and cancel_event.is_set():
                raise SignalError('cancelled','信号依赖安装已取消。')
            package_manager = PackageManager("opkg")
            remote_paths = [REMOTE_PACKAGE_DIR + "/" + path.name for path in paths]
            plan = self._execute_stage("router_signal_local_plan",
                package_manager.local_install(remote_paths, simulate=True),
                90, cancel_event, "核对离线安装计划")
            try:
                package_manager.verify_plan(plan, ())
            except ValueError as exc:
                raise SignalError("environment", str(exc)) from exc
            if cancel_event is not None and cancel_event.is_set():
                raise SignalError('cancelled','信号依赖安装已取消。')
            tell(f"正在安装 {total} 个离线包…", "info")
            self._execute_stage(
                "router_signal_offline_install",
                package_manager.local_install(remote_paths),
                240, cancel_event, "安装信号依赖",
            )
        finally:
            from .offline_cleanup import cleanup_remote
            cleanup_remote(self._execute, "router_signal_cleanup", signal_cleanup_command(), tell)
        after = self.probe_dependencies(cancel_event)
        missing = [key for key in ("lua", "uci", "ubus", "jsonc") if not after[key]]
        if missing:
            raise SignalError(
                "dependencies",
                "安装信号依赖后仍缺少：" + "、".join(missing) + "；请确认本机联网正常、设备档案匹配后重试。",
            )
        return after

    def _install_offline_apk(self, device, cache_dir, cancel_event, message=None, uploader=None):
        """apk（25.12+）离线安装信号依赖：传索引 → 路由器算闭包 → 下载 → 传回 → 本地装。

        依赖闭包交给路由器自身的 apk；PC 只下载 `SIGNAL_PACKAGES` 闭包并核对官方哈希。
        """
        from .apk_offline import ApkOfflineInstaller
        tell = message or (lambda _text, _level: None)

        def stage(command_name, command, timeout, label):
            return self._execute_stage(command_name, command, timeout, cancel_event, label)

        installer = ApkOfflineInstaller(stage, tell=tell, uploader=uploader or self._sftp_upload,
                                        cleanup_execute=self._execute)
        try:
            installer.install(tuple(SIGNAL_PACKAGES), device, cache_dir or default_cache_dir(),
                              cancel_event)
        except ValueError as exc:
            raise SignalError("environment", str(exc)) from None
        except SignalError:
            raise
        except RuntimeError as exc:
            raise SignalError("transport", f"本机下载安装包失败：{exc}") from None
        after = self.probe_dependencies(cancel_event)
        missing = [key for key in ("lua", "uci", "ubus", "jsonc") if not after[key]]
        if missing:
            raise SignalError(
                "dependencies",
                "\u8def\u5c0f\u7ba1 下载安装后仍缺少：" + "、".join(missing) + "；请确认本机联网正常、设备档案匹配后重试。",
            )
        return after

    def _sftp_upload(self, local_path, remote_path):
        """经执行器 SSH 客户端上传单个文件；无客户端时诚实报错。

        成功不关闭共享句柄（复用长连接）；失败先丢弃缓存句柄再报错，
        否则后一个文件必死——此前 finally 里直接 close 共享句柄就是这个 bug。
        """
        ssh_client = getattr(self.runner, "ssh_client", None)
        if ssh_client is None:
            raise SignalError("transport", "远程连接不可用，无法传输安装包。")
        try:
            sftp = ssh_client.open_sftp(timeout=30)
        except Exception:
            raise SignalError("transport", "文件传输通道打开失败；请重新登录后重试。") from None
        try:
            sftp.put(local_path, remote_path)
        except Exception:
            try:
                ssh_client.drop_sftp()
            except Exception:
                pass
            raise SignalError("transport", "安装包传输中断；请检查连接后重试。") from None

    @staticmethod
    def _stage_reason(stderr):
        """按 opkg 常见错误关键字给具体原因；只做子串匹配，不回传原始输出。"""
        return package_error_reason(stderr)

    def _execute_stage(self, command_name, command, timeout, cancel_event, stage):
        """分阶段执行安装命令；取消/超时/失败各自给明确说法。"""
        if cancel_event is not None and cancel_event.is_set():
            raise SignalError("cancelled", "信号管理已取消；请刷新核对设备状态。")
        result = self._run_command(command_name, command, timeout, cancel_event)
        if result.cancelled:
            raise SignalError("cancelled", "信号管理已取消；请刷新核对设备状态。")
        if result.timed_out:
            raise SignalError(
                "transport", f"{stage}超时（超过 {timeout} 秒）；请检查路由器负载与网速后重试。",
            )
        if result.exit_code != 0:
            raise SignalError(
                "transport", f"{stage}失败：{self._stage_reason(result.stderr or result.stdout)}；请处理后重试。",
            )
        return (result.stdout or "") + "\n" + (result.stderr or "") if command_name.endswith("_plan") else result.stdout

    def read(self, cancel_event, *, details=False):
        """后台读取全部 wifi-iface，包括禁用项；只返回白名单，不写本地文件。"""
        data = self._run("read", cancel_event, details=True) if details else self._run("read", cancel_event)
        try:
            revision = data["revision"]
            if not isinstance(revision, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", revision):
                raise ValueError()
            source = data["rows"]
            # luci.jsonc 将空 Lua 表编码为对象；只允许空对象代表空列表。
            if source == {}:
                source = []
            if not isinstance(source, list) or len(source) > 512:
                raise ValueError()
            from .signal_details import parse_details
            rows = []
            for item in source:
                fields = ("section", "ssid", "mode", "radio", "band", "running", "reason")
                if not isinstance(item, dict) or any(type(item.get(k)) is not str for k in fields):
                    raise ValueError()
                if any(len(item[k]) > 256 for k in fields):
                    raise ValueError()
                if any(type(item.get(k)) is not bool for k in ("disabled", "radio_disabled")):
                    raise ValueError()
                if item["running"] not in {"active", "inactive", "unknown"}:
                    raise ValueError()
                reason = _REASONS.get(item["reason"], "设备返回不支持的配置，请在路由器管理界面处理。") if item["reason"] else ""
                if item["mode"] not in {"ap", "sta"}:
                    reason = _REASONS["mode"]
                if not re.fullmatch(r"[A-Za-z0-9_]+", item["section"]):
                    reason = _REASONS["anonymous"]
                if not isinstance(item.get("label", ""), str) or len(item.get("label", "")) > 128:
                    raise ValueError()
                if type(item.get("managed_bridge", False)) is not bool:
                    raise ValueError()
                for key in ("runtime_ssid", "runtime_band"):
                    if type(item.get(key, "")) is not str or len(item.get(key, "")) > 256:
                        raise ValueError()
                if item.get("runtime_band", "") not in ("", "2g", "5g", "6g"):
                    raise ValueError()
                rows.append(RouterSignal(
                    item["section"], item["ssid"], item["mode"], item["radio"], item["band"],
                    item["disabled"], item["radio_disabled"], item["running"], reason,
                    parse_details(item.get("details")),
                    {"": "", "pending": "无线网卡正在重新加载", "setup_failed": "固件报告无线初始化失败"}.get(
                        item.get("runtime_note", ""), "固件返回未知运行状态"),
                    item.get("label", ""), item.get("managed_bridge", False),
                    item.get("runtime_ssid", ""), item.get("runtime_band", ""),
                ))
            if sum(len(row.details.clients) for row in rows if row.details is not None) > 512:
                raise ValueError()
            if len({row.section for row in rows}) != len(rows):
                raise ValueError()
            if type(data.get("pending_changes", False)) is not bool:
                raise ValueError()
            return SignalInventory(tuple(rows), revision, data.get("pending_changes", False))
        except (KeyError, TypeError, ValueError):
            raise SignalError("internal", "路由器无线清单结构不完整；请检查固件支持并重新刷新。") from None

    def change(self, inventory, section, enabled, cancel_event):
        """后台修改一个 disabled 并有限轮询核实；失败不自动覆盖回滚其他配置。

        原始备份保存在路由器 /etc/winbox-wireless-backups/<token>/wireless，权限 600。
        取消、断连或超时均可能发生在提交之后，因此不能报告已撤销。
        """
        row = next((r for r in inventory.rows if r.section == section), None)
        if row is None or row.reason or type(enabled) is not bool:
            raise SignalError("unsupported", "无线配置不支持操作；请刷新后在路由器管理界面检查。")
        parent = enabled and row.radio_disabled
        if enabled == (not row.disabled) and not parent:
            raise SignalError("changed", _ERRORS["changed"])
        if row.managed_bridge or parent:
            from .wireless_setup_manager import WirelessSetupManager
            options = {"enable_parent": True} if parent else {}
            WirelessSetupManager(self.runner).toggle(section, enabled, cancel_event,
                                                     expected_revision=inventory.revision, **options)
            return self.read(cancel_event)
        token = uuid4().hex
        backup = f"/etc/winbox-wireless-backups/{token}/wireless"
        try:
            self._run("change", cancel_event, revision=inventory.revision,
                      section=section, enabled=enabled, token=token)
            for attempt in range(6):
                current = self.read(cancel_event)
                actual = next((r for r in current.rows if r.section == section), None)
                expected = "active" if enabled else "inactive"
                if actual and actual.disabled == (not enabled) and actual.running == expected:
                    return current
                if attempt < 5:
                    if cancel_event is not None:
                        if cancel_event.wait(1):
                            raise SignalError("cancelled", "已请求停止核实。")
                    else:
                        time.sleep(1)
            raise SignalError("unconfirmed", "配置或运行状态未达到请求值。")
        except Exception as exc:
            # 仅传播本模块已脱敏的摘要；未知异常不得携带原始 stdout/stderr。
            # 错误码原样保留，服务层靠它判断是否缺依赖。
            code = exc.code if isinstance(exc, SignalError) else "internal"
            detail = str(exc) if isinstance(exc, RuntimeError) else "设备执行异常。"
            raise SignalError(
                code,
                f"路由器启停结果未确认：{detail} 请重连并刷新核对；不自动回滚。"
                f"若备份步骤已完成，原配置在路由器 {backup}；恢复前请由管理员核对后续改动。"
            ) from None

    def repair(self, inventory, section, cancel_event):
        """运行态修复：配置已启用但接口未运行，重载该 radio 并核实回来。

        不修改任何 UCI（disabled 原样），只做一次 `wifi up <radio>` + 有界轮询
        核实，专治抓包接管失败等原因留下的“已启用·未运行”。父 radio 被禁用、
        行不支持、配置本就禁用或运行正常时拒绝（各回各家，不抢启用/禁用入口）。
        """

        row = next((r for r in inventory.rows if r.section == section), None)
        if row is None or row.reason:
            raise SignalError("unsupported", "无线配置不支持操作；请刷新后在路由器管理界面检查。")
        if row.disabled:
            raise SignalError("unsupported", "该信号配置为禁用，请直接点“启用”；修复只处理“已启用但未运行”。")
        if row.radio_disabled:
            raise SignalError("parent_disabled", _ERRORS["parent_disabled"])
        if row.running == "active":
            raise SignalError("unsupported", "该信号运行正常，无需修复。")
        if not valid_radio_name(row.radio):
            raise SignalError("unsupported", "父 radio 无法唯一识别，请检查无线配置。")
        result = self._run_command(
            "repair_wifi_up", f"wifi up {quote_argument(row.radio)}", timeout=60, cancel_event=cancel_event)
        if result.cancelled:
            raise SignalError("cancelled", "信号修复已取消；请刷新核对设备状态。")
        if result.timed_out or result.exit_code != 0:
            raise SignalError(
                "transport", "重新启用无线网卡超时或失败，状态未确认；请检查连接后刷新核对。")
        for attempt in range(15):
            if cancel_event is not None and cancel_event.is_set():
                raise SignalError("cancelled", "信号修复已取消；请刷新核对设备状态。")
            current = self.read(cancel_event)
            actual = next((r for r in current.rows if r.section == section), None)
            if actual and not actual.disabled and actual.running == "active":
                return current
            if attempt < 14:
                if cancel_event is not None:
                    if cancel_event.wait(2):
                        raise SignalError("cancelled", "信号修复已取消；请刷新核对设备状态。")
                else:
                    time.sleep(2)
        raise SignalError(
            "unconfirmed",
            f"已重新启用 {row.radio}，但该信号 30 秒内仍未运行；请在路由器管理界面检查，"
            "必要时重启路由器无线或整机后再刷新核对。")

    def _run(self, action, cancel_event, **values):
        """统一有限时执行、取消和 JSON 校验；拒绝输出原始错误或配置内容。

        默认解释器缺依赖时探针找一次版本化替代并重试；库缺失则原样上报依赖错误。
        """
        try:
            return self._run_with_bin(action, self._lua_bin, cancel_event, **values)
        except SignalError as exc:
            if exc.code != "dependencies" or self._lua_bin != "lua":
                raise
            probe = self.probe_dependencies(cancel_event)
            alt = probe.get("lua_bin", "")
            if not alt or alt == "lua" or not all(probe[key] for key in ("uci", "ubus", "jsonc")):
                raise
            self._lua_bin = alt
            return self._run_with_bin(action, alt, cancel_event, **values)

    def _run_with_bin(self, action, lua_bin, cancel_event, **values):
        """用指定解释器执行 Lua 事务并校验信封；错误码原样透出。"""
        stdout = self._execute(
            "router_signal_" + action,
            signal_command(action, lua_bin, **values), 20, cancel_event,
        )
        try:
            data = json.loads(stdout)
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise SignalError("internal", "路由器响应不是完整 JSON；请检查固件依赖与连接后刷新。") from None
        if data.get("ok") is not True:
            code = data.get("code")
            raise SignalError(
                code if code in _ERRORS else "internal",
                "路由器 wireless：" + _ERRORS.get(code, _ERRORS["internal"]),
            )
        if not isinstance(data.get("result"), dict):
            raise SignalError("internal", "路由器响应缺少核实结果；请刷新后重试。")
        return data["result"]

    def _run_command(self, command_name, command, timeout, cancel_event):
        """按连接证据分类失败；日志只保留阶段、类型和耗时，不记录远端正文。"""
        started = time.monotonic()
        try:
            return self.runner.run(command_name, command, timeout=timeout, cancel_event=cancel_event)
        except Exception as exc:
            connected = getattr(getattr(self.runner, "ssh_client", None), "connected", None)
            stage = getattr(exc, "ssh_stage", "execute")
            LOGGER.warning("Signal request failed action=%s stage=%s type=%s elapsed=%.3f connected=%s",
                           command_name, stage, type(exc).__name__, time.monotonic() - started, connected)
            if isinstance(exc, InterruptedError) or (cancel_event is not None and cancel_event.is_set()):
                code, message = "cancelled", "信号管理已取消；请刷新核对设备状态。"
            elif connected is False:
                code, message = "disconnected", "路由器远程连接已不可用；请重新登录后刷新核对。"
            elif isinstance(exc, TimeoutError):
                code, message = "timeout", "路由器请求超时，尚不能判定登录连接已断开；请稍后刷新核对。"
            else:
                code, message = "transport", "路由器请求执行失败，尚不能判定登录连接已断开；请查看诊断信息。"
            detail = f"动作：{command_name}；阶段：{stage}；异常类型：{type(exc).__name__}。"
            raise SignalError(code, message + detail) from exc

    def _execute(self, command_name, command, timeout, cancel_event):
        """执行单条命令并返回 stdout；取消/超时/非零退出一律转脱敏错误。"""
        if cancel_event is not None and cancel_event.is_set():
            raise SignalError("cancelled", "信号管理已取消；请刷新核对设备状态。")
        result = self._run_command(command_name, command, timeout, cancel_event)
        if result.cancelled:
            raise SignalError("cancelled", "信号管理已取消；请刷新核对设备状态。")
        if result.timed_out:
            raise SignalError("timeout", "路由器命令执行超时；请稍后重试。")
        if getattr(result, "failure_stage", "") or getattr(result, "exception_type", ""):
            connected = getattr(getattr(self.runner, "ssh_client", None), "connected", None)
            code = "disconnected" if connected is False else "transport"
            raise SignalError(code, "路由器命令传输失败；请检查连接后重试。")
        if result.exit_code != 0:
            raise SignalError("execution", "路由器命令未成功执行；请检查组件和权限后重试。")
        if len(result.stdout) > 512 * 1024:
            raise SignalError("internal", "路由器响应不是完整 JSON；请检查固件依赖与连接后刷新。")
        return result.stdout
