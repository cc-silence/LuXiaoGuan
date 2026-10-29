from modules.capture_services.openwrt.radio_diagnostics import record_scan_selection, record_takeover_probe
from modules.capture_services.openwrt.password_verification import dictionary_paths
"""OpenWrt 远程抓包后端骨架。"""

import ipaddress
import json
import re
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path

from modules.capture_services.backends.base import CaptureBackend, CaptureBackendError
from modules.capture_services.openwrt.command_builder import quote_argument
from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.device_probe import OpenWrtDeviceProbe
from modules.capture_services.openwrt.device_probe import read_iface_types, read_iwdev_complete
from modules.capture_services.openwrt.device_probe import sta_link_down
from modules.capture_services.openwrt.handshake_capture import OpenWrtHandshakeCapture
from modules.capture_services.openwrt.handshake_capture import HandshakeCaptureResult, HandshakeCaptureUpdate
from modules.capture_services.openwrt.models import (
    AccessPointInfo,
    CaptureCleanupError,
    CaptureTaskType,
    OpenWrtConnectionResult,
    OpenWrtSessionSnapshot,
)
from modules.capture_services.openwrt.session_journal import SessionJournal
from modules.capture_services.openwrt.offline_package_repository import OfflinePackageRepository
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier
from modules.capture_services.openwrt.password_verification import PasswordVerifyCancelled
from modules.capture_services.openwrt.password_reporting import PasswordVerificationReporter
from modules.capture_services.openwrt.pmkid_capture import OpenWrtPmkidCapture
from modules.capture_services.openwrt.radio_session import OpenWrtRadioSession
from modules.capture_services.openwrt.radio_policy import IdleRadioUnavailable, require_idle_phy, phy_supports_channel
from modules.capture_services.openwrt.radio_takeover import OpenWrtRadioTakeover
from modules.capture_services.openwrt.result_converter import finalize_result_file, safe_result_stem
from modules.capture_services.openwrt.result_converter import converted_output_dir
from modules.capture_services.openwrt.result_converter import unique_result_path, write_22000
from modules.capture_services.openwrt.result_converter import write_hccapx
from modules.capture_services.openwrt.result_converter import analyze_handshake_cap, convertible_pairs
from modules.capture_services.openwrt.result_converter import CONVERTIBLE_VERDICTS as _CONVERTIBLE_VERDICTS
from modules.capture_services.openwrt.command_builder import normalize_bssid
from modules.capture_services.openwrt.hcx_filter import normalize_targets
from modules.capture_services.openwrt.wireless_diagnostics import (
    collect_tool_failure_diagnostics,
)
from modules.capture_services.openwrt.remote_process import RemoteProcessManager
from modules.capture_services.openwrt.remote_resources import CaptureResourceError, finished_cleanup_command
from modules.capture_services.openwrt.reconnect_cleanup import ReconnectCleanup
from modules.capture_services.openwrt.result_converter import CaptureResultConverter
from modules.capture_services.openwrt.ssh_client import OpenWrtSshClient
from modules.capture_services.openwrt.ssh_client import HostKeyChangedError
from modules.capture_services.openwrt.ssh_client import HostKeyConfirmationRequired
from modules.capture_services.openwrt.wifi_scanner import OpenWrtWifiScanner
from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture
from modules.capture_services.openwrt.wps_capture import WpsTargetResult
from modules.capture_services.openwrt.workspace import CaptureWorkspaceFactory, CaptureWorkspaceManager
from modules.capture_services.openwrt.workspace import local_session_root


def _channel_matches_band(channel, band):
    """判断单个信道整数归属 2.4G 或 5G 频段；信道未知时返回 False。"""

    try:
        channel = int(channel or 0)
    except (TypeError, ValueError):
        return False
    if channel <= 0:
        return False
    if band == "2.4G":
        return channel <= 14
    return channel > 14


def _parse_phy_frequency(line):
    """从 iw phy 频率行提取中心频率 MHz；无法识别时返回 None。

    标准输出形如 "* 2412 MHz [1] (30.0 dBm)"，其中 MHz 紧跟在频率数字之后。
    """

    from ..openwrt.phy_channels import parse_phy_channel
    row = parse_phy_channel(line)
    return row[0] if row is not None else None


# 多目标定向任务最多循环轮数；每轮跳过已出可跑包结论的目标。
_MAX_HANDSHAKE_ROUNDS = 3


def _maybe_drop_raw_cap(final_path, verdict, hc_path, settings):
    """“保留原始”关闭时删除无用原始 CAP，只留转换产物。

    参数：final_path 是已落盘的原始包；verdict 是解析结论；hc_path 是已生成的
    转换产物（22000 或 HCCAPX，为空表示没生成）；settings 是任务配置。返回应记入
    结果的 CAP 路径：删除成功返回空串。可跑包结论在转换产物齐全时删原始；无法解析
    的包按用户要求也不保留（开关默认开，此时仍保留）。转换产物缺失、有且仅有的
    证据一定保留；删除失败同样保留，避免静默丢失。
    """

    if bool((settings or {}).get("keep_raw_capture", True)):
        return str(final_path)
    if verdict in _CONVERTIBLE_VERDICTS and not hc_path:
        return str(final_path)
    if verdict not in _CONVERTIBLE_VERDICTS and verdict != "unknown":
        return str(final_path)
    try:
        Path(str(final_path)).unlink()
    except OSError:
        return str(final_path)
    return ""


def _convert_handshake_products(final_path, target, local_root, stem, verdict, settings):
    """可跑包结论的转换产物：22000（尊重开关）加 HCCAPX（兼容格式同步生成）。

    参数：final_path 是已落盘原始 CAP；target 是目标；local_root 是输出根目录；
    stem 是安全文件名词干；verdict 是解析结论；settings 是任务配置。
    返回 (hc22000_path, hc22000_note, hccapx_path, hccapx_note)；单项失败只记因
    不抛错。HCCAPX 只有 M1+M2 配对成功才写得出，纯 PMKID 会记因跳过。
    """

    hc_path, hc_note, hcx_path, hcx_note = "", "", "", ""
    if verdict not in _CONVERTIBLE_VERDICTS:
        return hc_path, hc_note, hcx_path, hcx_note
    if not bool((settings or {}).get("generate_22000", True)):
        hc_note = "22000 开关已关闭，未生成。"
    else:
        try:
            out22000 = unique_result_path(converted_output_dir(local_root), stem, ".22000")
            write_22000(str(final_path), target.ssid, target.bssid, out22000)
            hc_path = str(out22000)
        except Exception as exc:
            hc_note = f"22000 未生成：{exc}"
    try:
        out_hcx = unique_result_path(converted_output_dir(local_root), stem, ".hccapx")
        write_hccapx(str(final_path), target.ssid, target.bssid, out_hcx)
        hcx_path = str(out_hcx)
    except Exception as exc:
        hcx_note = f"HCCAPX 未生成：{exc}"
    return hc_path, hc_note, hcx_path, hcx_note


def _ordered_results(latest, order):
    """按原始目标顺序返回每目标最新一条结局；中断时未尝试的目标没有条目。"""

    return tuple(latest[bssid] for bssid in order if bssid in latest)


def _resolve_max_rounds(settings):
    """从配置读取多目标轮数上限；勾选无限返回 None（直到手动停止或全部完成）。

    数字非法时回退默认 3 轮，保证执行器永远有明确上限或明确的无限语义。
    """

    try:
        source = dict(settings or {})
    except (TypeError, ValueError):
        source = {}
    if source.get("handshake_rounds_infinite"):
        return None
    try:
        return max(1, min(int(source.get("handshake_rounds", _MAX_HANDSHAKE_ROUNDS)), 99))
    except (TypeError, ValueError):
        return _MAX_HANDSHAKE_ROUNDS


def _mask_name(settings, ssid, bssid):
    """\u8fd4\u56de\u539f\u59cb\u6807\u8bc6\uff0c\u8131\u654f\u7531\u663e\u793a\u5c42\u8d1f\u8d23\u3002"""
    return str(ssid or bssid or "")


def _mask_on(settings, which):
    """\u540e\u53f0\u4e0d\u518d\u51bb\u7ed3\u663e\u793a\u504f\u597d\uff1b\u4fdd\u7559\u8c03\u7528\u517c\u5bb9\u3002"""
    return False


def _session_diag_note(workspace):
    """非 SSH 失败也有现场文件时给出路径，保证导出能收录本次会话。

    save_ssh_failure 只收录带传输标记的异常；辅助自检、信道 Prep 等业务失败
    不带标记，现场诊断文件再多也不会被导出引用。消息里带上会话内诊断路径，
    信息窗的会话引用识别到后会自动收录（含无线诊断与 ssh-failure.json）。
    """

    try:
        local_dir = getattr(workspace, "local_dir", None)
        if not local_dir:
            return ""
        diags = sorted(Path(str(local_dir)).glob("*-wireless-diag.txt"))
    except Exception:
        return ""
    if not diags:
        return ""
    return f"；现场诊断已保存：{diags[0]}"


def _emit_target_message(message_callback, entry, mask_ssid=False, mask_bssid=False):
    """把单个目标结局转成信息窗一条播报，级别按结论确定性区分。

    不传 message_callback 时直接跳过，保持与旧调用完全兼容。
    """
    if message_callback is None:
        return
    name = str(entry.target.ssid or entry.target.bssid or "")
    result = entry.result
    if result is None:
        text = f"{name}：{entry.error or '本次未能完成。'}"
        level = "error"
    elif result.verdict in _CONVERTIBLE_VERDICTS:
        text = f"{name}：{result.verdict_detail}"
        level = "success"
    elif result.verdict == "none":
        text = f"{name}：{result.verdict_detail or '无有效结果，未保留文件。'}"
        level = "warning"
    elif result.verdict == "partial":
        text = f"{name}：{result.verdict_detail}"
        level = "warning"
    else:
        text = f"{name}：{result.verdict_detail}"
        level = "info"
    message_callback(text, level)


def _strip_useless_unknown(result):
    """unknown 且无可用完整帧则清空路径并注明自动复核结论；否则原样返回。

    自动复核已经数过包里一个可用帧都没有，再留就是堆垃圾；有零散帧的仍保留。
    """

    if result.verdict == "unknown" and not result.has_usable_frames:
        return replace(
            result, cap_path="",
            verdict_detail=result.verdict_detail + "包内无可用完整帧，未保留文件。")
    return result


class _BatchInterrupted(Exception):
    """批量任务被取消或 SSH 中断时的内部控制流，已完成部分保留。"""

    def __init__(self, note, status="cancelled", cleanup_errors=()):
        super().__init__(note)
        self.status = status
        self.cleanup_errors = tuple(cleanup_errors)


@dataclass(frozen=True)
class HandshakeTargetResult:
    """单个目标在本批次中的结局：成功时 result 有效，失败时 error 说明原因。"""

    target: AccessPointInfo
    result: HandshakeCaptureResult | None = None
    error: str = ""
    error_code: str = ""
    cleanup_errors: tuple = ()
    stop_reason: str = ""


@dataclass(frozen=True)
class HandshakeBatchResult:
    """一批次多目标定向任务的汇总。

    cancelled 仅表示取消；status 区分 completed/cancelled/disconnected/failed/
    idle_radio_unavailable。cleanup_errors 与主体结局并存，retained_results 保留前轮
    已落地文件；results 是各目标最后结局，清理失败也不能覆盖这些结果。
    planned_total 为去重后的计划目标数；零兼容旧调用方按返回条目计数。
    """

    cancelled: bool
    results: tuple
    note: str = ""
    status: str = "completed"
    cleanup_errors: tuple = ()
    retained_results: tuple = ()
    planned_total: int = 0


@dataclass(frozen=True)
class TakeoverProbeEntry:
    """只读射频普查的单个目标结论，与执行期选卡逻辑同口径。

    phy 是普查选中的无线网卡；busy 表示该 phy 仍有无线接口（执行期会走接管
    授权）；need_takeover 为真则本次任务必须先经主线程确认接管；error 非空
    表示该目标连选卡都失败，调用方应展示后中止启动，不进入抓包。
    """

    bssid: str
    ssid: str
    band: str
    channel: int = 0
    phy: str = ""
    phy_channel: int = 0
    busy: bool = False
    need_takeover: bool = False
    error: str = ""


