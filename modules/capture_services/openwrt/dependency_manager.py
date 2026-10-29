"""OpenWrt 抓包依赖检查和安装边界。"""

import base64
from threading import Event

import hashlib
import re
from pathlib import Path

from .package_manager import PackageManager, package_error_reason, compare_opkg_versions
from .common_tool_dependencies import TOOLS, TOOL_KEYS, TOOL_BY_KEY, check_tools
from .dependency_groups import CAPTURE_PACKAGES

from modules.capture_services.openwrt.command_builder import quote_argument
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState
from modules.capture_services.openwrt.hcx_capabilities import SUPPORT_NOTE, read_hcx_capabilities
from modules.capture_services.openwrt.signal_commands import signal_probe_command
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.signal_package_feed import (
    default_cache_dir,
    download_profile_packages,
    normalize_profile,
    parse_installed,
    offline_cache_transaction,
)

# 抓包依赖离线包固定目录；写死不接受参数。
DEPENDENCY_PACKAGE_DIR = "/tmp/winbox-dependency-pkgs"
# 安装空间按计划中的新增包及依赖核算，不设置固定 MB 门槛。


def _removable_packages():
    """可卸载白名单：具名工具及用户明确选择的组件，不包含传递依赖。"""
    names = set()
    for key, packages in OpenWrtDependencyManager.FEATURE_PACKAGES.items():
        if key not in TOOL_BY_KEY or TOOL_BY_KEY[key].removable:
            names.update(packages)
    names.add("openssh-sftp-server")
    return frozenset(names)


