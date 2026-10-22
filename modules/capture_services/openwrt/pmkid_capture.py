"""OpenWrt 全局和过滤 PMKID 任务接口。"""

import time
import sys
from threading import Event
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from modules.capture_services.openwrt.command_builder import normalize_bssid, quote_argument
from modules.capture_services.openwrt.device_probe import (
    monitor_lost_note,
    read_monitor_status,
)
from modules.capture_services.openwrt.result_converter import analyze_handshake_cap, convertible_pairs
from modules.capture_services.openwrt.models import CaptureCleanupError
from modules.capture_services.openwrt.process_lease import RENEW_SECONDS
from modules.capture_services.openwrt.hcx_capabilities import (
    HcxCompatibilityError, hcx_capture_command, read_hcx_capabilities,
)
from modules.capture_services.openwrt.hcx_filter import capture_filter, filter_text, normalize_targets
from modules.capture_services.openwrt.wireless_diagnostics import (
    collect_tool_failure_diagnostics,
)


@dataclass(frozen=True)
class PmkidCaptureResult:
    """PMKID 任务保留的原始 PCAPNG 产物；不在此阶段声明已获得有效 PMKID。"""

    pcapng_path: str
    filtered_bssids: tuple = ()
    verdict: str = "unknown"
    verdict_detail: str = ""
    convertible_count: int = 0
    pmkid_count: int = 0
    hc22000_files: tuple = ()
    hc22000_note: str = ""
    finished_early: bool = False
    early_reason: str = ""
    completed_bssids: tuple = ()
    cancelled: bool = False


@dataclass(frozen=True)
class PmkidCaptureUpdate:
    """PMKID worker 发送给主线程的有限时长进度。"""

    elapsed: int
    total: int
    note: str = ""
    captured_bytes: int | None = None


class _WextInitializationError(RuntimeError):
    """工具旧式 WEXT 初始化失败，禁止按退出后的接口状态重试。"""


@dataclass(frozen=True)
class PmkidPlan:
    """单次探测计划，绑定执行器、连接实例、会话和完整用户选择。"""

    capabilities: object
    runner: object
    connection: object
    session_id: str
    band: str
    active_beacon: bool
    targets: tuple
    bpf_text: str
    executable: str = "hcxdumptool"

    def validate(self, runner, session_id, band, active_beacon, targets):
        """在写文件或启动前拒绝跨任务、跨连接及选择变化，不执行网络操作。"""

        connection = getattr(getattr(runner, "ssh_client", None), "_client", None)
        if (runner is not self.runner or connection is not self.connection
                or (session_id, band, active_beacon, targets)
                != (self.session_id, self.band, self.active_beacon, self.targets)):
            raise HcxCompatibilityError("PMKID 能力计划与本次会话或目标不一致，请重新启动任务。")