def _emit_wps_message(message_callback, entry, mask_ssid=False, mask_bssid=False):
    """把单个目标的 Pin 结局转成信息窗播报，绝不携带 PIN 与密码内容。

    不传 message_callback 时直接跳过。成功只报结果文件路径。
    """

    if message_callback is None:
        return
    name = str(entry.ssid or entry.bssid or "")
    if entry.psk_ok:
        message_callback(f"{name}：已 Pin 出密码并写入：{entry.result_file}。", "success")
    elif entry.pin_ok:
        message_callback(f"{name}：仅拿到 PIN，已写入：{entry.result_file}。", "success")
    elif entry.tried_seconds:
        message_callback(f"{name}：跑了 {entry.tried_seconds} 秒，未拿到 PIN。" + (entry.note or ""), "warning")
    else:
        message_callback(f"{name}：{entry.note or '未能 Pin。'}", "warning")


def _fail_with_site(exc, workspace):
    """把本地会话现场目录附到失败信息中；该目录失败时保留、成功时清理。"""
    try:
        site = str(workspace.local_dir)
    except Exception:
        return exc
    text = str(exc)
    if site and site not in text:
        if isinstance(exc, CaptureCleanupError):
            return CaptureCleanupError(f"{text}；本次现场保留在：{site}。", exc.cleanup_errors,
                                       primary_error=exc.primary_message)
        return RuntimeError(f"{text}；本次现场保留在：{site}。")
    return exc


# 传输层已死的典型签名：异常瞬间 is_active() 可能还没翻转，不能只看连接状态，
# 见到这些直接判连接已断开，避免英文原文捅到界面。
_LOST_SOCKET_MARKERS = (
    "socket is closed",
    "socket exception",
    "connection reset",
    "broken pipe",
    "connection aborted",
    "session not active",
    "transport is closed",
    "no existing session",
)


def _ssh_lost_error(ssh_client, exc):
    """SSH 已断开时把级联报错翻译成明确结论，附带原始错误；连接正常则原样返回。"""

    try:
        connected = ssh_client.connected
    except Exception:
        connected = True
    text = f"{type(exc).__name__}: {exc}".lower()
    if connected and not any(marker in text for marker in _LOST_SOCKET_MARKERS):
        if "channel closed" in text:
            note = "本次路由器命令通道已关闭，任务未完成；SSH 登录连接仍可用。请重试任务，若再次失败请导出日志。"
            error = (CaptureCleanupError(note, exc.cleanup_errors, primary_error=exc.primary_error)
                     if isinstance(exc, CaptureCleanupError) else RuntimeError(note))
            error.__cause__ = exc
            return error
        return exc
    note = f"OpenWrt SSH 连接已断开，任务被迫中断。请到“路由器登录”重新登录后再重试。（原错误：{exc}）。"
    return CaptureCleanupError(note, exc.cleanup_errors, primary_error=exc.primary_message) if isinstance(exc, CaptureCleanupError) else RuntimeError(note)


