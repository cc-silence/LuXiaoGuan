"""OpenWrt Wi-Fi 扫描任务接口。"""

import csv
import io
import re
import sys
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

from modules.capture_services.openwrt.command_builder import quote_argument
from modules.capture_services.openwrt.device_probe import (
    monitor_lost_note,
    monitor_status_text,
    read_monitor_status,
)
from modules.capture_services.openwrt.models import AccessPointInfo, ClientInfo, CaptureCleanupError


@dataclass(frozen=True)
class WifiScanResult:
    """一次扫描的可显示结果和本地 CSV 诊断文件。"""

    access_points: tuple
    csv_path: str


@dataclass(frozen=True)
class WifiScanUpdate:
    """扫描 worker 向主线程发送的倒计时和增量 AP 快照。"""

    elapsed: int
    total: int
    access_points: tuple = ()


class OpenWrtWifiScanner:
    """执行受控 airodump 扫描并解析 CSV，不负责页面控件。

    所有远端对象均来自当前任务的 workspace、PID 和 monitor 会话；本类不修改 UCI，
    不重载无线配置，也不执行主动断连。
    """

    def __init__(self, command_runner, process_manager):
        self.command_runner = command_runner
        self.process_manager = process_manager

    def scan(
        self,
        workspace,
        monitor_interface,
        session_id,
        band,
        seconds,
        refresh_seconds,
        workspace_manager,
        cancel_event=None,
        progress_callback=None,
    ):
        """在指定 monitor 接口扫描并同步 CSV。

        参数：workspace 必须已创建；band 仅接受 2.4G/5G；seconds 是有限扫描时长。
        返回 WifiScanResult。副作用：启动并停止本任务 airodump PID、下载 CSV；取消时
        仍停止 PID，调用方随后负责删除 monitor 和工作目录。
        """

        band_argument = "bg" if band == "2.4G" else "a"
        duration = max(5, min(int(seconds), 120))
        refresh_interval = max(1, min(int(refresh_seconds), duration))
        prefix = PurePosixPath(workspace.remote_dir) / "scan"
        remote_csv = f"{prefix}-01.csv"
        local_csv = workspace.local_dir / "scan.csv"
        command = (
            "airodump-ng --background 1 "
            f"--band {band_argument} --write-interval 1 --output-format csv "
            f"--write {quote_argument(str(prefix))} {quote_argument(monitor_interface)}"
        )
        status = read_monitor_status(
            self.command_runner, monitor_interface, cancel_event
        )
        if status != "ready":
            raise RuntimeError(
                "monitor 接口当前不可用"
                f"（{monitor_status_text(status)}），拒绝开始扫描，请检查无线驱动后重试。"
            )
        handle = self.process_manager.start(
            "airodump-ng",
            command,
            session_id,
            str(workspace.remote_dir),
            cancel_event,
            # 看门狗：SSH 中死没人来停时，超时后进程自杀，不无限写盘撑爆 /tmp。
            max_runtime=max(300, duration + 120),
        )
        try:
            started = time.monotonic()
            deadline = started + duration
            next_refresh = started + min(1, refresh_interval)
            last_reported_second = -1
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("Wi-Fi 扫描已取消。")
                if not self.process_manager.is_running(handle, cancel_event):
                    raise RuntimeError(
                        "airodump-ng 在扫描完成前意外退出，请检查路由器无线驱动和 monitor 支持。"
                        + monitor_lost_note(
                            self.command_runner, monitor_interface, cancel_event
                        )
                    )
                now = time.monotonic()
                elapsed = min(duration, int(now - started))
                points_for_update = ()
                if now >= next_refresh:
                    points_for_update = self._sync_current_results(
                        remote_csv,
                        local_csv,
                        band,
                        workspace_manager,
                        cancel_event,
                    )
                    next_refresh = now + refresh_interval
                if progress_callback is not None and (
                    elapsed != last_reported_second or points_for_update
                ):
                    progress_callback(
                        WifiScanUpdate(elapsed, duration, points_for_update)
                    )
                    last_reported_second = elapsed
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        finally:
            primary_error = sys.exc_info()[1]
            try:
                self.process_manager.stop(handle)
            except Exception as exc:
                note = f"停止本任务扫描进程失败：{exc}"
                raise CaptureCleanupError(
                    f"{primary_error}；{note}" if primary_error else note, (note,),
                ) from exc
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Wi-Fi 扫描已取消。")
        exists = self.command_runner.run(
            "verify_scan_csv",
            f"test -s {quote_argument(remote_csv)}",
            timeout=8,
            cancel_event=cancel_event,
        )
        if exists.exit_code != 0:
            raise RuntimeError("扫描没有生成有效 CSV 结果，请确认无线网卡支持 monitor 模式并重试。")
        workspace_manager.download_atomic(remote_csv, local_csv, cancel_event)
        final_points = tuple(self.parse_csv(local_csv, band))
        if progress_callback is not None:
            progress_callback(WifiScanUpdate(duration, duration, final_points))
        return WifiScanResult(final_points, str(local_csv))

    def _sync_current_results(
        self,
        remote_csv,
        local_csv,
        band,
        workspace_manager,
        cancel_event,
    ):
        """同步当前 CSV 快照；文件尚未生成或正在写入时等待下一轮，不中断扫描。"""

        exists = self.command_runner.run(
            "check_scan_csv_update",
            f"test -s {quote_argument(remote_csv)}",
            timeout=5,
            cancel_event=cancel_event,
        )
        if exists.exit_code != 0:
            return ()
        try:
            workspace_manager.download_atomic(remote_csv, local_csv, cancel_event)
            return tuple(self.parse_csv(local_csv, band))
        except (OSError, EOFError, csv.Error, RuntimeError):
            # airodump 正在改写文件时可能得到短暂的不完整快照，下一刷新周期重试即可；
            # 传输层重试仍失败也等下一轮，不中断扫描。
            return ()

    @staticmethod
    def parse_csv(path, band):
        """解析 airodump-ng CSV 中的 AP 与客户端区域，坏行会被忽略。"""

        text = open(path, "r", encoding="utf-8", errors="replace", newline="").read()
        rows = list(csv.reader(io.StringIO(text)))
        points = {}
        station_rows = False
        for row in rows:
            if not row:
                continue
            first = row[0].strip()
            if first == "Station MAC":
                station_rows = True
                continue
            if first == "BSSID":
                continue
            if not station_rows:
                if len(row) < 14 or not _BSSID.fullmatch(first):
                    continue
                bssid = first.upper()
                channel = _int(row[3])
                power = _int(row[8])
                privacy = row[5].strip()
                cipher = row[6].strip()
                auth = row[7].strip()
                ssid = row[13].strip() or "__hidden__"
                points[bssid] = AccessPointInfo(
                    bssid=bssid,
                    ssid=ssid,
                    power=power,
                    channel=channel or 0,
                    band=band,
                    encryption=" ".join(item for item in (privacy, cipher, auth) if item) or "OPN",
                )
                continue
            if len(row) < 6 or not _BSSID.fullmatch(first):
                continue
            bssid = row[5].strip().upper()
            if bssid not in points:
                continue
            point = points[bssid]
            clients = point.clients + (ClientInfo(first.upper(), _int(row[3])),)
            points[bssid] = AccessPointInfo(
                **{**point.__dict__, "clients": clients}
            )
        return sorted(points.values(), key=lambda point: (point.power is None, -(point.power or -999), point.bssid))


_BSSID = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _int(value):
    """将 CSV 的数值字段转换为整数；空值和异常值用 None 表示。"""

    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