class OpenWrtDependencyManager:
    """集中管理 opkg/apk 依赖，不修改软件源协议、不在检测阶段安装软件。"""

    FEATURE_PACKAGES = {
        **{tool.key: tool.package_names for tool in TOOLS},
        "terminal": ("tmux",),
        **CAPTURE_PACKAGES,
    }
    FEATURE_COMMANDS = {
        "terminal": ("tmux",),
        "scan": ("airodump-ng",),
        "handshake": ("airodump-ng", "aireplay-ng"),
        "pmkid": ("hcxdumptool",),
        "wps": ("reaver",),
        # 在线密码验证使用 OpenWrt 固件基础命令，不对应额外第三方根包。
        "password": ("uci", "wifi", "ip"),
    }

    def __init__(self, command_runner):
        self.command_runner = command_runner

    def required_packages(self, enabled_features):
        """返回启用功能需要的去重包名；纯计算，无系统副作用。"""

        packages = []
        for feature in enabled_features:
            for package in self.FEATURE_PACKAGES.get(feature, ()):
                if package not in packages:
                    packages.append(package)
        return tuple(packages)

    # 系统自带命令不查版本（无意义），只确认存在且可执行。
    _BASE_COMMANDS = frozenset({"uci", "wifi", "ip", "iw"})

    def check(self, enabled_features, sftp_available, cancel_event=None):
        """检查启用功能的真实命令：存在、可执行、版本号。

        只认 `command -v` 找到且 `test -x` 通过的命令；版本号用 `--version`/`-v`
        探测，探不到只记“版本未知”，不判缺失。坏的（存在但不可执行）按缺失处理，
        走安装覆盖修复。返回值：DependencyInfo 元组。副作用：只执行远端只读查询，
        不运行 opkg update/install。
        """

        results = []
        probes = {}
        for feature, display_name in (
            ("scan", "信号扫描"),
            ("handshake", "常规抓包"),
            ("pmkid", "PMKID"),
            ("wps", "秒 Pin"),
            ("password", "猜密码"),
        ):
            if not bool(enabled_features.get(feature, False)):
                results.append(DependencyInfo(feature, display_name, DependencyState.DISABLED))
                continue
            commands = self.FEATURE_COMMANDS.get(feature, ())
            if feature in {"scan", "handshake", "pmkid", "wps"}:
                commands += ("iw", "ip")
            missing_commands = []
            missing_names = []
            unknown_commands = []
            versions = []
            for command in commands:
                if command not in probes:
                    probes[command] = self._probe_command(command, cancel_event)
                state, version = probes[command]
                if state == "missing":
                    missing_commands.append(command)
                    missing_names.append(command)
                elif state == "broken":
                    missing_commands.append(f"{command}（存在但不可执行）")
                    missing_names.append(command)
                elif state != "ready":
                    unknown_commands.append(command)
                elif version:
                    versions.append(f"{command} {version}")
            packages = list(self.FEATURE_PACKAGES.get(feature, ())) if any(name not in {"iw", "ip"} for name in missing_names) else []
            for name, package in (("iw", "iw"), ("ip", "ip-tiny")):
                if name in missing_names and package not in packages:
                    packages.append(package)
            if unknown_commands:
                detail = "检测结果未确认：" + "、".join(unknown_commands)
            elif missing_commands:
                detail = "缺少命令：" + "、".join(missing_commands)
            elif versions:
                detail = "命令可用：" + "、".join(versions)
            else:
                detail = "命令可用"
            if feature == "pmkid" and not missing_commands:
                detail += (
                    "；已安装仅表示找到可执行命令；"
                    "不代表 hcxdumptool 与当前路由器固件、无线驱动的无线 API 兼容性已验证。"
                    + SUPPORT_NOTE
                )
            results.append(
                DependencyInfo(
                    feature,
                    display_name,
                    DependencyState.UNKNOWN if unknown_commands else DependencyState.MISSING if missing_commands else DependencyState.READY,
                    () if unknown_commands else tuple(packages),
                    detail,
                )
            )
        common_keys = TOOL_KEYS.intersection(key for key, enabled in enabled_features.items() if enabled)
        if common_keys:
            results.extend(check_tools(self.command_runner, common_keys, cancel_event))
        if enabled_features.get('terminal'):
            results.append(self.check_terminal(cancel_event))
        results.append(
            DependencyInfo(
                "sftp",
                "SFTP 同步",
                DependencyState.READY if sftp_available is True else DependencyState.MISSING if sftp_available is False else DependencyState.UNKNOWN,
                ("openssh-sftp-server",) if sftp_available is False else (),
                "SFTP 可用" if sftp_available is True else "文件传输支持未安装" if sftp_available is False else "文件传输检测失败，请重新检查",
            )
        )
        results.append(
            DependencyInfo(
                "converter",
                "结果转换",
                DependencyState.UNKNOWN,
                detail="将在确认 OpenWrt/Windows 转换器后检测",
            )
        )
        return tuple(results)

    def _probe_command(self, command, cancel_event):
        """探测单个命令，返回 (missing|broken|ready, 版本或空串)。

        系统自带命令只确认存在且可执行，不查版本。版本用 `--version`/`-v` 探测，
        探不到记空串（调用方显示“版本未知”仍判可用）。取消与超时直接抛错。
        """

        result = self.command_runner.run(
            f"dependency_{command}",
            f"p=$(command -v {command} 2>/dev/null); "
            f"[ -z \"$p\" ] && echo MISSING || "
            f"([ -x \"$p\" ] && echo \"OK:$p\" || echo BROKEN)",
            timeout=10,
            cancel_event=cancel_event,
        )
        if result.cancelled:
            raise RuntimeError("OpenWrt 依赖检测已取消。")
        if result.timed_out:
            raise RuntimeError(f"检测 OpenWrt 命令 {command} 超时。")
        lines = [line.strip() for line in (result.stdout or "").splitlines()]
        if "MISSING" in lines and result.exit_code == 0:
            return "missing", ""
        if "BROKEN" in lines:
            return "broken", ""
        if result.exit_code != 0 or not any(line.startswith("OK:") for line in lines):
            return "unknown", ""
        if command in self._BASE_COMMANDS:
            return "ready", "系统自带"
        version = self._probe_version(command, cancel_event)
        return "ready", version or "版本未知"

    def _probe_version(self, command, cancel_event):
        """hcxdumptool 单次读帮助且透传失败；其他工具沿用原版本显示探针。"""

        if command == "hcxdumptool":
            return read_hcx_capabilities(self.command_runner, cancel_event).version
        try:
            result = self.command_runner.run(
                f"dependency_{command}_version",
                f"({command} --version 2>&1 || {command} -v 2>&1 || true) | head -n 3",
                timeout=10,
                cancel_event=cancel_event,
            )
        except Exception:
            return ""
        if result.cancelled or result.timed_out:
            return ""
        match = re.search(r"(\d+\.\d+(?:\.\d+)?)", result.stdout or "")
        return match.group(1) if match else ""

    @offline_cache_transaction(lambda result: bool(result) and all(
        item.state in (DependencyState.READY, DependencyState.DISABLED) for item in result if item.key != "converter"))
    def install_all(self, enabled_features, sftp_available, device, cache_dir,
                    cancel_event, message=None, uploader=None, *, include_sftp=True):
        """确认后安装缺失的抓包依赖，装完复测返回最新清单。

        message 为播报回调 (text, level)；缺项为零直接返回复测结果。
        Local bundle first; router network next; PC download only on network failure.
        uploader 只用于单测注入，生产走 SSH 文件传输。
        """
        tell = message or (lambda _text, _level: None)
        tell("正在检查缺失依赖…", "info")
        def checked():
            return tuple(item for item in self.check(enabled_features, sftp_available, cancel_event)
                         if include_sftp or item.key != "sftp")
        initial = checked()
        if enabled_features.get('signal') and any(item.key == 'signal' and item.state != DependencyState.READY for item in initial):
            from .signal_manager import RouterSignalManager
            RouterSignalManager(self.command_runner).install_auto(device, cache_dir, cancel_event, tell, uploader)
            initial = checked()
        wanted = self._missing_packages(initial)
        if not wanted:
            tell("依赖齐全，无需安装。", "success")
            return checked()
        tell("本次安装：" + "、".join(wanted) + "。", "info")
        from .common_bundle_installer import install_from_local
        from .installation_sources import install_with_sources, report_verified, package_batches
        sources = []
        for index, batch in enumerate(package_batches(wanted)):
            if cancel_event is not None and cancel_event.is_set():
                raise SignalError("cancelled", "依赖安装已取消。")
            if index:
                # Earlier groups may have supplied a shared/transitive dependency.
                remaining = set(self._missing_packages(checked()))
                batch = tuple(name for name in batch if name in remaining)
            if not batch:
                continue
            source = install_with_sources(
                lambda: install_from_local(self, batch, device, cache_dir, sftp_available, cancel_event, tell, uploader),
                lambda: self._install_online(batch, cancel_event, tell),
                lambda: self._install_offline(batch, device, cache_dir, sftp_available, cancel_event, tell, uploader), tell)
            if source not in sources:
                sources.append(source)
        source = " / ".join(sources)
        tell("正在复测依赖…", "info")
        if include_sftp:
            sftp_available = self._probe_sftp(cancel_event, sftp_available)
        result = checked()
        report_verified(source, all(item.state in (DependencyState.READY, DependencyState.DISABLED)
                                    for item in result if item.key != "converter"), tell)
        return result

    def uninstall_packages(self, enabled_features, sftp_available, cancel_event,
                           message=None, selected=None, *, include_sftp=True):
        """确认后卸载选中的抓包依赖，卸完复测返回最新清单。

        selected 为空表示全卸已装项；非空时只卸其中已装的，未知包名直接拒绝。
        只卸白名单中明确选中的包，不连带清理依赖，不用 --autoremove/--force。
        """
        tell = message or (lambda _text, _level: None)
        tell("正在检查已装依赖…", "info")
        if not include_sftp and selected and "openssh-sftp-server" in selected:
            raise ValueError("文件传输不属于抓包依赖，不能从此分组卸载。")
        def checked():
            return tuple(item for item in self.check(enabled_features, sftp_available, cancel_event)
                         if include_sftp or item.key != "sftp")
        # Common tools use actual installed package names, not command availability.
        # A built-in/foreign executable must never select wpad or its provider.
        common_keys = tuple(key for key, enabled in enabled_features.items()
                            if enabled and key in TOOL_BY_KEY)
        package_manager = None
        common_installed = ()
        exact_selection = common_keys or selected is not None
        if exact_selection:
            from .tool_uninstall import installed_tool_packages
            package_manager = self._preflight(0, cancel_event)
            candidates = tuple(dict.fromkeys(package for key in common_keys
                               for package in TOOL_BY_KEY[key].package_names)) if common_keys else tuple(selected)
            if set(candidates) - _removable_packages():
                raise ValueError("未知的卸载软件包选择。")
            common_installed = installed_tool_packages(
                self.command_runner, package_manager, candidates, cancel_event)
        installed = list(common_installed)
        for item in (() if selected is not None and not common_keys else checked()):
            if item.key in common_keys:
                continue
            if item.key in TOOL_BY_KEY and not TOOL_BY_KEY[item.key].removable:
                continue
            if item.state.value != "ready":
                continue
            if item.key == "sftp":
                packages = ("openssh-sftp-server",)
            else:
                packages = self.FEATURE_PACKAGES.get(item.key, ())
            for package in packages:
                if package not in installed:
                    installed.append(str(package))
        if selected is None:
            targets = list(installed)
        else:
            unknown = [name for name in selected if name not in _removable_packages()]
            if unknown:
                raise SignalError(
                    "environment",
                    "拒绝卸载未知软件包：" + "、".join(unknown) + "；只允许卸载具名工具包。")
            targets = [name for name in selected if name in installed]
            skipped = [name for name in selected if name not in installed]
            for name in skipped:
                tell(f"{name} 未安装，跳过。", "info")
        if not targets:
            tell("未找到本项可卸载的独立软件包。如果命令仍可用，可能由固件或其他包提供；不会删除其他包。" if common_keys else "没有可卸载的抓包依赖。", "info" if common_keys else "success")
            return checked()
        if "signal" in common_keys:
            order = tuple(reversed(TOOL_BY_KEY["signal"].package_names))
            targets.sort(key=lambda name: order.index(name) if name in order else len(order))
        tell("本次卸载：" + "、".join(targets) + "。", "info")
        if "openssh-sftp-server" in targets:
            tell("注意：卸载后文件同步不可用，重装即恢复；SSH 命令通道不受影响。", "warning")
        package_manager = package_manager or self._preflight(0, cancel_event)
        total = len(targets)
        for position, package in enumerate(targets, start=1):
            tell(f"正在卸载 {package}（第 {position}/{total} 个）…", "info")
            command = self._package_change(package_manager, (package,), cancel_event, remove=True)
            self._execute_stage(
                "dependency_uninstall_" + package,
                command,
                120, cancel_event, f"卸载 {package}")
            tell(f"{package} 卸载完成。", "success")
        if exact_selection:
            remaining = installed_tool_packages(self.command_runner, package_manager,
                                                targets, cancel_event)
            if remaining:
                raise SignalError("environment", "卸载后仍安装：" + "、".join(remaining))
        if include_sftp:
            sftp_available = self._probe_sftp(cancel_event, sftp_available)
        tell("正在复测依赖…", "info")
        return checked()

    def check_terminal(self, cancel_event=None):
        """只读检查会话保留工具；超时不冒充未安装。"""
        from .command_runner import require_command_completion
        try:
            probe=self.command_runner.run('dependency_tmux',
                'p=$(command -v tmux 2>/dev/null); test -n "$p" && test -x "$p" || exit 44; tmux -V',
                timeout=10,cancel_event=cancel_event)
            require_command_completion(probe)
            state=(DependencyState.MISSING if probe.exit_code==44 else
                   DependencyState.READY if probe.exit_code==0 and (probe.stdout or '').strip().startswith('tmux ') else DependencyState.UNKNOWN)
            detail=(probe.stdout or '').strip() if state==DependencyState.READY else '会话保留工具未安装' if state==DependencyState.MISSING else '工具存在性或版本检测未通过'
        except Exception as exc:
            ssh=getattr(self.command_runner,'ssh_client',None)
            if cancel_event is not None and cancel_event.is_set():raise
            if ssh is not None and ssh.connected is False:raise
            state=DependencyState.UNKNOWN
            detail='会话保留支持检测失败：'+type(exc).__name__
        return DependencyInfo('terminal','终端会话保留',state,
            ('tmux',) if state==DependencyState.MISSING else (),detail)

    def check_sftp(self, cancel_event=None, *, fresh=False):
        """实际协商文件传输；仅确认服务程序缺失时判未安装，超时等错误保留未知状态。"""
        ssh=getattr(self.command_runner,"ssh_client",None)
        if ssh is None:
            return DependencyInfo("sftp","文件传输",DependencyState.UNKNOWN,detail="无法检测文件传输，请重新检查。")
        connection=ssh._client
        try:
            if fresh:ssh.drop_sftp()
            ssh.open_sftp(timeout=10,cancel_event=cancel_event)
            if ssh._client is not connection:raise RuntimeError("连接已变化。")
            return DependencyInfo("sftp","文件传输",DependencyState.READY,detail="文件传输可用")
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("文件传输检测已取消。") from exc
            if not ssh.connected or ssh._client is not connection:
                raise SignalError("transport","路由器远程连接已变化或断开。") from exc
            detail="文件传输检测失败："+str(exc)+"；请重新检查。"
            if not isinstance(exc,(TimeoutError,OSError)):
                try:
                    result=self.command_runner.run("dependency_sftp_presence",
                        'p=$(command -v sftp-server 2>/dev/null); '
                        'for f in "$p" /usr/lib/sftp-server /usr/libexec/sftp-server /usr/lib/openssh/sftp-server; do '
                        '[ -n "$f" ] && [ -x "$f" ] && { echo PRESENT; exit 0; }; done; echo MISSING',
                        timeout=10,cancel_event=cancel_event)
                    if cancel_event is not None and cancel_event.is_set():raise InterruptedError("文件传输检测已取消。")
                    if ssh._client is not connection or not ssh.connected:raise SignalError("transport","路由器远程连接已变化或断开。")
                    if not result.cancelled and not result.timed_out and result.exit_code==0 and result.stdout.strip()=="MISSING":
                        return DependencyInfo("sftp","文件传输",DependencyState.MISSING,
                                              ("openssh-sftp-server",),"文件传输服务程序未安装")
                except (InterruptedError,SignalError):raise
                except Exception as query_error:detail+=" "+str(query_error)
            return DependencyInfo("sftp","文件传输",DependencyState.UNKNOWN,detail=detail)

    def _probe_sftp(self, cancel_event, default):
        """安装后重新协商，不能把包管理器成功当作服务可用。"""
        if getattr(self.command_runner,"ssh_client",None) is None:return default
        item=self.check_sftp(cancel_event,fresh=True)
        return True if item.state==DependencyState.READY else False if item.state==DependencyState.MISSING else None

    @staticmethod
    def _missing_packages(checked):
        """从检测结果收集缺失包名；保持功能顺序，去重。"""
        wanted = []
        for item in checked:
            if item.state.value == "missing" and item.missing_packages:
                for package in item.missing_packages:
                    if package not in wanted:
                        wanted.append(str(package))
        return wanted

    def _install_online(self, packages, cancel_event, tell):
        """按实际包管理器更新源后逐包安装；联网失败转 offline_retry，其他失败诚实报错。"""
        package_manager = self._preflight(0, cancel_event)
        tell("正在更新软件源…", "info")
        from .installation_sources import network_retry_error
        try:
            self._execute_stage(
                "dependency_update", package_manager.update(), 180, cancel_event, "更新软件源")
            self._package_change(package_manager, tuple(packages), cancel_event)
            total = len(packages)
            for position, package in enumerate(packages, start=1):
                tell(f"正在下载安装 {package}（第 {position}/{total} 个）…", "info")
                command = self._package_change(package_manager, (package,), cancel_event)
                self._execute_stage(
                    "dependency_install_" + package,
                    command,
                    600, cancel_event, f"安装 {package}")
                tell(f"{package} 安装完成。", "success")
        except SignalError as exc:
            if exc.code == "transport" and _looks_like_network(str(exc)):
                raise network_retry_error(exc) from None
            raise


    def _install_offline(self, wanted, device, cache_dir, sftp_available,
                         cancel_event, tell, uploader=None, *, local_paths=None, local_packages=None, candidate_trial=False):
        """本机按档案下载闭包→传包→本地逐包安装；SFTP 缺失先用命令通道开路。

        核验由上层复测 `check()` 完成，这里只保证传装动作诚实报错。
        uploader 只用于单测注入，生产走 SSH 文件传输。
        """
        if str(getattr(device, "package_manager", "") or "").strip() == "apk":
            self._install_offline_apk(wanted, device, cache_dir, sftp_available,
                                      cancel_event, tell, uploader)
            return
        if local_paths is None:
            try:
                profile = normalize_profile(
                    getattr(device, "version", ""),
                    getattr(device, "package_architecture", ""),
                    getattr(device, "package_manager", ""),
                    distribution=getattr(device, "distribution", "OpenWrt"))
            except ValueError as exc:
                raise SignalError("environment", str(exc)) from None
            installed_text = self._execute_stage(
                "dependency_list_installed", "opkg list-installed",
                30, cancel_event, "读取已装软件包")
            try:
                paths = download_profile_packages(
                    profile, parse_installed(installed_text),
                    cache_dir or default_cache_dir(), cancel_event=cancel_event,
                    wanted=tuple(wanted), message=tell,
                    version_check=lambda a, op, b: compare_opkg_versions(self.command_runner, a, op, b, cancel_event))
            except ValueError as exc:
                raise SignalError("environment", str(exc)) from None
            except RuntimeError as exc:
                raise SignalError("transport", f"本机下载安装包失败：{exc}") from None
        else:
            paths = tuple(local_paths)
        if not paths:
            return
        from .package_space import ipk_installed_size, ipk_package_versions, required_kb
        if local_packages is None:
            local_packages = ipk_package_versions(paths)
        package_manager = self._preflight(required_kb(ipk_installed_size(path) for path in paths), cancel_event)
        sftp_ok = bool(sftp_available) and self._sftp_usable()
        if not sftp_ok:
            tell("文件传输通道不可用，改用命令通道传输安装包（较慢）。", "warning")
        self._execute_stage(
            "dependency_mkdir", "mkdir -p " + DEPENDENCY_PACKAGE_DIR,
            30, cancel_event, "准备传包目录")
        try:
            total = len(paths)
            if uploader is not None:
                for position, path in enumerate(paths, start=1):
                    if cancel_event is not None and cancel_event.is_set():
                        raise SignalError("cancelled", "依赖安装已取消。")
                    tell(f"正在传输 {path.name}（第 {position}/{total} 个）…", "info")
                    uploader(str(path), DEPENDENCY_PACKAGE_DIR + "/" + path.name)
            else:
                for position, path in enumerate(paths, start=1):
                    if cancel_event is not None and cancel_event.is_set():
                        raise SignalError("cancelled", "依赖安装已取消。")
                    tell(f"正在传输 {path.name}（第 {position}/{total} 个）…", "info")
                    if sftp_ok:
                        try:
                            self._sftp_upload(str(path), DEPENDENCY_PACKAGE_DIR + "/" + path.name)
                        except SignalError:
                            tell("文件传输中断，改用命令通道继续。", "warning")
                            sftp_ok = False
                            self._exec_push(str(path), DEPENDENCY_PACKAGE_DIR + "/" + path.name,
                                            cancel_event, tell)
                    else:
                        self._exec_push(str(path), DEPENDENCY_PACKAGE_DIR + "/" + path.name,
                                        cancel_event, tell)
            remote_paths = [DEPENDENCY_PACKAGE_DIR + "/" + path.name for path in paths]
            from .local_ipk_transaction import prepare_config, local_command, verify_local_plan
            if local_packages is not None:
                self._execute_stage("dependency_local_config", prepare_config(DEPENDENCY_PACKAGE_DIR),
                                    20, cancel_event, "\u51c6\u5907\u65e0\u8054\u7f51\u79bb\u7ebf\u5b89\u88c5")
            command = lambda simulate=False: (local_command(package_manager, remote_paths, simulate)
                if local_packages is not None else package_manager.local_install(remote_paths, simulate=simulate))
            try:
                plan = self._execute_stage("dependency_local_plan",
                    command(True), 90, cancel_event, "核对离线安装计划")
            except SignalError as exc:
                if candidate_trial and exc.code != "cancelled" and getattr(exc, "package_diagnostic", ""):
                    from .candidate_policy import CandidatePlanRejected
                    raise CandidatePlanRejected(exc) from exc
                raise
            try:
                package_manager.verify_plan(plan, ())
                if local_packages is not None:
                    verify_local_plan(plan, local_packages)
            except (ValueError, SignalError) as exc:
                error = exc if isinstance(exc, SignalError) else SignalError("environment", str(exc))
                error.package_diagnostic = self._package_diagnostic("dependency_local_plan validation", plan)
                if candidate_trial:
                    from .candidate_policy import CandidatePlanRejected
                    raise CandidatePlanRejected(error) from exc
                if error is exc:
                    raise
                raise error from exc
            tell(f"正在安装 {len(paths)} 个已校验离线包…", "info")
            self._execute_stage("dependency_offline_install",
                command(),
                600, cancel_event, "安装离线依赖")
        finally:
            from .offline_cleanup import cleanup_remote
            cleanup_remote(self._execute, "dependency_cleanup",
                           "rm -rf " + quote_argument(DEPENDENCY_PACKAGE_DIR), tell)

    def _install_offline_apk(self, wanted, device, cache_dir, sftp_available,
                             cancel_event, tell, uploader=None):
        """apk（25.12+）离线安装：传官方索引 → 路由器算依赖闭包 → PC 下载 → 传回 → 本地装。

        依赖闭包由路由器自身 apk 解析，PC 只按清单下载并对照官方 sha256 校验。
        """
        from .apk_offline import ApkOfflineInstaller
        upload = uploader or self._package_uploader(sftp_available, cancel_event, tell)

        def stage(command_name, command, timeout, label):
            return self._execute_stage(command_name, command, timeout, cancel_event, label)

        def check_space(plan):
            from .package_space import check_plan_space
            check_plan_space(self, PackageManager('apk'), plan, cancel_event,
                             repositories='/tmp/winbox-apk/repos.list')
        installer = ApkOfflineInstaller(stage, tell=tell, uploader=upload, space_check=check_space,
                                        cleanup_execute=self._execute)
        try:
            installer.install(tuple(wanted), device, cache_dir or default_cache_dir(), cancel_event)
        except ValueError as exc:
            raise SignalError("environment", str(exc)) from None
        except SignalError:
            raise
        except RuntimeError as exc:
            raise SignalError("transport", f"本机下载安装包失败：{exc}") from None

    def _package_uploader(self, sftp_available, cancel_event, tell):
        """依赖包文件上传：优先 SFTP，失败或不可用改命令通道分片传（与 opkg 离线一致）。"""
        sftp_ok = bool(sftp_available) and self._sftp_usable()

        def upload(local, remote):
            nonlocal sftp_ok
            if cancel_event is not None and cancel_event.is_set():
                raise SignalError("cancelled", "依赖安装已取消。")
            if sftp_ok:
                try:
                    self._sftp_upload(local, remote)
                    return
                except SignalError:
                    tell("文件传输中断，改用命令通道继续。", "warning")
                    sftp_ok = False
            self._exec_push(local, remote, cancel_event, tell)

        return upload

    def _preflight(self, min_overlay_kb, cancel_event):
        """复用信号探针做 opkg/root/空间预检；不满足直接拦截。"""
        import json
        try:
            data = json.loads(self._execute(
                "dependency_preflight", signal_probe_command(), 20, cancel_event))
            result = data["result"]
            root = result["root"] == 1
            overlay_kb = result.get("overlay_kb", -1)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise SignalError("internal", "路由器环境探针响应异常；请检查连接后重试。") from None
        try:
            package_manager = PackageManager.from_probe(result)
        except ValueError as exc:
            raise SignalError("environment", str(exc)) from exc
        if not root:
            raise SignalError(
                "environment",
                "当前远程连接用户不是 root，无权安装软件包；请用 root 连接后重试。")
        if isinstance(overlay_kb, int) and 0 <= overlay_kb < min_overlay_kb:
            raise SignalError(
                "environment",
                f"路由器可写空间剩余 {overlay_kb}KB，本次安装及预留需要 {min_overlay_kb}KB；未执行安装。")

        if min_overlay_kb > 0 and (type(overlay_kb) is not int or overlay_kb < 0):
            raise SignalError("environment", "无法确认路由器可写空间，未执行安装。")
        return package_manager

    def _package_change(self, package_manager, packages, cancel_event, *, remove=False):
        """两种包管理器都先预演，避免替换旧库或连带卸载。"""
        plan = ""
        try:
            plan = self._execute_stage("dependency_package_plan",
                package_manager.change(packages, remove=remove, simulate=True),
                60, cancel_event, "检查软件包操作计划")
            package_manager.verify_plan(plan, packages, remove=remove)
            if not remove:
                from .package_space import check_plan_space
                check_plan_space(self, package_manager, plan, cancel_event)
            return package_manager.change(packages, remove=remove)
        except SignalError as exc:
            if not getattr(exc, "package_diagnostic", None):
                exc.package_diagnostic = self._package_diagnostic("dependency_package_plan validation", plan)
            raise
        except ValueError as exc:
            reason=package_error_reason(plan)
            error=SignalError("environment", reason if reason!=package_error_reason("") else str(exc))
            error.package_diagnostic=self._package_diagnostic("dependency_package_plan",plan)
            raise error from exc

    @staticmethod
    def _package_diagnostic(stage, output):
        """\u4fdd\u7559\u6709\u754c\u3001\u8131\u654f\u7684\u5305\u7ba1\u7406\u5668\u539f\u6587\uff0c\u4f9b\u4fe1\u606f\u7a97\u53ca\u65e5\u5fd7\u5bfc\u51fa\u4f7f\u7528\u3002"""
        from modules.log_services import sanitized_text
        from modules.display_privacy import Masker
        text=sanitized_text(str(output or "")[-8192:],Masker())
        return stage+"\n"+text if text else stage+" (no output)"

    def _sftp_usable(self):
        """执行器 SSH 客户端是否存在；真连通性在首次上传时验证。"""
        return getattr(getattr(self, "command_runner", None), "ssh_client", None) is not None

    def _sftp_upload(self, local_path, remote_path):
        """经执行器 SSH 客户端上传单个文件；失败抛 transport 由上层转命令通道。

        成功不关闭共享句柄（复用长连接）；失败先丢弃缓存句柄再报错，
        否则后一个文件必死。
        """
        ssh_client = getattr(self.command_runner, "ssh_client", None)
        if ssh_client is None:
            raise SignalError("transport", "远程连接不可用，无法传输安装包。")
        try:
            sftp = ssh_client.open_sftp(timeout=60)
        except Exception:
            raise SignalError("transport", "文件传输通道打开失败。") from None
        try:
            sftp.put(local_path, remote_path)
        except Exception:
            try:
                ssh_client.drop_sftp()
            except Exception:
                pass
            raise SignalError("transport", "安装包传输中断。") from None

    def _exec_push(self, local_path, remote_path, cancel_event, message=None):
        """无 SFTP 时用命令通道分片传文件；验 sha256，不对版直接失败。

        分片进度逐段播报，长文件传输不再静默；取消只在片间检查，不掐断片内写入。
        """
        tell = message or (lambda _text, _level: None)
        path = Path(local_path)
        name = path.name
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise SignalError("cancelled", "依赖安装已取消。")
                block = stream.read(256 * 1024)
                if not block:
                    break
                size += len(block)
                digest.update(block)
        digest = digest.hexdigest()
        codec = self._execute(
            "dependency_push_codec",
            "if command -v base64 >/dev/null 2>&1; then echo base64; else echo printf; fi",
            10, cancel_event).strip()
        use_base64 = codec == "base64"
        remote_tmp = remote_path + ".b64" if use_base64 else remote_path
        self._execute(
            "dependency_push_init",
            "rm -f " + quote_argument(remote_tmp) + " " + quote_argument(remote_path)
            + " && : > " + quote_argument(remote_tmp),
            30, cancel_event)
        # Dropbear 的 exec 请求会把整条 shell 命令放进一个 SSH channel request。
        # 48KB 的 base64 参数在部分精简固件上超过服务端接受上限，首片就会被断开；
        # 8KB 留足 shell 转义和请求头余量，牺牲少量轮次换兼容性。
        # POSIX printf is a shell builtin on older OpenWrt without base64.
        # Five characters per escaped byte keep fallback requests below 8 KiB.
        step = 6 * 1024 if use_base64 else 1536
        total_parts = max(1, (size + step - 1) // step)
        tell(f"正在命令通道传输 {name}（共 {total_parts} 片）…", "info")
        with path.open("rb") as stream:
            for number, block in enumerate(iter(lambda: stream.read(step), b""), start=1):
                if cancel_event is not None and cancel_event.is_set():
                    raise SignalError("cancelled", "依赖安装已取消。")
                encoded = (base64.b64encode(block).decode("ascii") if use_base64 else
                           "".join("\\0%03o" % value for value in block))
                self._execute(
                    "dependency_push_part",
                    ("printf '%s' " if use_base64 else "printf '%b' ") + quote_argument(encoded)
                    + " >> " + quote_argument(remote_tmp),
                    30, cancel_event)
                if number == 1 or number == total_parts or number % 10 == 0:
                    tell(f"正在命令通道传输 {name}（{number}/{total_parts} 片）…", "info")
        tell(f"正在合并校验 {name}…", "info")
        if use_base64:
            self._execute(
                "dependency_push_join",
                "base64 -d " + quote_argument(remote_tmp)
                + " > " + quote_argument(remote_path)
                + " && rm -f " + quote_argument(remote_tmp),
                60, cancel_event)
        out = self._execute(
            "dependency_push_verify",
            "sha256sum " + quote_argument(remote_path),
            30, cancel_event)
        if not str(out or "").startswith(digest):
            raise SignalError("transport", "安装包传输校验失败；请检查连接后重试。")

    @staticmethod
    def _stage_reason(stderr):
        """按 opkg 常见错误关键字给具体原因；只做子串匹配，不回传原始输出。"""
        return package_error_reason(stderr)

    def _execute(self, command_name, command, timeout, cancel_event):
        """执行单条命令返回 stdout；取消/超时/非零退出转脱敏错误。"""
        if cancel_event is not None and cancel_event.is_set():
            raise SignalError("cancelled", "依赖安装已取消。")
        try:
            result = self.command_runner.run(
                command_name, command, timeout=timeout, cancel_event=cancel_event)
        except Exception:
            raise SignalError("transport", "路由器远程连接异常；请重新登录后刷新核对。") from None
        if result.cancelled:
            raise SignalError("cancelled", "依赖安装已取消。")
        if result.timed_out:
            raise SignalError("transport", "路由器命令超时；请检查路由器负载后重试。")
        if result.exit_code != 0:
            if command_name.startswith("dependency_push_part"):
                raise SignalError(
                    "transport",
                    "命令通道传输片被路由器拒绝；请保留当前日志后重试。",
                )
            raise SignalError("transport", "路由器命令执行失败；请检查连接和权限后重试。")
        return result.stdout

    def _execute_stage(self, command_name, command, timeout, cancel_event, stage):
        """分阶段执行安装命令；取消/超时/失败各自给明确说法。"""
        if cancel_event is not None and cancel_event.is_set():
            raise SignalError("cancelled", "依赖安装已取消。")
        try:
            result = self.command_runner.run(
                command_name, command, timeout=timeout, cancel_event=cancel_event)
        except Exception as exc:
            raise SignalError("transport", stage+"时远程连接异常；请重新登录后核对安装状态。") from exc
        if result.cancelled:
            raise SignalError("cancelled", "依赖安装已取消。")
        if result.timed_out:
            raise SignalError(
                "transport", f"{stage}超时；请检查路由器负载与网速后重试。")
        if result.exit_code != 0:
            output=(result.stdout or "")+"\n"+(result.stderr or "")
            reason=self._stage_reason(output)
            blocked=any(value in output.lower() for value in ("is depended upon by packages","is depended upon by other packages","has dependent packages"))
            error=SignalError(
                "environment" if blocked else "transport", reason if blocked else f"{stage}失败：{self._stage_reason(output)}；请处理后重试。")
            error.package_diagnostic=self._package_diagnostic(command_name+" exit="+str(result.exit_code),(result.stdout or "")+"\n"+(result.stderr or ""))
            raise error
        return (result.stdout or "") + "\n" + (result.stderr or "") if command_name.endswith("_plan") else result.stdout


def _looks_like_network(message):
    """仅明确联网错误才转代下载，不用离线重试掩盖签名或依赖冲突。"""
    return "联网或 DNS" in message