class OpenWrtCaptureBackend(CaptureBackend):
    """定义 OpenWrt 远程抓包执行边界并校验连接配置。

    当前架构阶段不发起 SSH，也不修改路由器。后续 SSH、设备探测、远程 PID、配置
    备份恢复和 SFTP 下载都必须封装在此后端及其同目录协作者中，页面只传普通字典。
    """

    key = "openwrt"

    def __init__(self, *, ssh_client=None):
        self._running = False
        self.snapshot = OpenWrtSessionSnapshot.initial()
        from threading import Event
        self.shutdown_event = Event()
        self.ssh_client = ssh_client if ssh_client is not None else OpenWrtSshClient()
        self.command_runner = OpenWrtCommandRunner(self.ssh_client)
        self.process_manager = RemoteProcessManager(self.command_runner)
        self.device_probe = OpenWrtDeviceProbe(self.command_runner)
        self.dependency_manager = OpenWrtDependencyManager(self.command_runner)
        self.radio_session = OpenWrtRadioSession(self.command_runner)
        self.radio_takeover = OpenWrtRadioTakeover(self.command_runner)
        self.workspace_manager = CaptureWorkspaceManager(self.ssh_client, self.command_runner)
        self.wifi_scanner = OpenWrtWifiScanner(self.command_runner, self.process_manager)
        self.pmkid_capture = OpenWrtPmkidCapture(self.command_runner, self.process_manager)
        self.handshake_capture = OpenWrtHandshakeCapture(self.command_runner, self.process_manager)
        self.wps_capture = OpenWrtWpsCapture(self.command_runner, self.process_manager)
        self.password_verifier = OpenWrtPasswordVerifier(self.command_runner)
        self.result_converter = CaptureResultConverter()
        self.offline_packages = OfflinePackageRepository()
        # 本机已结束任务的会话 ID：其接管残留不等 10 分钟，下次连接立即重放；
        # 陌生新鲜记录仍跳过（可能是同机另一实例的活会话）。重启后清空，回到保守规则。
        self._finished_sessions = set()
        self.reconnect_cleanup = ReconnectCleanup(self)
        # 信号清单推送回调，由常驻服务装配（抓包 worker 在接管/恢复后顺手刷新
        # 信号页）。默认 None 即关闭，不影响任何抓包流程。
        self.signal_refresh_callback = None

    def _notify_signal_changed(self, cancel_event):
        """接管/恢复后推一次信号清单；返回页面是否已经接收最新快照。"""
        callback = self.signal_refresh_callback
        if callback is None:
            return True
        try:
            return callback(cancel_event) is not False
        except Exception:
            return False

    @staticmethod
    def validate_settings(settings):
        """校验 OpenWrt 地址、端口和用户。

        参数：settings 是配置管理器标准化后的 openwrt 字典。
        返回值：标准化后的 host、port、user 字典。
        副作用：无，不连接网络。主机名允许使用域名，IP 地址会额外做格式确认。
        """

        host = str(settings.get("host", "")).strip()
        user = str(settings.get("user", "")).strip()
        try:
            port = int(settings.get("port", 22))
        except (TypeError, ValueError) as exc:
            raise CaptureBackendError("SSH 端口必须是 1 到 65535 之间的数字。") from exc
        if not host:
            raise CaptureBackendError("请填写 OpenWrt 的 IP 地址或主机名。")
        if not user:
            raise CaptureBackendError("请填写 OpenWrt 的 SSH 用户名。")
        if not 1 <= port <= 65535:
            raise CaptureBackendError("SSH 端口必须是 1 到 65535 之间的数字。")
        if all(char.isdigit() or char in ".:" for char in host):
            try:
                ipaddress.ip_address(host)
            except ValueError as exc:
                raise CaptureBackendError(f"OpenWrt IP 地址格式不正确：{host}") from exc
        return {"host": host, "port": port, "user": user}

    def set_access_points(self, access_points):
        """替换共享 AP 快照；只更新内存模型，不执行扫描或远端命令。"""

        self.snapshot = replace(self.snapshot, access_points=tuple(access_points))
        return self.snapshot

    def set_selected_bssids(self, bssids):
        """按 BSSID 更新 AP 选择状态，供多个动作共用同一目标集合。"""

        selected = {str(value).upper() for value in bssids}
        points = tuple(
            point.with_selected(point.bssid.upper() in selected)
            for point in self.snapshot.access_points
        )
        return self.set_access_points(points)

    def connect(self, settings, password, expected_fingerprint="", cancel_event=None, progress_callback=None):
        """建立 SSH，随后执行设备、SFTP 和依赖只读探测。

        参数：settings 是标准化 OpenWrt 配置；password 仅用于本次认证；expected_fingerprint
        是已保存或刚确认的 SHA256 指纹。返回 OpenWrtConnectionResult。
        副作用：建立连接、探测和清理残留；仅重放身份匹配的恢复记录，可能恢复无线配置。
        """

        normalized = self.validate_settings(settings)
        self.ssh_client.configure(
            normalized["host"],
            normalized["port"],
            normalized["user"],
        )
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("OpenWrt 登录已取消。")
        try:
            key_info = self.ssh_client.connect(
                password=password,
                expected_fingerprint=expected_fingerprint,
                timeout=15,
                auth_method=settings.get("auth_method", "password"),
                private_key_path=settings.get("private_key_path", ""),
            )
        except HostKeyConfirmationRequired as exc:
            return OpenWrtConnectionResult(
                status="fingerprint_required",
                detail="首次连接此设备，需要确认 SSH 主机指纹。",
                fingerprint=exc.fingerprint,
                key_type=exc.key_type,
            )
        except HostKeyChangedError as exc:
            return OpenWrtConnectionResult(
                status="fingerprint_changed",
                detail="已保存的 SSH 主机指纹与当前设备不一致，需要确认是否为你的设备。",
                fingerprint=exc.actual_fingerprint,
                key_type=exc.key_type,
                expected_fingerprint=exc.expected_fingerprint,
            )
        self.command_runner.radio_diagnostics.bind_connection(key_info.fingerprint)
        self.command_runner.operation_diagnostics.bind_connection(key_info.fingerprint)
        if progress_callback is not None:
            try:
                progress_callback("正在读取路由器信息...")
            except Exception:
                pass
        try:
            recovery_note = self.reconnect_cleanup.recover(key_info.fingerprint, cancel_event)
            device = self.device_probe.probe(cancel_event)
            sftp=self.dependency_manager.check_sftp(cancel_event)
            sftp_available=True if sftp.state.value=="ready" else False if sftp.state.value=="missing" else None
            device = replace(device, sftp_available=sftp_available)
            dependencies = self.dependency_manager.check(
                dict(settings.get("enabled_features", {}), terminal=True),
                sftp_available, cancel_event)
            from modules.capture_services.openwrt.common_tool_dependencies import TOOL_KEYS, check_tools
            dependencies=tuple(sftp if item.key=="sftp" else item for item in dependencies)
            dependencies += check_tools(self.command_runner, TOOL_KEYS, cancel_event)
            dependencies = tuple(
                replace(item, detail=item.detail + "\uff1b\u5b89\u88c5\u65f6\u4f18\u5148\u68c0\u67e5\u5df2\u4e0b\u8f7d\u7684\u672c\u5730\u79bb\u7ebf\u5305\uff0c\u5e76\u6838\u5bf9\u5b9e\u9645\u4f9d\u8d56\u662f\u5426\u5339\u914d\u3002")
                if item.state.value == "missing" and item.missing_packages else item
                for item in dependencies
            )
            reaped = self.radio_session.reap_stale_monitors(cancel_event)
            detail = "OpenWrt SSH 登录和设备探测完成."
            self.pmkid_capture.tool_selection = None
            if sftp_available is True:
                from modules.capture_services.openwrt.compatible_tool import CompatibleToolInstaller
                try:
                    if CompatibleToolInstaller(self).restore(device, cancel_event):
                        detail += "已核验并恢复使用永久保存的兼容工具。"
                except InterruptedError:
                    raise
                except Exception as exc:
                    if cancel_event is not None and cancel_event.is_set():
                        raise
                    detail += "兼容工具未启用，继续使用原工具：" + str(exc)
            if recovery_note:
                detail += recovery_note
            if reaped > 0:
                detail += f"另清理了{reaped}个残留临时接口。"
            reaped_procs = self.process_manager.reap_stale_processes(
                cancel_event, force_sids=self._finished_sessions)
            if reaped_procs > 0:
                detail += f"另停止了{reaped_procs}个残留抓包进程。"
            residual_note = self._recycle_remote_residuals(cancel_event)
            if residual_note:
                detail += residual_note
            recover_note = self._recover_crashed_takeovers(cancel_event)
            if recover_note:
                detail += recover_note
            return OpenWrtConnectionResult(
                status="connected",
                detail=detail,
                fingerprint=key_info.fingerprint,
                key_type=key_info.key_type,
                device=device,
                dependencies=dependencies,
            )
        except Exception:
            self.ssh_client.close()
            raise

    _TMP_MIN_FREE_MB = 8

    def _check_remote_tmp(self, cancel_event=None):
        """读取远端 /tmp 可用 MB；读不到按未知放行，不因探测失败误杀任务。"""

        try:
            result = self.command_runner.run(
                "check_remote_tmp",
                "df -m /tmp 2>/dev/null | awk 'NR==2 {print $4}'",
                timeout=10,
                cancel_event=cancel_event,
            )
        except Exception:
            return None
        if result.cancelled or result.timed_out or result.exit_code != 0:
            return None
        try:
            return max(0, int(str(result.stdout or "").strip().split()[0]))
        except (ValueError, IndexError):
            return None

    def _ensure_remote_tmp(self, cancel_event=None):
        """保证远端 /tmp 可用空间；不足先做一次自家残留清理再复核，仍不足拒绝。

        真机证据：/tmp 写满会导致 netifd 无线配置重载生成空接口（嘴上说 up，
        实际什么都没建起来），随后所有恢复核实必败、整批 0/N。宁可在动无线
        之前就拦下，给出明确清理指引。
        """

        self.reconnect_cleanup.require_ready()
        free_mb = self._check_remote_tmp(cancel_event)
        if free_mb is None or free_mb >= self._TMP_MIN_FREE_MB:
            return
        # 可能是自家残留（跑飞进程/旧会话目录）占着：复用连接期同款清理再看一次。
        # 自家刚死的会话不等 10 分钟（连跑场景），陌生会话仍保守。
        try:
            self.process_manager.reap_stale_processes(
                cancel_event, force_sids=self._finished_sessions)
        except Exception:
            pass
        try:
            self._recycle_remote_residuals(cancel_event)
        except Exception:
            pass
        free_mb = self._check_remote_tmp(cancel_event)
        if free_mb is None or free_mb >= self._TMP_MIN_FREE_MB:
            return
        raise CaptureResourceError(
            f"路由器 /tmp 仅剩约 {free_mb} MB（需要至少 {self._TMP_MIN_FREE_MB} MB），"
            "无线配置重载与抓包都要写 /tmp，硬上会失败并可能留下半残信号。"
            "请重启路由器释放空间（多半是跑飞的抓包进程在持续写盘），"
            "或断开重连触发自动清理后重试。")

    def _recycle_remote_residuals(self, cancel_event):
        """只删除明确结束且没有进程或打开文件引用的自有目录，保留未知任务。"""

        try:
            result = self.command_runner.run(
                "recycle_residual_dirs",
                finished_cleanup_command(self._finished_sessions),
                timeout=30,
                cancel_event=cancel_event,
            )
        except Exception as exc:
            return f"远端残留清理未执行：{exc}"
        if result.cancelled or result.timed_out or result.exit_code != 0:
            return "远端残留清理未完成，下次连接重试。"
        return ""

    def _is_own_finished_session(self, journal_path):
        """判断 journal 是否属于本机已结束任务；是则新鲜也不用等 10 分钟。

        新鲜记录默认跳过是给同机另一实例的活会话留余量；本机已结束的任务不
        可能还活着，立即重放才能尽快修好半残 radio。解析失败按陌生处理。
        """

        try:
            data = json.loads(journal_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict):
            return False
        return str(data.get("session_id", "")) in self._finished_sessions

    def _recover_crashed_takeovers(self, cancel_event):
        """仅重放当前设备身份匹配的接管记录，并核对恢复结果。

        只处理 10 分钟以上的 journal（新鲜的可能是同机另一实例活会话的），
        本机已结束任务的残留除外（见 _finished_sessions，下次连接立即重放）。
        旧记录缺少身份或属于其他设备时保留且跳过；成功删除记录，失败保留并记因。
        """

        from modules.capture_services.openwrt.session_journal import SessionJournal

        try:
            journals = sorted(local_session_root().rglob("takeover-journal*.json"))
        except OSError:
            return ""
        now = time.time()
        recovered = 0
        problems = []
        unidentified = 0
        for path in journals:
            if (cancel_event is not None and cancel_event.is_set()) or not self.ssh_client.connected:
                problems.append("恢复核实已停止，未处理记录保留。")
                break
            try:
                journal_fresh = now - path.stat().st_mtime < 600
            except OSError:
                continue
            if journal_fresh and not self._is_own_finished_session(path):
                continue
            try:
                journal = SessionJournal.from_dict(
                    json.loads(path.read_text(encoding="utf-8", errors="replace"))
                )
            except (OSError, ValueError):
                problems.append(f"{path.name} 无法解析，已保留。")
                continue
            if journal is None or not journal.radio or not journal.interfaces:
                continue
            if journal.cleanup_stage == "restored":
                continue
            if not journal.device_fingerprint:
                unidentified += 1
                continue
            if journal.device_fingerprint != self.ssh_client.host_fingerprint:
                continue
            recovery_journal = journal
            if journal.config_token and journal.backup_path:
                snapshot_name = Path(journal.backup_path).name
                if snapshot_name == journal.backup_path:
                    try:
                        snapshot_text = (path.parent / snapshot_name).read_text(
                            encoding="utf-8", errors="strict")
                        recovered_phy, recovered_ifaces = self.radio_takeover.resolve_phy(
                            journal.radio, snapshot_text)
                        recovery_journal = replace(
                            journal, phy=recovered_phy, interfaces=tuple(recovered_ifaces))
                    except (OSError, UnicodeError, RuntimeError, ValueError):
                        pass
            error = self._recover_single_takeover(recovery_journal, cancel_event)
            if error is None:
                recovered += 1
                note = self._audit_recovered_takeover(recovery_journal, path, cancel_event)
                if note:
                    problems.append(note)
                try:
                    path.unlink()
                except OSError:
                    pass
            else:
                problems.append(error)
        note = ""
        if unidentified:
            note += f"{unidentified} 条旧恢复记录无法核实所属设备，已保留且未执行自动恢复。"
        if recovered:
            note += f"另恢复了{recovered}个异常中断的无线接管。"
        if problems:
            note += "恢复异常：" + "；".join(problems)
        return note

    def _recover_single_takeover(self, journal, cancel_event):
        """恢复单个接管：wifi up 后 30 秒内核对接口名回来，返回错误或 None。"""

        if not journal.device_fingerprint or journal.device_fingerprint != self.ssh_client.host_fingerprint:
            return "恢复记录与当前设备身份未核实，未执行恢复，记录已保留。"
        if journal.config_token:
            from modules.capture_services.openwrt.radio_takeover import RadioTakeoverRecord
            try:
                self.radio_takeover.restore(RadioTakeoverRecord(journal.session_id,
                    journal.radio, journal.phy, journal.interfaces, journal.config_token), cancel_event)
                return None
            except Exception as exc:
                return f"{journal.radio} 信号配置恢复未完成：{exc}"
        try:
            result = self.command_runner.run(
                "recover_wifi_up",
                f"wifi up {quote_argument(journal.radio)}",
                timeout=60,
                cancel_event=cancel_event,
            )
        except Exception as exc:
            return f"{journal.radio} 恢复命令失败：{exc}"
        if result.cancelled or result.timed_out or result.exit_code != 0:
            return f"{journal.radio} 重新启用失败，请手动执行 `wifi up {journal.radio}`。"
        deadline = time.monotonic() + 30
        while True:
            try:
                phy_interfaces, _channels, _diag = read_iwdev_complete(
                    self.command_runner, "recover_verify_ifaces", cancel_event
                )
            except Exception as exc:
                return f"{journal.radio} 已启用但接口核对失败：{exc}"
            present = set()
            for ifaces in phy_interfaces.values():
                present.update(ifaces)
            missing = [name for name in journal.interfaces if name not in present]
            if not missing:
                return None
            if time.monotonic() >= deadline:
                return (
                    f"{journal.radio} 已启用，但以下接口未回来：{', '.join(missing)}。"
                    f"请手动执行 `wifi up {journal.radio}` 并确认客户端重新上线。"
                )
            time.sleep(2)

    def _audit_recovered_takeover(self, journal, journal_path, cancel_event):
        """崩溃恢复成功后做一次只读自检并落盘取证。

        journal 无 phy 字段，由 radio 序号推导；自检失败按无差异处理，
        返回中文差异说明或空字符串，不影响恢复成功结论。
        """

        if not journal.radio:
            return ""
        from types import SimpleNamespace

        record = SimpleNamespace(
            radio=journal.radio, phy="", interfaces=journal.interfaces
        )
        try:
            note = self.radio_takeover.audit_restore(record, cancel_event)
        except Exception:
            return ""
        if not note:
            return ""
        try:
            (journal_path.parent / "takeover-restore-audit.txt").write_text(
                note + "\n", encoding="utf-8", errors="replace"
            )
        except OSError:
            pass
        return note

    def disconnect(self):
        """关闭 SFTP/SSH并返回初始页面快照，不修改路由器配置。"""

        self.ssh_client.close()
        self._running = False
        self.snapshot = OpenWrtSessionSnapshot.initial()
        return self.snapshot

    def scan_wifi(self, session_id, settings, cancel_event=None, progress_callback=None, message_callback=None):
        """执行单次受控 Wi-Fi 扫描，并在 finally 精确恢复远端临时资源。

        参数：settings 是已保存的普通扫描配置。返回 WifiScanResult。副作用：仅创建和
        删除本任务 monitor/目录，短时运行 airodump-ng；不改 UCI、不重载无线。
        """

        if not self.ssh_client.connected:
            raise RuntimeError("OpenWrt 尚未连接，不能开始 Wi-Fi 扫描。")
        self._ensure_remote_tmp(cancel_event)
        band = "5G" if settings.get("band") == "5G" else "2.4G"
        self.reconnect_cleanup.require_ready()
        workspace = CaptureWorkspaceFactory.create(session_id, local_session_root(), "/tmp/winbox-capture")
        self._running = True
        primary_error = None
        try:
            radio = self._find_scan_radio(
                band, cancel_event, idle_radio_only=bool(settings.get("idle_radio_only", False)),
                message_callback=message_callback,
            )
            from modules.capture_services.openwrt.management_link import require_management_link
            expected_phy = settings.get("management_scan_phy", "")
            expected_fingerprint = settings.get("management_scan_fingerprint", "")
            if ((expected_phy and expected_phy != radio) or
                    (expected_fingerprint and expected_fingerprint != self.ssh_client.host_fingerprint)):
                raise RuntimeError("扫描条件或登录连接已变化，请重新开始扫描。")
            require_management_link(self.command_runner, radio, cancel_event)
            self.workspace_manager.create(workspace, cancel_event)
            record = self.radio_session.create(
                session_id, radio, band, cancel_event=cancel_event,
                idle_radio_only=bool(settings.get("idle_radio_only", False)),
            )
            return self.wifi_scanner.scan(
                workspace,
                record.interface,
                session_id,
                band,
                settings.get("scan_seconds", 24),
                settings.get("refresh_seconds", 10),
                self.workspace_manager,
                cancel_event,
                progress_callback,
            )
        except Exception as exc:
            primary_error = _ssh_lost_error(self.ssh_client, exc)
            raise _fail_with_site(primary_error, workspace) from exc
        finally:
            self._finish_capture_task(workspace, primary_error)

    def capture_pmkid(
        self,
        session_id,
        settings,
        selected_bssids=(),
        cancel_event=None,
        progress_callback=None,
        message_callback=None,
    ):
        """先按实际工具能力准入，再采集 PMKID 并清理本任务临时资源。

        参数：手动过滤模式由 selected_bssids 指定目标；全局模式传空集合。返回
        PmkidCaptureResult。副作用：创建专属 monitor、运行本任务 hcxdumptool PID、同步
        原始 PCAPNG；beacon 按版本能力与当次确认执行，不发送 deauth，不修改 UCI。settings 中
        allow_radio_takeover 为真且目标频段无空闲 radio 时同样先快照再接管（跳频需要
        信道自由），确认必须由页面层在主线程提前完成。
        """

        if not self.ssh_client.connected:
            raise RuntimeError("OpenWrt 尚未连接，不能开始 PMKID 任务。")
        band = settings.get("band", "2.4G")
        selected_bssids = normalize_targets(selected_bssids)
        mode = settings.get("mode", "filter" if selected_bssids else "global")
        if mode not in {"filter", "global"} or (mode == "filter" and not selected_bssids):
            raise ValueError("PMKID 模式或目标无效，手动过滤必须选择至少一个目标。")
        if mode == "global" and selected_bssids:
            raise ValueError("PMKID 全局模式与所选目标冲突，请重新确认范围。")
        local_root = settings.get("resolved_output_dir")
        if not local_root:
            raise RuntimeError("结果目录无效，请重新选择 Windows 本地保存位置。")
        from modules.capture_services.openwrt.hcx_capabilities import validate_pmkid_mode
        validate_pmkid_mode(mode, bool(settings.get("pmkid_active_beacon", False)))
        command_options = self.pmkid_capture.prepare(
            band, settings.get("pmkid_active_beacon", False), selected_bssids, cancel_event,
            session_id=session_id,
        )
        from modules.capture_services.openwrt.bundled_compatible_tool import require_preferred_tool
        require_preferred_tool(self.snapshot.device,command_options.capabilities.version)
        Path(local_root).mkdir(parents=True, exist_ok=True)
        self.reconnect_cleanup.require_ready()
        workspace = CaptureWorkspaceFactory.create(session_id, local_session_root(), "/tmp/winbox-capture")
        self._running = True
        primary_error = None
        takeover_record = None
        try:
            phy = self._find_scan_radio(
                band, cancel_event, idle_radio_only=bool(settings.get("idle_radio_only", False)),
                message_callback=message_callback,
            )
            self.workspace_manager.create(workspace, cancel_event)
            takeover_record = self._maybe_takeover(
                workspace, session_id, phy, settings, cancel_event,
                message_callback=message_callback, expected_band=band,
            )
            record = self.radio_session.create(
                session_id, phy, band, cancel_event=cancel_event,
                idle_radio_only=bool(settings.get("idle_radio_only", False)),
                tool_managed=True,
            )
            if message_callback is not None:
                message_callback("正在检查 PMKID 工具启动和无线接口；检查通过后开始采集。", "info")
            result = self.pmkid_capture.capture(
                workspace,
                record.interface,
                session_id,
                int(settings.get("pmkid_duration_seconds", 0)),
                selected_bssids,
                self.workspace_manager,
                cancel_event,
                progress_callback,
                bool(settings.get("pmkid_active_beacon", False)),
                int(settings.get("pmkid_idle_timeout_seconds", 180) or 0),
                None,
                band=band, command_options=command_options,
                finalization_cancel_event=self.shutdown_event,
            )
            return self._analyze_pmkid_result(
                result, result.pcapng_path, local_root, settings,
                result_stem=self._pmkid_final_stem(band, session_id, result.filtered_bssids),
            )
        except Exception as exc:
            from modules.capture_services.capture_logging import save_ssh_failure
            save_ssh_failure(workspace.local_dir, exc, self.ssh_client.connected)
            primary_error = _ssh_lost_error(self.ssh_client, exc)
            raise _fail_with_site(primary_error, workspace) from exc
        finally:
            self._finish_capture_task(workspace, primary_error, takeover_record,
                                      message_callback=message_callback)

    def _analyze_pmkid_result(self, result, final_path, local_root, settings, *, result_stem=None):
        """先检查内容，只发布含可用组合的结果。

        result_stem 指定时从本任务临时目录发布；省略时分析调用方提供的路径。
        无效内容返回空产物路径，临时文件由会话收尾回收；无法判断时报检查失败。
        """
        try:
            analysis, pairs = analyze_handshake_cap(final_path)
        except Exception as exc:
            raise RuntimeError(f"抓包内容检查失败，未生成可用结果：{exc}") from exc
        combos = convertible_pairs(pairs)
        if result.filtered_bssids:
            allowed = set(normalize_targets(result.filtered_bssids))
            combos = {key: value for key, value in combos.items() if str(key[0]).upper() in allowed}
        if not combos:
            if analysis.status == "unknown":
                raise RuntimeError("抓包内容未能完成检查，未生成可用结果。")
            verdict = analysis.status if analysis.status in {"none", "partial"} else "none"
            return replace(result, pcapng_path="", verdict=verdict,
                           verdict_detail="本轮未抓到可用包，未保存文件。")
        if result_stem is not None:
            final_path = str(finalize_result_file(
                final_path, local_root, result_stem, ".pcapng",
            ))
        pmkid_count = sum(1 for verdict in combos.values() if verdict == "pmkid")
        ssid_by_bssid = {
            point.bssid.upper(): (point.ssid or "")
            for point in self.snapshot.access_points
        }
        hc_files = []
        hc_notes = []
        completed_bssids = tuple(dict.fromkeys(str(ap).upper() for ap, _sta in combos))
        converted = set()
        for (ap, _sta), verdict in combos.items():
            if ap in converted:
                continue
            converted.add(ap)
            ssid = ssid_by_bssid.get(str(ap).upper(), "")
            if not ssid or ssid == "__hidden__":
                hc_notes.append(f"{ap} 无信号名，跳过 22000。")
                continue
            if not bool((settings or {}).get("generate_22000", True)):
                hc_notes.append(f"{ssid}：22000 开关已关闭，未生成。")
                continue
            try:
                out = unique_result_path(
                    converted_output_dir(local_root), safe_result_stem(ssid, ap), ".22000"
                )
                write_22000(final_path, ssid, ap, out)
                hc_files.append((str(ap).upper(), ssid, str(out)))
            except Exception as exc:
                hc_notes.append(f"{ssid}：22000 未生成：{exc}")
        detail = f"发现 {len(combos)} 个可跑包组合（PMKID {pmkid_count} 个）。"
        verdict = "convertible"
        return replace(
            result,
            pcapng_path=final_path,
            verdict=verdict,
            verdict_detail=detail,
            convertible_count=len(combos),
            pmkid_count=pmkid_count,
            hc22000_files=tuple(hc_files),
            hc22000_note="".join(hc_notes),
            completed_bssids=completed_bssids,
        )

    def _pmkid_final_stem(self, band, session_id, filtered_bssids):
        """决定 PMKID 最终文件名词干：单目标用其信号名，否则用 band + 会话短 ID。"""

        ssid_by_bssid = {
            point.bssid.upper(): (point.ssid or "")
            for point in self.snapshot.access_points
        }
        names = {
            ssid_by_bssid.get(str(value).upper(), "")
            for value in (filtered_bssids or ())
        }
        if len(names) == 1:
            only = next(iter(names))
            if only:
                first = str((filtered_bssids or ("",))[0])
                return safe_result_stem(only, first)
        return f"pmkid-{band}-{str(session_id)[:8]}"

    def capture_handshake_passive(
        self,
        session_id,
        settings,
        target,
        cancel_event=None,
        progress_callback=None,
        message_callback=None,
    ):
        """对一个已扫描目标执行有限时长的被动 H 任务。

        返回 HandshakeCaptureResult。副作用：创建本任务 M 接口和目录、运行本任务 A 工具
        并同步原始 CAP；不执行 D 操作、不删除或修改任何 UCI 配置，任何退出路径都尝试
        精确清理。settings 中 allow_radio_takeover 为真且目标频段无空闲 radio 时，会先
        快照无线状态再临时关闭对应 radio（抓完自动恢复）；settings 中 active_deauth
        为真时，会按间隔对目标已发现客户端逐个发送定点去认证小包（需用户在设置页开启
        并在启动确认中授权）；两个确认都必须由页面层在主线程提前完成，本方法不创建弹窗。
        """

        if not self.ssh_client.connected:
            raise RuntimeError("OpenWrt 尚未连接，不能开始定向握手任务。")
        local_root = settings.get("resolved_output_dir")
        if not local_root:
            raise RuntimeError("结果目录无效，请重新选择 Windows 本地保存位置。")
        Path(local_root).mkdir(parents=True, exist_ok=True)
        self._ensure_remote_tmp(cancel_event)
        self.reconnect_cleanup.require_ready()
        workspace = CaptureWorkspaceFactory.create(session_id, local_session_root(), "/tmp/winbox-capture")
        self._running = True
        primary_error = None
        takeover_record = None
        try:
            phy = self._find_scan_radio(
                target.band, cancel_event, target.channel,
                bool(settings.get("idle_radio_only", False)), message_callback,
            )
            self.workspace_manager.create(workspace, cancel_event)
            takeover_record = self._maybe_takeover(
                workspace, session_id, phy, settings, cancel_event,
                target_channel=target.channel,
                expected_band=target.band,
                message_callback=message_callback,
            )
            record = self.radio_session.create(
                session_id,
                phy,
                target.band,
                cancel_event=cancel_event,
                idle_radio_only=bool(settings.get("idle_radio_only", False)),
            )
            result = self.handshake_capture.capture_passive(
                workspace,
                record.interface,
                session_id,
                target,
                settings.get("idle_timeout_seconds", 180) or 180,
                self.workspace_manager,
                cancel_event,
                progress_callback,
                bool(settings.get("active_deauth", False)),
                "",
                lambda: self.radio_session.heal_link(cancel_event),
            )
            if result.verdict in ("none", "partial"):
                # 包里没有 EAPOL，或有 EAPOL 但凑不出可跑包组合，密码学上无价值，
                # 不落盘；会话临时目录在成功清理时一并消失。
                return replace(result, cap_path="")
            result = _strip_useless_unknown(result)
            if result.verdict == "unknown" and not result.cap_path:
                return result
            stem = safe_result_stem(target.ssid, target.bssid)
            final_path = finalize_result_file(
                result.cap_path,
                local_root,
                stem,
                ".cap",
            )
            hc_path, hc_note, hcx_path, hcx_note = _convert_handshake_products(
                final_path, target, local_root, stem, result.verdict, settings
            )
            # 原始包删除看“有没有转换产物剩下”，22000 与 HCCAPX 任一成功即可。
            kept_cap = _maybe_drop_raw_cap(
                final_path, result.verdict, hc_path or hcx_path, settings
            )
            return replace(
                result,
                cap_path=kept_cap,
                hc22000_path=hc_path,
                hc22000_note=hc_note,
                hccapx_path=hcx_path,
                hccapx_note=hcx_note,
            )
        except Exception as exc:
            primary_error = _ssh_lost_error(self.ssh_client, exc)
            raise _fail_with_site(primary_error, workspace) from exc
        finally:
            self._finish_capture_task(workspace, primary_error, takeover_record)

    def verify_passwords(
        self,
        session_id,
        settings,
        targets,
        cancel_event=None,
        progress_callback=None,
        message_callback=None,
        pause_control=None,
    ):
        """对多个已扫描目标逐个在线试密码，返回 (结局元组, 恢复异常说明)。

        高风险任务：临时 STA 会改动路由器 wireless/network/firewall，验证器先唯一
        备份、finally 手术恢复；本方法不做 radio 接管（STA 需在业务 radio 上关联），
        确认必须由页面层在主线程提前完成。字典为空直接抛错，已试部分保留计数。
        """

        targets = tuple(targets or ())
        if not targets:
            raise ValueError("在线密码验证至少需要一个已扫描目标。")
        if not self.ssh_client.connected:
            raise RuntimeError("OpenWrt 尚未连接，不能开始在线密码验证。")
        local_root = settings.get("resolved_output_dir")
        if not local_root:
            raise RuntimeError("结果目录无效，请重新选择 Windows 本地保存位置。")
        dictionary = dictionary_paths(settings)
        if not dictionary or any(not Path(path).is_file() for path in dictionary):
            raise RuntimeError("密码字典不存在，请先在抓包设置中选择字典文件。")
        Path(local_root).mkdir(parents=True, exist_ok=True)
        self.reconnect_cleanup.require_ready()
        workspace = CaptureWorkspaceFactory.create(session_id, local_session_root(), "/tmp/winbox-capture")
        self._running = True
        primary_error = None
        restore_errors = ""
        reporter = PasswordVerificationReporter(targets, settings, message_callback, progress_callback)
        candidates = ()
        try:
            self.workspace_manager.create(workspace, cancel_event)
            try:
                candidates, skipped = OpenWrtPasswordVerifier.load_candidates(dictionary, cancel_event)
            except InterruptedError as exc:
                raise PasswordVerifyCancelled("在线密码验证已取消。") from exc
            if not candidates:
                raise RuntimeError("字典中没有可用候选（只要 8-64 字符的非空行）。")
            if message_callback is not None:
                skip_note = f"，跳过 {skipped} 行" if skipped else ""
                message_callback(
                    f"字典载入 {len(candidates)} 个候选{skip_note}，开始逐个试连。",
                    "info",
                )
            results = self.password_verifier.verify_targets(
                targets,
                candidates,
                session_id,
                Path(local_root),
                cancel_event,
                reporter.progress,
                reporter.target_event,
                pause_control=pause_control,
            )
            restore_errors = self.password_verifier.last_restore_errors
            if cancel_event is not None and cancel_event.is_set():
                raise PasswordVerifyCancelled("在线密码验证已取消。")
            if restore_errors:
                restore_errors = "路由器配置恢复未完成，请检查无线配置和网络状态后重新连接。"
        except Exception as exc:
            reporter.interrupted(isinstance(exc, PasswordVerifyCancelled))
            primary_error = _ssh_lost_error(self.ssh_client, exc)
            # 验证期间的异常可能回显含 key 的命令，禁止转发到 worker 消息/诊断。
            if candidates:
                if isinstance(exc, PasswordVerifyCancelled):
                    primary_error = RuntimeError("在线密码验证已取消。")
                    if self.password_verifier.last_restore_errors:
                        primary_error = RuntimeError(
                            "在线密码验证已取消；路由器配置恢复未完成，请重新连接并检查无线配置和网络状态。"
                        )
                elif primary_error is not exc:
                    primary_error = RuntimeError("OpenWrt SSH 连接已断开，请重新登录后检查配置恢复情况。")
                else:
                    primary_error = RuntimeError(
                        "在线密码验证执行或恢复出错，请检查路由器连接、无线配置和结果目录权限。"
                    )
            raise _fail_with_site(primary_error, workspace) from exc
        finally:
            # V 结尾的网络重启本来就可能闪断 SSH：主体成功时远端临时目录清不掉
            # 只记入恢复备注，不推翻“已找到密码”的成功结论；主体失败仍按原链路抛错。
            if pause_control is not None:
                pause_control.finish()
            try:
                self._finish_capture_task(workspace, primary_error)
            except RuntimeError as cleanup_exc:
                if primary_error is None:
                    restore_errors = "；".join(
                        filter(None, [restore_errors, "远端临时目录未清理，请重新连接并检查配置恢复情况。"])
                    )
                else:
                    raise RuntimeError(
                        f"{primary_error}；清理临时资源失败，请重新连接并检查配置恢复情况。"
                    ) from cleanup_exc
        return results, restore_errors

    def run_wps(
        self,
        session_id,
        settings,
        targets,
        mode,
        cancel_event=None,
        progress_callback=None,
        message_callback=None,
    ):
        """对多个已扫描目标逐个跑 Pin，返回结局元组。

        参数 mode 是 normal/second/offline/combo。每个目标独立建 monitor（占用则
        按确认走接管）、跑完精确恢复；单个目标失败记因继续下一个，只有取消或 SSH
        中断才整体中断（已完成部分保留）。确认必须由页面层在主线程提前完成。
        """

        targets = tuple(targets or ())
        if not targets:
            raise ValueError("WPS 任务至少需要一个已扫描目标。")
        if mode not in ("normal", "second", "offline", "combo"):
            raise ValueError(f"不支持的 Pin 模式：{mode}。")
        if not self.ssh_client.connected:
            raise RuntimeError("OpenWrt 尚未连接，不能开始 WPS 任务。")
        local_root = settings.get("resolved_output_dir")
        if not local_root:
            raise RuntimeError("结果目录无效，请重新选择 Windows 本地保存位置。")
        Path(local_root).mkdir(parents=True, exist_ok=True)
        self.reconnect_cleanup.require_ready()
        workspace = CaptureWorkspaceFactory.create(session_id, local_session_root(), "/tmp/winbox-capture")
        self._running = True
        primary_error = None
        options = dict(settings.get("wps_defaults", {}) or {})
        results = []
        try:
            self.workspace_manager.create(workspace, cancel_event)
            for index, target in enumerate(targets):
                if cancel_event is not None and cancel_event.is_set():
                    raise _BatchInterrupted("任务已取消。")
                if not self.ssh_client.connected:
                    raise _BatchInterrupted(
                        "OpenWrt SSH 连接已断开，任务被迫中断。"
                    )
                label = _mask_name(settings, target.ssid, target.bssid)
                if message_callback is not None:
                    message_callback(
                        f"正在 Pin 第 {index + 1}/{len(targets)} 个：{label}。",
                        "info",
                    )
                takeover_record = None
                errors = []
                try:
                    phy = self._find_scan_radio(
                        target.band, cancel_event, idle_radio_only=bool(settings.get("idle_radio_only", False)),
                        message_callback=message_callback,
                    )
                    takeover_record = self._maybe_takeover(
                        workspace, session_id, phy, settings, cancel_event,
                        f"w{index}", task_type="wps",
                        expected_band=target.band,
                        message_callback=message_callback,
                    )
                    record = self.radio_session.create(
                        session_id, phy, target.band, cancel_event=cancel_event,
                        idle_radio_only=bool(settings.get("idle_radio_only", False)),
                    )
                except Exception as exc:
                    # 接管完成后 M 接口准备失败也必须恢复 radio，不能让后续手动
                    # 启停看到“配置已启用、运行接口未回来”的半恢复状态。
                    errors.extend(self._restore_task_wireless(workspace, takeover_record, message_callback))
                    if cancel_event is not None and cancel_event.is_set():
                        detail = "；".join(errors)
                        if detail:
                            raise _BatchInterrupted(f"任务已取消；清理异常：{detail}") from exc
                        raise _BatchInterrupted("任务已取消。") from exc
                    if not self.ssh_client.connected:
                        detail = "；".join(errors)
                        suffix = f"；清理异常：{detail}" if detail else ""
                        raise _BatchInterrupted(
                            f"OpenWrt SSH 连接已断开，任务被迫中断（原错误：{exc}）。{suffix}"
                        ) from exc
                    results.append(WpsTargetResult(
                        bssid=str(target.bssid or ""), ssid=str(target.ssid or ""),
                        mode=mode,
                        note="；".join(
                            filter(None, [f"准备 monitor 失败：{exc}"] + errors)
                        ),
                    ))
                    continue
                try:
                    outcome = self.wps_capture.run_targets(
                        workspace,
                        record.interface,
                        session_id,
                        (target,),
                        mode,
                        options,
                        Path(local_root),
                        self.workspace_manager,
                        cancel_event,
                        progress_callback,
                    )[0]
                except Exception as exc:
                    if cancel_event is not None and cancel_event.is_set():
                        raise _BatchInterrupted("任务已取消。") from exc
                    if not self.ssh_client.connected:
                        raise _BatchInterrupted(
                            f"OpenWrt SSH 连接已断开，任务被迫中断（原错误：{exc}）。"
                        ) from exc
                    outcome = WpsTargetResult(
                        bssid=str(target.bssid or ""), ssid=str(target.ssid or ""),
                        mode=mode, note=str(exc),
                    )
                finally:
                    errors.extend(self._restore_task_wireless(workspace, takeover_record, message_callback))
                if errors:
                    outcome = replace(
                        outcome,
                        note="；".join(filter(None, [outcome.note] + errors)),
                    )
                results.append(outcome)
            for outcome in results:
                _emit_wps_message(message_callback, outcome, mask_ssid=_mask_on(settings, "ssid"),
                      mask_bssid=_mask_on(settings, "bssid"))
            return tuple(results)
        except Exception as exc:
            primary_error = _ssh_lost_error(self.ssh_client, exc)
            raise _fail_with_site(primary_error, workspace) from exc
        finally:
            self._finish_capture_task(workspace, primary_error, None, message_callback)

    def capture_handshakes_passive(
        self,
        session_id,
        settings,
        targets,
        cancel_event=None,
        progress_callback=None,
        message_callback=None,
        *,
        target_callback=None,
        stop_on_idle_unavailable=False,
        group_same_channel=False,
    ):
        """对多个已扫描目标逐个执行定向任务，最多循环指定轮数。

        每轮跳过已出可跑包结论的目标；普通目标失败继续，取消/断连/恢复失败整体中断。
        自动调用方通过 stop_on_idle_unavailable 要求空闲拒绝停整轮；手动默认继续。
        target_callback 在目标落盘后、下一目标与最终清理前回传结构化结果。
        副作用与单目标一致，只是 M 接口按目标创建删除、接管按目标恢复、远端目录在
        最后统一清理；任一确认都必须由页面层在主线程提前完成，本方法不创建弹窗。
        message_callback(text, level) 是线程安全的过程播报，目标开始与结束时逐条通知。
        """

        unique = {}
        for target in targets or ():
            key = normalize_bssid(target.bssid)
            normalized = replace(target, bssid=key)
            if key in unique and unique[key] != normalized:
                raise ValueError("目标存在重复且冲突的记录，请重新扫描后确认。")
            unique[key] = normalized
        targets = tuple(unique.values())
        from modules.capture_services.openwrt.channel_capture import group_channel_targets
        if group_same_channel:
            group_channel_targets(targets)
        if not targets:
            raise ValueError("多目标定向任务至少需要一个已扫描目标。")
        if not self.ssh_client.connected:
            raise RuntimeError("OpenWrt 尚未连接，不能开始定向握手任务。")
        local_root = settings.get("resolved_output_dir")
        if not local_root:
            raise RuntimeError("结果目录无效，请重新选择 Windows 本地保存位置。")
        Path(local_root).mkdir(parents=True, exist_ok=True)
        self._ensure_remote_tmp(cancel_event)
        self.reconnect_cleanup.require_ready()
        workspace = CaptureWorkspaceFactory.create(session_id, local_session_root(), "/tmp/winbox-capture")
        self._running = True
        primary_error = None
        latest = {}
        retained = []
        cleanup_errors = []
        status = "completed"
        note = ""
        order = [target.bssid for target in targets]
        from modules.capture_services.handshake_processing import DeferredHandshake, HandshakeProcessingQueue
        from modules.capture_services.openwrt.capture_capacity import bounded_groups, read_target_capacity
        processing = HandshakeProcessingQueue()

        def consume(group, entries, terminal=False):
            nonlocal primary_error
            group_interruption = None
            for target, (entry, retry) in zip(group, entries):
                previous = latest.get(target.bssid)
                if entry.result is not None and entry.result.verdict in _CONVERTIBLE_VERDICTS:
                    retry = False
                elif entry.error or entry.error_code or entry.result is None:
                    retry = False
                elif (previous is not None and previous.result is not None
                      and entry.result.content_fingerprint
                      and entry.result.content_fingerprint == previous.result.content_fingerprint):
                    entry = replace(entry, stop_reason="no_progress", result=replace(
                        entry.result, verdict_detail=entry.result.verdict_detail
                        + "连续两轮完整等待后的目标内容相同，无进展，已停止重复采集。"))
                    retry = False
                elif entry.result.verdict == "unknown" and max_rounds is None:
                    entry = replace(entry, stop_reason="unknown", result=replace(
                        entry.result, verdict_detail="包文件无法完成检查，本目标已停止。"
                        + ("原始记录已保留。" if entry.result.has_usable_frames else "未保留文件。")))
                    retry = False
                if previous and previous.result and any((
                    previous.result.cap_path, previous.result.hc22000_path, previous.result.hccapx_path,
                )):
                    retained.append(previous)
                latest[target.bssid] = entry
                if entry.error or entry.error_code:
                    primary_error = RuntimeError(entry.error or entry.error_code)
                cleanup_errors.extend(entry.cleanup_errors)
                if target_callback is not None:
                    target_callback(entry)
                if entry.cleanup_errors:
                    group_interruption = _BatchInterrupted("目标清理失败，已停止后续目标。", "failed")
                if stop_on_idle_unavailable and entry.error_code == "idle_radio_unavailable":
                    group_interruption = _BatchInterrupted(entry.error, "idle_radio_unavailable")
                if cancel_event is not None and cancel_event.is_set():
                    group_interruption = _BatchInterrupted("任务已取消。")
                if group_interruption is not None and len(group) == 1 and not terminal:
                    raise group_interruption
                if group_interruption is None:
                    _emit_target_message(message_callback, entry, mask_ssid=_mask_on(settings, "ssid"),
                         mask_bssid=_mask_on(settings, "bssid"))
                if retry:
                    pending.append(target)
            if group_interruption is not None and not terminal:
                raise group_interruption

        try:
            self.workspace_manager.create(workspace, cancel_event)
            pending = list(targets)
            total = len(pending)
            max_rounds = _resolve_max_rounds(settings)
            round_no = 0
            while True:
                round_no += 1
                if max_rounds is not None and round_no > max_rounds:
                    break
                if not pending:
                    break
                if message_callback is not None:
                    message_callback(
                        f"进入第 {round_no} 轮，还有 {len(pending)} 个目标未出可跑包结果。",
                        "info",
                    )
                current = list(pending)
                pending = []
                groups = group_channel_targets(current) if group_same_channel else tuple((t,) for t in current)
                index = 0
                for group in bounded_groups(groups, lambda: read_target_capacity(self.command_runner, cancel_event)):
                    ready = processing.pop(wait=processing.full)
                    while ready is not None:
                        consume(*ready)
                        ready = processing.pop()
                    if cancel_event is not None and cancel_event.is_set():
                        raise _BatchInterrupted("任务已取消。")
                    if not self.ssh_client.connected:
                        raise _BatchInterrupted("OpenWrt SSH 连接已断开。", "disconnected")
                    if message_callback is not None:
                        labels = '、'.join(_mask_name(settings, t.ssid, t.bssid) for t in group)
                        message_callback(f"第 {round_no} 轮：{group[0].band} 信道 {group[0].channel}，同时采集 {len(group)} 个目标：{labels}", "info")
                    args = (workspace, session_id, settings, local_root, group[0], index,
                            total, round_no, cancel_event, progress_callback, message_callback)
                    captured = self._capture_single_handshake(
                        *args, group_targets=group if len(group) > 1 else (), defer_processing=True)
                    index += len(group)
                    if isinstance(captured, DeferredHandshake):
                        processing.submit(group, captured)
                    else:
                        consume(group, captured if len(group) > 1 else (captured,))
                # Retry and no-progress decisions use finalized results from this round.
                ready = processing.pop(wait=True)
                while ready is not None:
                    consume(*ready)
                    ready = processing.pop(wait=True)
        except _BatchInterrupted as exc:
            status, note = exc.status, str(exc)
            cleanup_errors.extend(exc.cleanup_errors)
            primary_error = exc
        except Exception as exc:
            primary_error = _ssh_lost_error(self.ssh_client, exc)
            status = "cancelled" if cancel_event is not None and cancel_event.is_set() else "failed"
            note = str(_fail_with_site(primary_error, workspace))
        finally:
            # Drain downloaded jobs even after cancellation/failure, before workspace cleanup.
            try:
                ready = processing.pop(wait=True)
                while ready is not None:
                    consume(*ready, terminal=True)
                    ready = processing.pop(wait=True)
            except Exception as exc:
                status = "failed"
                note = str(exc)
            finally:
                processing.close()
            try:
                if progress_callback is not None:
                    progress_callback(HandshakeCaptureUpdate(0, 0, stage="cleaning"))
            except Exception as exc:
                status = "failed"
                note = "；".join(filter(None, (note, f"发布清理进度失败：{exc}")))
            finally:
                try:
                    self._finish_capture_task(workspace, primary_error, None, message_callback)
                except Exception as exc:
                    cleanup_errors.extend(getattr(exc, "cleanup_errors", (str(exc),)))
        cancelled = cancel_event is not None and cancel_event.is_set()
        if cancelled:
            status = "cancelled"
            note = "任务已取消。" + note if "已取消" not in note else note
        elif cleanup_errors and status == "completed":
            status = "failed"
        if primary_error is not None and not note:
            note = str(_fail_with_site(primary_error, workspace))
        return HandshakeBatchResult(
            cancelled or status == "cancelled", _ordered_results(latest, order), note,
            status, tuple(dict.fromkeys(cleanup_errors)), tuple(retained), len(targets),
        )

    def _capture_single_handshake(
        self,
        workspace,
        session_id,
        settings,
        local_root,
        target,
        index,
        total,
        round_no,
        cancel_event,
        progress_callback,
        message_callback=None,
        *, group_targets=(), defer_processing=False,
    ):
        """执行批次中的单个目标，返回 (HandshakeTargetResult, 是否值得再试)。

        取消与 SSH 中断直接抛出，由批量循环转为部分返回；其他异常记入该目标后
        继续下一个目标。恢复错误结构化返回，由外层停止批次；已落地产物仍保留。
        """

        group = tuple(group_targets) or (target,)
        def failed_entries(entry, retry):
            return tuple((replace(entry, target=t), retry) for t in group) if group_targets else (entry, retry)
        label = '、'.join(_mask_name(settings, t.ssid, t.bssid) for t in group)
        takeover_record = None
        try:
            self._ensure_remote_tmp(cancel_event)
            phy = self._find_scan_radio(
                target.band, cancel_event, target.channel,
                bool(settings.get("idle_radio_only", False)), message_callback,
            )
            takeover_record = self._maybe_takeover(
                workspace,
                session_id,
                phy,
                settings,
                cancel_event,
                f"r{round_no}t{index}",
                target_channel=target.channel,
                expected_band=target.band,
                message_callback=message_callback,
            )
            record = self.radio_session.create(
                session_id,
                phy,
                target.band,
                cancel_event=cancel_event,
                idle_radio_only=bool(settings.get("idle_radio_only", False)),
            )
        except Exception as exc:
            # 接管成功后，创建 M 接口仍可能因驱动、取消或连接中断失败。此前此分支
            # 直接返回，漏掉 radio 恢复，导致 UCI 显示“已启用”但运行接口未回来。
            # 准备阶段也必须按正常收尾顺序精确恢复本任务动过的对象。
            from modules.capture_services.capture_logging import save_ssh_failure
            local_dir = getattr(workspace, "local_dir", None)
            diagnostic = (
                save_ssh_failure(local_dir, exc, self.ssh_client.connected)
                if local_dir is not None else ""
            )
            primary_message = str(exc).strip() or type(exc).__name__
            if diagnostic:
                primary_message += f"；连接诊断已保存：{diagnostic}"
            else:
                primary_message += _session_diag_note(workspace)
            preparation_errors = self._restore_task_wireless(workspace, takeover_record)
            if isinstance(exc, CaptureResourceError):
                raise _BatchInterrupted(primary_message, "resource_limit", preparation_errors) from exc
            if cancel_event is not None and cancel_event.is_set():
                raise _BatchInterrupted("任务已取消。", cleanup_errors=preparation_errors) from exc
            if not self.ssh_client.connected:
                raise _BatchInterrupted(
                    f"OpenWrt SSH 会话不可用，任务被迫中断（原错误：{primary_message}）。",
                    "disconnected", preparation_errors,
                ) from exc
            return failed_entries(HandshakeTargetResult(
                target, None, primary_message,
                "idle_radio_unavailable" if isinstance(exc, IdleRadioUnavailable) else "preparation_failed",
                tuple(preparation_errors),
            ), False)

        def labeled(progress):
            if progress_callback is None:
                return
            progress_callback(
                replace(
                    progress,
                    target_label=label,
                    target_index=index,
                    target_total=total,
                    round_no=round_no,
                    target_bssid=target.bssid,
                    target_bssids=tuple(t.bssid for t in group),
                )
            )

        errors = []
        try:
            result = self.handshake_capture.capture_passive(
                workspace,
                record.interface,
                session_id,
                target,
                settings.get("idle_timeout_seconds", 180) or 180,
                self.workspace_manager,
                cancel_event,
                labeled,
                bool(settings.get("active_deauth", False)),
                f"r{round_no}t{index}",
                lambda: self.radio_session.heal_link(cancel_event),
                **({"group_targets": group} if group_targets else {}),
                **({"defer_analysis": True} if defer_processing else {}),
            )
        except Exception as exc:
            errors.extend(getattr(exc, "cleanup_errors", ()))
            from modules.capture_services.capture_logging import save_ssh_failure
            diagnostic = save_ssh_failure(workspace.local_dir, exc, self.ssh_client.connected)
            errors.extend(self._restore_task_wireless(workspace, takeover_record, message_callback))
            primary_message = getattr(exc, "primary_message", str(exc)) or type(exc).__name__
            if diagnostic:
                primary_message += f"；连接诊断已保存：{diagnostic}"
            else:
                primary_message += _session_diag_note(workspace)
            if cancel_event is not None and cancel_event.is_set():
                raise _BatchInterrupted("任务已取消。" + _session_diag_note(workspace), cleanup_errors=errors) from exc
            if not self.ssh_client.connected:
                raise _BatchInterrupted(f"OpenWrt SSH 会话不可用，任务被迫中断（原错误：{primary_message}）。", "disconnected", errors) from exc
            from modules.capture_services.openwrt.command_runner import CommandExecutionTimeout
            primary = getattr(exc, "primary_error", None) or exc
            if isinstance(primary, CaptureResourceError):
                raise _BatchInterrupted(primary_message, "resource_limit", errors) from exc
            if isinstance(primary, CommandExecutionTimeout):
                raise _BatchInterrupted(primary_message, "command_timeout", errors) from exc
            return failed_entries(HandshakeTargetResult(
                target, None, primary_message,
                "idle_radio_unavailable" if isinstance(exc, IdleRadioUnavailable) else "capture_failed",
                tuple(errors),
            ), False)
        errors.extend(self._restore_task_wireless(workspace, takeover_record))
        from modules.capture_services.handshake_processing import DeferredHandshake
        from modules.capture_services.openwrt.handshake_capture import DownloadedHandshake

        def finish():
            # All remote work and restoration have finished before this handoff.
            analyzed = result.analyze() if isinstance(result, DownloadedHandshake) else result
            items = analyzed if group_targets else (analyzed,)
            callback = (lambda update: None) if defer_processing else labeled
            entries = []
            for t, item in zip(group, items):
                try:
                    entries.append(self._save_handshake_entry(item, settings, local_root, t, errors, callback))
                except Exception as exc:
                    entries.append((HandshakeTargetResult(t, error=str(exc) or type(exc).__name__,
                                    error_code="processing_failed", cleanup_errors=tuple(errors)), False))
            if isinstance(result, DownloadedHandshake) and not any(entry.error for entry, _ in entries):
                try:
                    result.release()
                except OSError as exc:
                    entries = [(replace(entry, cleanup_errors=entry.cleanup_errors + (str(exc),)), retry)
                               for entry, retry in entries]
            return tuple(entries)

        if defer_processing and not errors:
            return DeferredHandshake(finish)
        entries = finish()
        return entries if group_targets else entries[0]

    def _save_handshake_entry(self, result, settings, local_root, target, errors, labeled):
        """复用逐目标的判据、转换和保存，无用包仍不落盘。"""
        if result.verdict in ("none", "partial"):
            # 无 EAPOL 或凑不出可跑包组合都不落盘（无密码学价值），但都值得再试；
            # 调用方按 verdict 区分显示，partial 标“有反应、值得再试”。
            note = "；".join(errors)
            return (
                HandshakeTargetResult(target, replace(result, cap_path="", verdict_detail="本轮未抓到可用包。"), note, cleanup_errors=tuple(errors)),
                True,
            )
        if result.verdict == "unknown" and not result.has_usable_frames:
            # 自动复核过了：一个可用完整帧都没有，再留就是堆垃圾，不落盘但值得再试。
            note = "；".join(errors)
            return (
                HandshakeTargetResult(target, _strip_useless_unknown(result), note,
                                      cleanup_errors=tuple(errors)),
                True,
            )
        stem = safe_result_stem(target.ssid, target.bssid)
        labeled(HandshakeCaptureUpdate(0, 0, stage="saving"))
        try:
            final_path = finalize_result_file(
                result.cap_path, local_root, stem, ".cap"
            )
        except Exception as exc:
            return HandshakeTargetResult(target, None, str(exc), "save_failed", tuple(errors)), False
        hc_path, hc_note, hcx_path, hcx_note = _convert_handshake_products(
            final_path, target, local_root, stem, result.verdict, settings
        )
        final_result = replace(
            result,
            cap_path=_maybe_drop_raw_cap(
                final_path, result.verdict, hc_path or hcx_path, settings
            ),
            hc22000_path=hc_path,
            hc22000_note=hc_note,
            hccapx_path=hcx_path,
            hccapx_note=hcx_note,
        )
        error_text = "；".join(errors)
        retry = result.verdict not in _CONVERTIBLE_VERDICTS
        return HandshakeTargetResult(target, final_result, error_text, cleanup_errors=tuple(errors)), retry

    def _maybe_takeover(self, workspace, session_id, phy, settings, cancel_event, tag="", task_type="handshake", target_channel=0, message_callback=None, expected_band=""):
        """用户已在主线程确认且 phy 被占用时快照并接管，否则返回 None。

        确认必须由页面层提前完成，本方法不创建弹窗；仅已核实空闲时直接返回 None，
        不做任何事。tag 用于区分同一会话内多个目标的快照文件名，task_type 写入
        接管记录供恢复流程识别任务类型。target_channel 仅用于定向 H；批次授权
        不代表每个目标都需接管，执行前重新核对实际选中 phy 的占用状态。"""

        if bool(settings.get("idle_radio_only", False)):
            # 创建接口前由 RadioSession 再次核实；任何接管授权均不能覆盖严格模式。
            return None
        authorized = bool(settings.get("allow_radio_takeover"))
        if not authorized and not target_channel:
            return None
        phy_interfaces, channels, diag = read_iwdev_complete(
            self.command_runner, "check_phy_free", cancel_event
        )
        if not phy_interfaces.get(phy) and (
            phy in phy_interfaces or phy in set(diag["expected"] or ())
        ):
            return None
        if not authorized:
            raise RuntimeError(
                "所选无线网卡仍有业务接口，同信道也需要临时接管，但本次尚未授权。"
                "请重新连接更新设备信息，再确认接管后重试。"
            )
        if message_callback is not None:
            reason = "定向采集需要独立使用射频，同信道不豁免接管" if target_channel else "任务需要独立使用射频/跳频"
            message_callback(f"选卡复核：{phy} 存在业务接口，{reason}，按本次确认临时接管，结束后恢复。", "warning")
        expected_band = expected_band or (
            "2.4G" if target_channel and target_channel <= 14 else
            "5G" if target_channel else ""
        )
        record = self._takeover_phy_for_session(
            workspace, session_id, phy, cancel_event, tag, task_type, expected_band)
        if message_callback is not None:
            try:
                message_callback(
                    f"已临时接管 {record.radio}（同频段信号短暂中断），信号管理页已同步最新状态。",
                    "info",
                )
            except Exception:
                pass
        return record

    def _takeover_phy_for_session(self, workspace, session_id, phy, cancel_event, tag="", task_type="handshake", expected_band=""):
        """为本次任务快照并接管被占用的 phy，返回恢复凭据。

        参数 tag 区分同一会话内多个目标，避免快照与记录文件名互相覆盖；为空时
        沿用旧文件名。副作用：抓取无线快照并写入本次本地目录（wireless-snapshot
        与 takeover-journal，前缀按 tag 区分，供失败排查与下次连接恢复）；随后临
        时禁用对应 radio 原本启用的信号。任一步失败都直接抛异常，不进入抓包。
        """

        device_fingerprint = self.ssh_client.host_fingerprint
        if not device_fingerprint:
            raise RuntimeError("无法核实接管设备身份，拒绝修改无线配置。")
        snapshot_text = self.radio_takeover.snapshot_wireless(cancel_event)
        radio = self.radio_takeover.resolve_radio(
            phy, snapshot_text, cancel_event, expected_band=expected_band)
        safe_tag = "".join(char for char in str(tag) if char.isalnum())[:8]
        snapshot_name = f"wireless-snapshot-{safe_tag}.txt" if safe_tag else "wireless-snapshot.txt"
        journal_name = f"takeover-journal-{safe_tag}.json" if safe_tag else "takeover-journal.json"
        try:
            workspace.local_dir.mkdir(parents=True, exist_ok=True)
            # 快照含路由器无线明文密码，落盘前脱敏；内存原文仅用于本次映射，不再外传。
            redacted = re.sub(r"\.key='[^']*'", ".key='***'", snapshot_text)
            (workspace.local_dir / snapshot_name).write_text(
                redacted, encoding="utf-8", errors="replace"
            )
        except OSError as exc:
            raise RuntimeError(f"无线快照本地落盘失败，拒绝接管无线网卡：{exc}") from exc
        record = self.radio_takeover.prepare_config_takeover(phy, radio, session_id, cancel_event)
        journal = SessionJournal(
            session_id=str(session_id),
            task_type=str(task_type or CaptureTaskType.HANDSHAKE.value),
            remote_work_dir=str(workspace.remote_dir),
            monitor_section="",
            backup_path=snapshot_name,
            cleanup_stage=f"takeover:{radio}",
            radio=radio,
            interfaces=tuple(record.interfaces),
            phy=record.phy,
            config_token=record.config_token,
            device_fingerprint=device_fingerprint,
        )
        try:
            (workspace.local_dir / journal_name).write_text(
                json.dumps(journal.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as exc:
            raise RuntimeError(f"接管记录本地落盘失败，拒绝继续抓包：{exc}") from exc
        self.radio_takeover.takeover_prepared(record, cancel_event)
        if not self._notify_signal_changed(cancel_event):
            raise RuntimeError("接管后信号列表未完成同步，已停止启动抓包并进入恢复。")
        return record

    def _restore_task_wireless(self, workspace, takeover_record, message_callback=None, cancel_event=None):
        """恢复本任务 monitor 与接管 radio，返回清理错误列表，不抛异常。

        供单目标收尾与多目标逐个收尾共用；调用方决定错误如何合并上报。
        SSH 已中断时不再逐条发命令碰运气（此前每条都会吃满超时再报
        Timeout opening channel），直接记一条推迟说明，残留由下次连接
        的 journal 重放与 wbmon 收尸自动处理。
        """

        from modules.capture_services.openwrt.radio_takeover import RadioTakeoverRecord
        pending = getattr(self.radio_takeover, "pending_record", None)
        if takeover_record is None and isinstance(pending, RadioTakeoverRecord):
            takeover_record = pending

        def _say(text):
            if message_callback is not None:
                try:
                    message_callback(text, "info")
                except Exception:
                    pass

        if not self.ssh_client.connected:
            if self.radio_session.active or takeover_record is not None:
                return ["SSH 会话不可用，临时接口与接管恢复待下次连接后核实清理。"]
            return []
        errors = []
        if any(h.program in ("airbase-ng", "hcxdumptool") and str(h.remote_work_dir) == str(workspace.remote_dir)
               for h in self.process_manager.handles()):
            return ["辅助采集进程停止尚未确认，无线恢复暂缓；请重新登录后清理。"]
        _say("正在删除临时无线接口…")
        try:
            self.radio_session.restore(cancel_event=cancel_event)
        except Exception as exc:
            errors.append(f"临时无线接口：{exc}")
        if not self.ssh_client.connected:
            errors.append("SSH 会话不可用，临时接口与接管恢复待下次连接后核实清理。")
            return errors
        if takeover_record is not None:
            _say("正在恢复被接管的无线…")
            try:
                self.radio_takeover.restore(takeover_record, cancel_event=cancel_event)
            except Exception as exc:
                diag_name = collect_tool_failure_diagnostics(
                    self.command_runner,
                    workspace,
                    self.workspace_manager,
                    "restore",
                    cancel_event=cancel_event,
                ) if self.ssh_client.connected else ""
                note = "；无线诊断已一并保留。" if diag_name else ""
                errors.append(f"无线恢复：{exc}{note}")
            else:
                if getattr(takeover_record, "config_token", ""):
                    try:
                        for path in workspace.local_dir.glob("takeover-journal*.json"):
                            data = json.loads(path.read_text(encoding="utf-8"))
                            if data.get("config_token") != takeover_record.config_token:
                                continue
                            data["cleanup_stage"] = "restored"
                            temporary = path.with_suffix(".json.part")
                            temporary.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
                            temporary.replace(path)
                    except (OSError, ValueError, TypeError) as exc:
                        errors.append(f"信号已恢复，但恢复记录更新失败：{type(exc).__name__}")
                try:
                    audit_note = self.radio_takeover.audit_restore(
                        takeover_record, cancel_event=cancel_event
                    )
                except Exception:
                    audit_note = ""
                if audit_note:
                    try:
                        (workspace.local_dir / "takeover-restore-audit.txt").write_text(
                            audit_note + "\n", encoding="utf-8", errors="replace"
                        )
                    except OSError:
                        pass
                    errors.append(audit_note)
        if self.ssh_client.connected:
            self._notify_signal_changed(cancel_event=cancel_event)
        return errors

    def _finish_capture_task(self, workspace, primary_error=None, takeover_record=None,
                               message_callback=None):
        """收尾本任务资源，并在失败时同时保留主错误和清理错误。

        参数：workspace 是本次唯一目录；primary_error 是任务主体异常或 None；
        takeover_record 是本次接管的恢复凭据或 None。返回值：无。副作用：先删除本
        任务 M 接口，再恢复被接管的 radio，最后清理远端目录；清理失败会抛出包含资源
        名称的异常，M 接口删除失败时保留 radio_session.record，供后续恢复流程重试。
        """

        # 任务结束即登记会话：其接管残留下次连接不等 10 分钟直接重放（陌生会话仍保守）。
        try:
            finished_sid = str(getattr(workspace, "session_id", "") or "")
            if finished_sid:
                self._finished_sessions.add(finished_sid)
                if len(self._finished_sessions) > 256:
                    self._finished_sessions.clear()
        except Exception:
            pass

        def _say(text):
            if message_callback is not None:
                try:
                    message_callback(text, "info")
                except Exception:
                    pass

        _say("正在清理路由器临时资源…")
        journal = self.command_runner.operation_diagnostics
        journal.record("cleanup", operation="restore_wireless", status="started",
            connected_before=bool(self.ssh_client.connected), takeover=takeover_record is not None,
            primary_failed=primary_error is not None,
            pending_processes=len(self.process_manager.handles()))
        cleanup_errors = self._restore_task_wireless(workspace, takeover_record, message_callback)
        wireless_cleanup_confirmed = not cleanup_errors
        journal.record("cleanup", operation="restore_wireless",
            status="failed" if cleanup_errors else "completed",
            connected_after=bool(self.ssh_client.connected),
            reason="restore_unconfirmed" if cleanup_errors else "restore_confirmed")
        if not self.ssh_client.connected:
            cleanup_errors.append("SSH 会话不可用，远端工作目录待下次连接后核实清理。")
        elif any(h.program in ("airbase-ng", "hcxdumptool") and str(h.remote_work_dir) == str(workspace.remote_dir)
                 for h in self.process_manager.handles()):
            cleanup_errors.append("辅助进程停止未确认，保留远端任务目录等待重连清理。")
        else:
            _say("正在清理远端工作目录…")
            try:
                if not self.workspace_manager.cleanup_remote(workspace, cancel_event=None):
                    cleanup_errors.append("远端工作目录：设备拒绝删除或命令执行失败")
            except Exception as exc:
                cleanup_errors.append(f"远端工作目录：{exc}")
        from modules.capture_services.local_cache_cleanup import acknowledge_session
        safe_local_cleanup = not cleanup_errors and not self.process_manager.handles()
        try:
            acknowledge_session(workspace, safe_local_cleanup)
        except (OSError, ValueError) as exc:
            cleanup_errors.append(f"本地安全凭据：{exc}")
        if primary_error is None and not cleanup_errors and safe_local_cleanup:
            # 成功时清理本机会话临时目录；失败时保留现场，位置已附在错误信息中。
            # 最终产物此前已搬运到输出根目录，不受影响。
            from modules.platform_services.owned_cache import inventory, delete_inventory
            if workspace.local_dir.exists():
                try:
                    delete_inventory(workspace.local_dir.parent,
                                     inventory(workspace.local_dir.parent, workspace.local_dir))
                except (OSError, ValueError) as exc:
                    cleanup_errors.append(f"本地工作目录：{exc}")
        if cleanup_errors or self.process_manager.handles():
            self.reconnect_cleanup.remember(workspace, takeover_record)
        self._running = False
        if not cleanup_errors:
            journal.record("cleanup", operation="finish_capture", status="completed",
                primary_failed=primary_error is not None, wireless_cleanup_confirmed=True,
                connected_after=bool(self.ssh_client.connected))
            return
        journal.record("cleanup", operation="finish_capture", status="failed",
            primary_failed=primary_error is not None,
            wireless_cleanup_confirmed=wireless_cleanup_confirmed,
            monitor_active=bool(self.radio_session.active),
            connected_after=bool(self.ssh_client.connected),
            pending_processes=len(self.process_manager.handles()), reason="reconnect_recovery_pending")
        cleanup_detail = "\uff1b".join(cleanup_errors)
        if primary_error is not None:
            raise CaptureCleanupError(
                f"{primary_error}；清理临时资源时又发生错误：{cleanup_detail}。"
                "建议保持设备在线并重新连接，让 \u8def\u5c0f\u7ba1 重试恢复。",
                tuple(getattr(primary_error, "cleanup_errors", ())) + tuple(cleanup_errors),
            ) from primary_error
        raise CaptureCleanupError(
            f"任务主体已结束，但清理临时资源失败：{cleanup_detail}。"
            "建议保持设备在线并重新连接，让 \u8def\u5c0f\u7ba1 重试恢复。", cleanup_errors,
        )

    @record_scan_selection
    def _find_scan_radio(self, band, cancel_event, target_channel=0, idle_radio_only=False, message_callback=None):
        """选择用于抓包的 phy：优先空闲，其次定向目标同信道，最后按频段回退。

        返回值：单个 phy（如 phy0）。空闲 radio（无任何无线接口）可自由切换抓包信道而不
        影响路由器自身业务；只有全部 radio 都被占用时才回退到按目标频段的业务信道选择，
        此时长时守信道可能与业务冲突，由 radio_session 在实际切信道时给出诊断。`iw dev`
        读取自带 sysfs 完整性校验，缺 phy 会自动重读。
        """

        try:
            phy_interfaces, phy_channels, diag = read_iwdev_complete(
                self.command_runner, "find_scan_radio", cancel_event
            )
        except Exception as exc:
            if not idle_radio_only or (cancel_event is not None and cancel_event.is_set()):
                raise
            raise IdleRadioUnavailable(
                "仅使用空闲网卡已开启，但无法读取无线硬件/接口清单，已拒绝使用。"
                "请检查设备连接并刷新无线信息后再试。"
            ) from exc

        return self._select_scan_radio(
            phy_interfaces, phy_channels, diag, band, cancel_event,
            idle_radio_only, message_callback, target_channel,
        )

    def _select_scan_radio(self, phy_interfaces, phy_channels, diag, band, cancel_event,
                           idle_radio_only=False, message_callback=None, target_channel=0):
        """在已读取的无线快照上执行选卡决策，与执行期同口径。

        参数与 _find_scan_radio 一致，只是 `iw dev` 快照由调用方一次读好（只读
        普查多目标共用），本方法不再发起新的 dump 读取，频段能力等只读核验除外。
        返回值：单个 phy。严格空闲无可用、无承载 radio 时抛异常，调用方按失败处理。
        """

        def chosen(phy, reason):
            if message_callback is not None:
                message_callback(f"选用无线网卡 {phy}（{band}）：{reason}。", "info")
            return phy

        channel_checks = {}

        def supports_target(phy):
            # 当前决策内缓存，预检和执行都使用同一能力门禁，不跨设备保存。
            if not target_channel:
                return True
            if phy not in channel_checks:
                channel_checks[phy] = self._phy_supports_channel(phy, target_channel, cancel_event)
            return channel_checks[phy]

        if idle_radio_only:
            notes = []
            for phy in sorted(set(phy_interfaces) | set(diag["expected"])):
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("选择空闲无线网卡已取消。")
                if phy_interfaces.get(phy):
                    notes.append(f"{phy} 存在无线接口（含未连接 STA，均算占用）")
                    continue
                try:
                    require_idle_phy(self.command_runner, phy, cancel_event)
                except IdleRadioUnavailable as exc:
                    notes.append(str(exc))
                    continue
                if self._phy_supports_band(phy, band, cancel_event) and supports_target(phy):
                    return chosen(phy, "已核实整块射频空闲，不复用业务接口、不接管；创建前再次核实")
                notes.append(f"{phy} 不支持 {band}")
            detail = "；".join(notes) or "没有可核实的无线硬件信息"
            raise IdleRadioUnavailable(
                f"仅使用空闲网卡已开启，{band} 没有可用的空闲射频：{detail}。"
                "本次不接管、不停用原业务；请更换有空闲射频的频段/设备。"
            )
        if not phy_interfaces and not diag["expected"]:
            raise RuntimeError(
                "无法识别 OpenWrt 无线接口结构，不能安全创建 monitor 接口。"
                f"本次 `iw dev` 输出摘要：{diag['excerpt'] or '<空>'}。"
            )
        seen_parts = []
        for phy, ifaces in sorted(phy_interfaces.items()):
            if not ifaces:
                seen_parts.append(f"{phy}=空闲")
            else:
                seen_parts.append(
                    f"{phy}={','.join(ifaces)}(信道{phy_channels.get(phy, '未知')})"
                )
        for phy in sorted(set(diag["expected"]) - set(phy_interfaces)):
            seen_parts.append(f"{phy}=空闲(无网卡实例)")
        seen = "; ".join(seen_parts)
        # 硬件在但 dump 缺失的 phy 同样视为空闲（两种独立读取都无网卡实例）；
        # 连硬件未知的不在此列，保持保守。
        free_phys = sorted(
            {phy for phy, ifaces in phy_interfaces.items() if not ifaces}
            | {phy for phy in diag["expected"] if phy not in phy_interfaces}
        )
        if free_phys:
            # 优先选择经能力核实支持目标频段和信道的空闲 radio，不按编号猜测。
            band_free = [
                phy for phy in free_phys if self._phy_supports_band(phy, band, cancel_event) and supports_target(phy)
            ]
            if band_free:
                return chosen(band_free[0], "无无线接口，优先使用空闲射频，无需接管")
        # 没有支持该频段的空闲 radio：回退到承载该频段业务的 radio。若它正被业务占用，
        # 长时守信道可能与业务冲突，由 radio_session 在实际切信道时给出诊断。
        candidates = sorted(
            phy
            for phy, ifaces in phy_interfaces.items()
            if ifaces and _channel_matches_band(phy_channels.get(phy), band) and supports_target(phy)
        )
        # 同信道只作为选卡优先级；业务接口仍必须经过授权接管并核实停用。
        if target_channel:
            same_channel = [phy for phy in candidates if phy_channels.get(phy) == target_channel]
            if same_channel:
                return chosen(same_channel[0], f"存在业务接口，与目标同信道 {target_channel}，仍需授权临时接管")
        if candidates:
            return chosen(candidates[0], "存在业务接口，按业务频段回退；保留原任务的复用/接管授权规则")
        # `iw dev` 在无线重载或 STA 未关联时可能只缺信道，接口和 phy 本身仍完整。
        # 此时不能把未知误判为“不承载目标频段”：以 `iw phy info` 实测能力补一次，
        # 后续接管仍会快照、关闭、恢复该 radio，不会猜 UCI 映射或改错频段。
        unknown_channel_candidates = sorted(
            phy
            for phy, ifaces in phy_interfaces.items()
            if ifaces
            and not _channel_matches_band(phy_channels.get(phy), "2.4G")
            and not _channel_matches_band(phy_channels.get(phy), "5G")
            and self._phy_supports_band(phy, band, cancel_event) and supports_target(phy)
        )
        if unknown_channel_candidates:
            return chosen(unknown_channel_candidates[0], "存在业务接口且信道未知，按频段能力回退；不视为空闲")
        # 最后兜底：phy 上如果全是未连接的 STA（如用户已停用本频段业务，仅剩断开的
        # 上行 STA），同样没有实际业务可扰；频段能力用 iw phy 实测确认，不猜。
        idle_sta = self._find_idle_sta_phy(band, phy_interfaces, cancel_event)
        if idle_sta is not None and supports_target(idle_sta):
            return chosen(idle_sta, "仅检测到未连接 STA，按原手动策略回退；不视为严格空闲射频")
        rejected = [phy for phy in phy_interfaces
                    if _channel_matches_band(phy_channels.get(phy), band)
                    and channel_checks.get(phy) is False]
        if rejected:
            raise RuntimeError(
                f"已找到 {band} 无线网卡 {','.join(sorted(rejected))}，"
                f"但目标信道 {target_channel} 的能力核验未通过。"
                "本次检查的详细原因已自动记录，请导出日志反馈，无需额外执行检测命令。"
            )
        name = "2.4G" if band == "2.4G" else "5G"
        if free_phys:
            raise RuntimeError(
                f"{name} 频段没有可用的空闲无线网卡，且承载 {name} 业务的网卡已全部被占用。"
                f"当前无线状态：{seen}。{diag['note']}"
                "请更换抓包频段，或先停用路由器上对应的无线业务后再试。"
            )
        raise RuntimeError(
            f"未找到承载 {name} 业务的无线接口，且没有空闲无线网卡可接管。"
            f"当前无线状态：{seen}。{diag['note']}"
                "若该频段没有空闲 radio，请更换抓包频段，或先停用路由器上对应的无线业务后再试。"
            )

    @record_takeover_probe
    def probe_handshake_takeover(self, targets, settings, cancel_event=None):
        """只读普查多目标的射频占用，供主线程按新鲜结果确认接管。

        单次 `iw dev` 读取由全部目标共用；逐个目标复用与执行期完全一致的选卡
        决策（含严格空闲与各类回退），选中 phy 仍有接口即需接管授权（与接管
        执行入口的空闲判定同口径）。不创建接口、不修改配置、不弹窗、不写快照。
        返回与 targets 一一对应的 TakeoverProbeEntry 元组；选卡失败记入
        entry.error，调用方展示后中止启动。取消直接抛异常，由任务槽按取消处理。
        """

        options = settings or {}
        idle_radio_only = bool(options.get("idle_radio_only", False))
        try:
            phy_interfaces, phy_channels, diag = read_iwdev_complete(
                self.command_runner, "probe_takeover_needs", cancel_event
            )
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            return tuple(
                TakeoverProbeEntry(
                    target.bssid, target.ssid, target.band,
                    int(target.channel or 0), error=message,
                )
                for target in targets or ()
            )
        expected = set(diag.get("expected") or ())
        entries = []
        for target in targets or ():
            channel = int(target.channel or 0)
            try:
                phy = self._select_scan_radio(
                    phy_interfaces, phy_channels, diag, target.band,
                    cancel_event, idle_radio_only, None, channel,
                )
            except Exception as exc:
                entries.append(TakeoverProbeEntry(
                    target.bssid, target.ssid, target.band, channel,
                    error=str(exc).strip() or type(exc).__name__,
                ))
                continue
            # 与接管执行入口同口径：已知且为空的 phy 才算空闲，其余一律需授权。
            known_empty = (not phy_interfaces.get(phy)) and (phy in phy_interfaces or phy in expected)
            entries.append(TakeoverProbeEntry(
                target.bssid, target.ssid, target.band, channel,
                phy=phy, phy_channel=int(phy_channels.get(phy) or 0),
                busy=not known_empty, need_takeover=not known_empty and not idle_radio_only,
            ))
        return tuple(entries)

    def _phy_supports_channel(self, phy, channel, cancel_event):
        """复用公共只读信道核验，拒绝双 5G 射频不支持的目标信道。"""
        return phy_supports_channel(self.command_runner, phy, channel, cancel_event)

    def _phy_supports_band(self, phy, band, cancel_event):
        """只读探测单个 phy 的频段能力，能力读取失败时不猜测支持。"""

        result = self.command_runner.run(
            "probe_phy_band",
            f"iw phy {quote_argument(phy)} info",
            timeout=10,
            cancel_event=cancel_event,
        )
        if result.cancelled or (cancel_event is not None and cancel_event.is_set()):
            raise RuntimeError("无线频段能力检查已取消。")
        if result.timed_out:
            raise RuntimeError("无线频段能力检查超时，请刷新无线信息后再试。")
        if result.exit_code != 0:
            return False
        from modules.capture_services.openwrt.device_probe import _parse_supported_modes
        modes = _parse_supported_modes(result.stdout)
        if modes and "monitor" not in modes:
            return False
        low = (2400, 2500) if band == "2.4G" else (5000, 6000)
        for line in result.stdout.splitlines():
            # freqs 行形如 "* 2412 MHz [1] (30.0 dBm)"，取第一个 MHz 频率判断频段。
            freq = _parse_phy_frequency(line)
            if freq is None:
                continue
            if low[0] <= freq <= low[1] and "disabled" not in line.lower():
                return True
        return False

    def _find_idle_sta_phy(self, band, phy_interfaces, cancel_event):
        """找全是未连接 STA 的 phy，返回首个频段能力匹配的，否则返回 None。

        参数 band 是目标频段。逐个 phy 检查：接口必须全部是 managed(STA) 类型且
        `iw dev <iface> link` 明确未连接，有一个已连接或类型不明就跳过；频段用
        iw phy 实测，不猜。类型读不到时整条兜底放弃，保守报 402。
        """

        try:
            iface_types = read_iface_types(self.command_runner, cancel_event)
        except Exception:
            return None
        if not iface_types:
            return None
        for phy in sorted(phy_interfaces):
            ifaces = phy_interfaces.get(phy) or ()
            if not ifaces:
                continue
            if any(iface_types.get(iface, "") != "managed" for iface in ifaces):
                continue
            try:
                idle = all(
                    sta_link_down(self.command_runner, iface, cancel_event)
                    for iface in ifaces
                )
            except Exception:
                continue
            if not idle:
                continue
            try:
                if self._phy_supports_band(phy, band, cancel_event):
                    return phy
            except Exception:
                continue
        return None

    def is_running(self):
        """返回是否存在本后端启动的远程抓包任务。"""

        return self._running

    def stop(self):
        """幂等停止入口；后续实现必须按本次记录的远程 PID 清理。"""

        self._running = False
        self.ssh_client.close()