class OpenWrtPmkidCapture:
    """执行版本准入后的采集；主动请求仅用于当次确认的勾选目标。"""

    def __init__(self, command_runner, process_manager):
        self.command_runner = command_runner
        self.process_manager = process_manager
        self.tool_selection = None

    def prepare(self, band, active_beacon, selected_bssids, cancel_event=None, *, session_id=""):
        """每个任务开始前读取一次实际能力，返回仅供本任务自检/采集共用的参数。"""

        targets = normalize_targets(selected_bssids)
        if type(active_beacon) is not bool:
            raise ValueError("PMKID 主动开关必须是布尔值，请重新保存设置。")
        connection = getattr(getattr(self.command_runner, "ssh_client", None), "_client", None)
        executable = self.tool_selection[1] if self.tool_selection and self.tool_selection[0] is connection else "hcxdumptool"
        capabilities = read_hcx_capabilities(self.command_runner, cancel_event) if executable == "hcxdumptool" else read_hcx_capabilities(self.command_runner, cancel_event, executable=executable)
        capabilities.capture_options(band, active_beacon, bool(targets), "capture.bpf")
        bpf_text = filter_text(capture_filter(
            targets, legacy_624=capabilities.version == "6.2.4", active_request=active_beacon,
        ), capabilities.version)
        plan = PmkidPlan(capabilities, self.command_runner, connection, session_id,
                         band, active_beacon, targets, bpf_text, executable)
        plan.validate(self.command_runner, session_id, band, active_beacon, targets)
        return plan

    def _plan_options(self, plan, workspace, workspace_manager, cancel_event, *, file_name="capture.bpf"):
        """把纯计划的 BPF 写到当前任务目录；上传失败/取消不能启动工具。"""

        if file_name not in {"capture.bpf", "startup.bpf"}:
            raise ValueError("Invalid internal filter filename")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("PMKID 抓包已取消。")
        remote_filter = None
        if plan.bpf_text:
            local_filter = workspace.local_dir / file_name
            remote_filter = PurePosixPath(workspace.remote_dir) / file_name
            local_filter.write_text(plan.bpf_text, encoding="ascii")
            workspace_manager.upload_atomic(local_filter, remote_filter, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("PMKID 抓包已取消。")
        plan.validate(self.command_runner, plan.session_id, plan.band, plan.active_beacon, plan.targets)
        return plan.capabilities.capture_options(plan.band, plan.active_beacon, bool(plan.targets), remote_filter)

    def capture(
        self,
        workspace,
        monitor_interface,
        session_id,
        seconds,
        selected_bssids,
        workspace_manager,
        cancel_event=None,
        progress_callback=None,
        active_beacon=False,
        idle_timeout=180,
        recover_monitor=None,
        *,
        band="2.4G",
        command_options=None,
        finalization_cancel_event=None,
    ):
        """采集限定时间的原始 PCAPNG，并按需要上传 BSSID 过滤列表。

        active_beacon 沿用旧接口名，现表示 6.3.4 目标主动请求，不再发送 beacon。
        先核对工具 API 与用户选择，不支持的组合明确拒绝，不回退。
        command_options 仅由同任务 prepare 生成，不从配置或历史快照读取。
        过滤模式只接收用户已在共享表中选择的 BSSID。recover_monitor 透传给启动
        自检（M 接口丢失或被关闭时重建一次并重跑自检）。返回原始包路径，转换为
        22000 留给独立转换器处理，转换失败也绝不删除该原始包。
        """

        duration = 0 if int(seconds) == 0 else max(5, min(int(seconds), 3600))
        filtered_bssids = normalize_targets(selected_bssids)
        if command_options is None:
            command_options = self.prepare(band, active_beacon, filtered_bssids, cancel_event, session_id=session_id)
        if not isinstance(command_options, PmkidPlan):
            raise HcxCompatibilityError("PMKID 必须使用本次能力计划，不能传入任意命令参数。")
        command_options.validate(self.command_runner, session_id, band, active_beacon, filtered_bssids)
        plan = command_options
        executable = command_options.executable
        command_options = self._plan_options(command_options, workspace, workspace_manager, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("PMKID 抓包已取消。")
        probe_options = command_options
        if active_beacon:
            passive_plan = replace(plan, active_beacon=False,
                bpf_text=filter_text(capture_filter(filtered_bssids), plan.capabilities.version))
            probe_options = self._plan_options(passive_plan, workspace, workspace_manager,
                                               cancel_event, file_name="startup.bpf")
        self._verify_monitor(monitor_interface, cancel_event)
        # 启动自检：空跑约 6 秒，hcxdumptool 初始化失败会立刻退出，
        # 把远端日志原文带回来再定是驱动、接口还是参数问题。
        self._probe_startup(
            monitor_interface, session_id, workspace, workspace_manager, cancel_event, False,
            recover_monitor, command_options=probe_options, executable=executable, verify_tool_mode=True,
        )
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("PMKID 抓包已取消。")
        plan.validate(self.command_runner, session_id, band, active_beacon, filtered_bssids)
        remote_pcapng = PurePosixPath(workspace.remote_dir) / "pmkid.pcapng"
        command = hcx_capture_command(monitor_interface, remote_pcapng, command_options, executable)
        handle = self.process_manager.start(
            "hcxdumptool",
            command,
            session_id,
            str(workspace.remote_dir),
            cancel_event,
            # 看门狗：SSH 中死没人来停时，超时后进程自杀，不无限写盘撑爆 /tmp。
            max_runtime=max(600, duration + 300) if duration else 0,
            **({"renewable_output":str(remote_pcapng)} if not duration else {}),

        )
        # 无新数据超时只说明本任务文件未增长，不能推断空域或内容已完成；
        # 0 表示关闭该功能。计时从首次见到非零文件开始，从未有数据则跑满全程。
        idle_limit = max(0, min(int(idle_timeout or 0), 3600))
        finished_early = False
        cancelled = False
        early_reason = ""
        content_check_at = None
        try:
            started = time.monotonic()
            deadline = started + duration if duration else float("inf")
            renewal_at = started + RENEW_SECONDS
            last_second = -1
            last_size = -1
            observed_size = None
            idle_since = None
            size_check_at = started + 15
            if filtered_bssids:
                content_check_at = started + 60
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    finished_early = True
                    early_reason = "已手动停止采集。"
                    break
                now = time.monotonic()
                if not duration and now >= renewal_at:
                    self.process_manager.renew_watchdog(handle,cancel_event)
                    renewal_at = now + RENEW_SECONDS
                if not self.process_manager.is_running(handle, cancel_event):
                    detail = self.process_manager.early_exit_detail(handle, cancel_event)
                    diag_name = collect_tool_failure_diagnostics(
                        self.command_runner, workspace, workspace_manager, "hcxdumptool", cancel_event,
                    )
                    if "WINBOX_WATCHDOG_SIZE_LIMIT" in detail:
                        raise RuntimeError("临时采集数据达到安全上限，已停止以避免占满路由器空间。请减少目标后重试。")
                    if "WINBOX_WATCHDOG_LEASE_EXPIRED" in detail:
                        raise RuntimeError("连接未及时确认采集仍受控，远端任务已自动停止，请检查连接后重试。")
                    raise RuntimeError(
                        "hcxdumptool 在抓包完成前意外退出，请检查路由器无线驱动和工具版本。"
                        + (f"远端日志末尾：{detail}" if detail else "")
                        + monitor_lost_note(self.command_runner, monitor_interface, cancel_event)
                        + ("；无线诊断已一并保留。" if diag_name else "")
                    )
                if now >= size_check_at:
                    size = self._remote_file_size(remote_pcapng, cancel_event)
                    observed_size = size
                    if not duration and size is not None:
                        from .process_lease import MAX_CAPTURE_BYTES
                        if size > MAX_CAPTURE_BYTES:
                            raise RuntimeError("临时采集数据达到安全上限，已停止以避免占满路由器空间。请减少目标后重试。")
                    if idle_limit > 0 and size is not None and size > 0:
                        if size != last_size:
                            last_size = size
                            idle_since = now
                        elif idle_since is not None and now - idle_since >= idle_limit:
                            finished_early = True
                            early_reason = "无进展：本任务包文件达到无新数据超时，提前结束；是否有效以内容分析为准。"
                            break
                    else:
                        last_size, idle_since = -1, None
                    size_check_at = now + 15
                if content_check_at is not None and now >= content_check_at:
                    if self._all_filtered_done(
                        workspace, workspace_manager, remote_pcapng,
                        filtered_bssids, cancel_event,
                    ):
                        finished_early = True
                        early_reason = "选中目标已全部出可跑包结果，提前结束。"
                        break
                    content_check_at = now + 60
                elapsed = int(time.monotonic() - started)
                if duration:
                    elapsed = min(duration,elapsed)
                if progress_callback is not None and elapsed != last_second:
                    progress_callback(PmkidCaptureUpdate(elapsed, duration, captured_bytes=observed_size))
                    last_second = elapsed
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        except (InterruptedError,RuntimeError) as exc:
            if (cancel_event is not None and cancel_event.is_set()
                    and (isinstance(exc,InterruptedError) or str(exc)=="PMKID 抓包已取消。")):
                cancelled = True
                finished_early = True
                early_reason = "已手动停止采集。"
            else:
                raise
        finally:
            primary_error = sys.exc_info()[1]
            try:
                self.process_manager.stop(handle)
            except Exception as exc:
                note = f"停止本任务抓包进程失败：{exc}"
                raise CaptureCleanupError(
                    f"{primary_error}；{note}" if primary_error else note, (note,),
                    primary_error=primary_error,
                ) from exc
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            finished_early = True
            early_reason = "已手动停止采集。"
        final_event = (finalization_cancel_event if finalization_cancel_event is not None else Event()) if cancelled else cancel_event
        if progress_callback is not None and cancelled:
            progress_callback(PmkidCaptureUpdate(int(time.monotonic()-started),duration,
                "正在检查已采集内容并完成收尾。"))
        if final_event is not None and final_event.is_set():
            raise RuntimeError("PMKID 抓包已取消。")
        exists = self.command_runner.run(
            "verify_pmkid_pcapng",
            f"test -s {quote_argument(str(remote_pcapng))}",
            timeout=8,
            cancel_event=final_event,
        )
        if exists.cancelled or (final_event is not None and final_event.is_set()):
            raise RuntimeError("PMKID 抓包已取消。")
        if exists.timed_out:
            raise RuntimeError("检查 PMKID 原始文件超时，请检查设备连接。")
        if exists.exit_code != 0:
            raise RuntimeError("PMKID 抓包没有生成原始 PCAPNG，请确认目标频段和无线驱动支持。")
        local_pcapng = workspace.local_dir / "pmkid.pcapng"
        workspace_manager.download_atomic(remote_pcapng, local_pcapng, final_event)
        if progress_callback is not None:
            progress_callback(PmkidCaptureUpdate(int(time.monotonic()-started) if not duration else duration, duration))
        if not finished_early:
            early_reason = f"已到设置的 {duration} 秒采集时长，本轮结束。"
        return PmkidCaptureResult(
            str(local_pcapng), filtered_bssids,
            finished_early=finished_early, early_reason=early_reason, cancelled=cancelled,
        )

    def _all_filtered_done(
        self, workspace, workspace_manager, remote_pcapng, filtered_bssids, cancel_event
    ):
        """检查选中目标是否全部已有可跑包组合；传输/执行错误直接交给任务处理。

        下载当前快照到本地临时文件再分析；远端增长中文件读到的是某一时刻快照，
        解析器可处理截断，失败不中断任务。
        """

        # 文件尚未产生不算传输失败，继续原有等待；执行/连接异常则上报。
        if not self._remote_file_size(remote_pcapng, cancel_event):
            return False
        live = workspace.local_dir / "pmkid-live.pcapng"
        workspace_manager.download_atomic(remote_pcapng, live, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("PMKID 抓包已取消。")
        _analysis, pairs = analyze_handshake_cap(str(live))
        have = {ap.upper() for (ap, _sta) in convertible_pairs(pairs)}
        return all(str(bssid).upper() in have for bssid in filtered_bssids)

    def _remote_file_size(self, remote_path, cancel_event):
        """读远端文件字节数；缺失/不可解析返回 None，执行错误和取消必须上报。"""

        result = self.command_runner.run(
            "pmkid_file_size",
            f"stat -c %s {quote_argument(str(remote_path))} 2>/dev/null",
            timeout=8,
            cancel_event=cancel_event,
        )
        if result.cancelled or (cancel_event is not None and cancel_event.is_set()):
            raise RuntimeError("PMKID 抓包已取消。")
        if result.timed_out:
            raise RuntimeError("读取 PMKID 包文件大小超时，请检查连接和设备状态。")
        if result.exit_code != 0:
            return None
        try:
            return max(0, int((result.stdout or "").strip().split()[0]))
        except (TypeError, ValueError, IndexError):
            return None

    def _probe_startup(
        self, monitor_interface, session_id, workspace, workspace_manager, cancel_event,
        active_beacon=False, recover_monitor=None, *, command_options=None, executable="hcxdumptool", verify_tool_mode=False,
    ):
        """空跑 hcxdumptool 约 6 秒做启动自检，抓出真实初始化报错。

        自检使用已核实的被动参数；主动任务传入独立的被动过滤文件，
        只是输出写 /dev/null。6 秒还活着说明初始化通过，停掉探针继续正式抓包；
        提前退出则把远端日志原文抛出来，不再用笼统结论掩盖。探针用独立会话键
        登记，不污染正式任务句柄。recover_monitor 是可选的无参数可调用对象，
        返回 (是否恢复, 说明)：仅当复核确认 M 接口丢失或被关闭时才调用一次并
        重跑自检，工具自身初始化失败绝不重试，避免掩盖驱动报错。
        """

        if command_options is None:
            plan = self.prepare("2.4G", active_beacon, (), cancel_event, session_id=session_id)
            executable = plan.executable
            command_options = self._plan_options(plan, workspace, workspace_manager, cancel_event)
        recovered = False
        while True:
            try:
                self._probe_once(
                    monitor_interface, session_id, workspace, workspace_manager, cancel_event,
                    command_options,
                    **({"executable": executable} if executable != "hcxdumptool" else {}),
                    **({"verify_tool_mode": True} if verify_tool_mode else {}),
                )
                return
            except (_WextInitializationError, CaptureCleanupError):
                raise
            except RuntimeError as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise
                if any(marker in str(getattr(exc, "diagnostic_detail", str(exc))).casefold() for marker in (
                    "unrecognized option", "unknown option", "invalid option",
                    "invalid argument", "bpf", "filter", "must be", "only allowed",
                )):
                    raise
                if recovered or recover_monitor is None:
                    raise
                if not self._monitor_recoverable(monitor_interface, cancel_event):
                    raise
                try:
                    ok, note = recover_monitor()
                except Exception:
                    raise
                if not ok:
                    raise RuntimeError(f"{exc}；M 接口自动恢复失败：{note}") from exc
                recovered = True

    def _monitor_recoverable(self, monitor_interface, cancel_event):
        """自检失败时复核 M 接口是否丢失或被关闭，是才值得恢复重试。

        复核本身失败返回 False，原错误优先上报，不污染诊断。
        """

        try:
            status = read_monitor_status(
                self.command_runner, monitor_interface, cancel_event
            )
        except Exception:
            return False
        return status in ("down", "missing")

    def _probe_once(
        self, monitor_interface, session_id, workspace, workspace_manager, cancel_event,
        command_options, executable="hcxdumptool", verify_tool_mode=False,
    ):
        """单次启动自检，提前退出直接抛错，由 _probe_startup 决定是否恢复重试。"""

        command = hcx_capture_command(monitor_interface, "/dev/null", command_options, executable)
        handle = self.process_manager.start(
            "hcxdumptool",
            command,
            f"{session_id}-probe",
            str(workspace.remote_dir),
            cancel_event,
            # 探针窗口仅 6 秒，看门狗纯防 SSH 中死后残留。
            max_runtime=120,
        )
        alive = False
        try:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("PMKID 抓包已取消。")
                if not self.process_manager.is_running(handle, cancel_event):
                    detail = self.process_manager.early_exit_detail(handle, cancel_event)
                    diag_name = ""
                    if cancel_event is None or not cancel_event.is_set():
                        # 必须在停止探针和恢复接口前留存现场；诊断失败不能覆盖启动原错。
                        try:
                            diag_name = collect_tool_failure_diagnostics(
                                self.command_runner, workspace, workspace_manager,
                                "hcxdumptool", cancel_event,
                                tool_log=detail,
                            )
                        except Exception:
                            diag_name = ""
                    error_type = RuntimeError
                    message = "路由器上的 PMKID 采集工具启动失败，本次尚未开始采集。请暂用常规握手抓包，详细原因可导出日志查看。"
                    if "failed to detect wlan interface" in (detail or "").casefold():
                        error_type = _WextInitializationError
                        message = (
                            "路由器上的 PMKID 采集工具启动失败，当前工具与无线接口不兼容。"
                            "请暂用常规握手抓包；PMKID 需要匹配当前固件和驱动的工具包。"
                        )
                    if error_type is _WextInitializationError and "hcxdumptool 6.2.4" in (detail or ""):
                        message = ("旧版 PMKID 工具无法识别无线网卡，本次尚未开始采集。"
                            "请到“路由器登录 → PMKID 工具”检查并安装适配的新版；暂可使用常规握手抓包。")
                    message += ("诊断已保存，可导出日志。" if diag_name
                                else "诊断文件未能保存。")
                    error = error_type(message)
                    error.diagnostic_detail = detail or "No remote tool log"
                    raise error
                time.sleep(0.5)
            if verify_tool_mode and read_monitor_status(self.command_runner, monitor_interface, cancel_event) != "ready":
                raise RuntimeError("\u5de5\u5177\u6ca1\u6709\u5c06\u63a5\u53e3\u521d\u59cb\u5316\u4e3a\u53ef\u7528\u76d1\u542c\u72b6\u6001\uff0c\u672c\u6b21\u672a\u5f00\u59cb\u91c7\u96c6\u3002")
            alive = True
        finally:
            primary_error = sys.exc_info()[1]
            try:
                self.process_manager.stop(handle)
            except Exception as exc:
                note = f"探针进程停止失败：{exc}"
                raise CaptureCleanupError(
                    f"{primary_error}；{note}" if primary_error else note, (note,),
                    primary_error=primary_error,
                ) from exc
        return

    def _verify_monitor(self, monitor_interface, cancel_event):
        """确认 monitor 接口存在且处于 monitor 类型，否则直接抛错。

        hcxdumptool 初始化失败时只报射频错误，不指明接口问题；这里先行排除，
        让用户看到确切原因。副作用：执行只读的 `iw dev info`。
        """

        result = self.command_runner.run(
            "verify_monitor_state",
            f"iw dev {quote_argument(monitor_interface)} info 2>/dev/null",
            timeout=10,
            cancel_event=cancel_event,
        )
        from .command_runner import require_command_completion
        require_command_completion(result)
        if result.exit_code != 0 or not any(kind in (result.stdout or "") for kind in ("type managed", "type monitor")):
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                f"monitor 接口状态异常，无法启动 PMKID 抓包：{detail or result.exit_code}。"
            )
