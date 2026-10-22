"""自动握手的授权范围、执行参数与扫描计划，由常驻协调器复用。"""

from dataclasses import dataclass, replace
from uuid import uuid4

from modules.capture_services.openwrt.command_builder import normalize_bssid
from modules.capture_services.openwrt.models import AccessPointCaptureState, AccessPointInfo


@dataclass(frozen=True)
class AutoHandshakeRequest:
    """主线程确认前冻结的授权范围与参数；不保存密码，不创建远端资源。

    必须通过 from_selection 从当前 AP 快照建立，并在确认后原样交给后台。
    confirmed 不使用可持久化布尔值代替用户每次的主线程确认。
    """

    authorized: tuple[AccessPointInfo, ...]
    completed: frozenset[str]
    band: str
    scan_seconds: int
    capture_seconds: int
    refresh_seconds: int
    output_dir: str
    auto_open_dir: bool
    keep_raw_capture: bool
    generate_22000: bool
    mask_ssid: bool
    mask_bssid: bool
    parallel_radios: bool = False

    @classmethod
    def from_selection(cls, access_points, settings, completed_bssids=()):
        """冻结当前勾选 AP 和完成记忆，拒绝空范围/非法身份，不改原配置。

        settings 应来自现有配置标准化/页面收集链；时间再次限制到执行器边界。
        输出目录由服务启动入口解析，此处只保存普通路径字符串，不访问文件系统。
        """

        selected = {}
        completed = {normalize_bssid(value) for value in completed_bssids}
        for point in access_points:
            if not point.selected:
                continue
            try:
                key = normalize_bssid(point.bssid)
            except ValueError as exc:
                raise ValueError("所选目标标识无效，请重新扫描并勾选授权网络。") from exc
            frozen = replace(point, bssid=key, clients=tuple(point.clients))
            if key in selected and selected[key] != frozen:
                raise ValueError("所选目标存在重复且冲突的记录，请重新扫描后确认。")
            selected[key] = frozen
            if point.capture_state == AccessPointCaptureState.CAPTURED:
                completed.add(key)
        if not selected:
            raise ValueError("请先扫描并勾选自有或明确授权的目标，不能启动无范围自动抓包。")
        band = settings.get("band", "2.4G")
        if band not in {"2.4G", "5G"}:
            raise ValueError("自动扫描频段无效，请选择 2.4G 或 5G。")
        try:
            scan = max(5, min(int(settings.get("scan_seconds", 24)), 120))
            capture = max(5, min(int(settings.get("idle_timeout_seconds", 180)) or 180, 3600))
            refresh = max(1, min(int(settings.get("refresh_seconds", 10)), scan))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("自动抓包时间无效，请重新设置扫描时间和单目标超时。") from exc
        return cls(
            tuple(selected.values()), frozenset(completed), band, scan, capture, refresh,
            str(settings.get("resolved_output_dir") or ""),
            bool(settings.get("auto_open_dir", True)),
            bool(settings.get("keep_raw_capture", True)),
            bool(settings.get("generate_22000", True)),
            bool(settings.get("mask_ssid_column", False)),
            bool(settings.get("mask_bssid_column", False)),
            settings.get("auto_parallel_radios") is True,
        )

    def backend_settings(self):
        """返回本次执行副本，强制被动、严格空闲和单轮，不继承其他功能参数。"""

        return {
            "band": self.band,
            "scan_seconds": self.scan_seconds,
            "idle_timeout_seconds": self.capture_seconds,
            "refresh_seconds": self.refresh_seconds,
            "resolved_output_dir": self.output_dir,
            "auto_open_dir": self.auto_open_dir,
            "keep_raw_capture": self.keep_raw_capture,
            "generate_22000": self.generate_22000,
            "mask_ssid_column": self.mask_ssid,
            "mask_bssid_column": self.mask_bssid,
            "idle_radio_only": True,
            "active_deauth": False,
            "allow_radio_takeover": False,
            "handshake_rounds": 1,
            "handshake_rounds_infinite": False,
            "auto_parallel_radios": self.parallel_radios,
        }


@dataclass(frozen=True)
class AutoTargetSkip:
    """一个授权目标未入队的原因；身份供模型关联，显示层需应用隐私掩码。"""

    bssid: str
    reason: str


@dataclass(frozen=True)
class AutoScanProgress:
    """计划阶段进度；stage 为 scanning/filtering，进度不代表目标完成。"""

    stage: str
    elapsed: int = 0
    total: int = 0
    authorized_count: int = 0


@dataclass(frozen=True)
class AutoScanPlan:
    """计划阶段终局，不表示抓包成功。

    status 为 ready/no_targets/cancelled/failed；只有 ready 有候选目标。
    不携带扫描临时 CSV 路径或范围外 AP，不改变服务现有产物。
    """

    status: str
    targets: tuple[AccessPointInfo, ...] = ()
    skipped: tuple[AutoTargetSkip, ...] = ()
    detail: str = ""
    cleanup_errors: tuple = ()
    access_points: tuple[AccessPointInfo, ...] = ()


def prepare_auto_scan(backend, request, cancel_event, progress_callback, message_callback):
    """后台执行一次授权扫描并返回计划；调用方须持有整轮任务槽。

    request 是主线程已确认的冻结对象。复用后端 scanner 及其 finally 清理；本函数
    不 begin/finish、不创建线程、不调用捕获方法；整轮由常驻协调器持有。
    """

    if not request.authorized:
        raise ValueError("自动抓包授权范围不能为空，请先扫描勾选并确认。")
    if cancel_event.is_set():
        return AutoScanPlan("cancelled", detail="自动扫描已取消。")
    count = len(request.authorized)

    def scanned(update):
        if not cancel_event.is_set():
            progress_callback(AutoScanProgress("scanning", update.elapsed, update.total, count))

    progress_callback(AutoScanProgress("scanning", total=request.scan_seconds, authorized_count=count))
    if cancel_event.is_set():
        return AutoScanPlan("cancelled", detail="自动扫描已取消。")
    try:
        # 不复用外层 ID：扫描清理只影响此 UUID 的平级目录。
        result = backend.scan_wifi(
            uuid4().hex, request.backend_settings(), cancel_event, scanned, message_callback,
        )
    except Exception as exc:
        cleanup_errors = tuple(getattr(exc, "cleanup_errors", ()))
        from modules.capture_services.openwrt.radio_policy import IdleRadioUnavailable
        cause, idle_unavailable = exc, False
        for _ in range(12):
            if cause is None:
                break
            if isinstance(cause, IdleRadioUnavailable):
                idle_unavailable = True
                break
            cause = getattr(cause, "primary_error", None) or cause.__cause__
        if idle_unavailable and not cleanup_errors and not cancel_event.is_set():
            return AutoScanPlan("idle_radio_unavailable", detail=(
                f"当前 {request.band} 没有空闲射频，自动抓包尚未开始。"
                "自动抓包只使用空闲射频，不中断路由器现有业务。"
                "请使用“开始常规抓包”，确认临时接管后采集；或使用有空闲射频的设备。"))
        return AutoScanPlan(
            "cancelled" if cancel_event.is_set() else "failed",
            detail=f"自动扫描未完成：{exc}",
            cleanup_errors=cleanup_errors,
        )
    if cancel_event.is_set():
        return AutoScanPlan("cancelled", detail="自动扫描已取消，未进入目标队列。")
    progress_callback(AutoScanProgress("filtering", authorized_count=count))
    authorized = {point.bssid for point in request.authorized}
    found = {}
    conflicts = set()
    for point in result.access_points:
        if cancel_event.is_set():
            return AutoScanPlan("cancelled", detail="自动筛选已取消，未进入目标队列。")
        try:
            key = normalize_bssid(point.bssid)
        except ValueError:
            continue
        if key not in authorized:
            continue
        point = replace(point, bssid=key, clients=tuple(point.clients), selected=True)
        if key in found and found[key] != point:
            conflicts.add(key)
        found[key] = point
    targets = []
    skipped = []
    for original in request.authorized:
        key = original.bssid
        point = found.get(key)
        if key in request.completed or (
            point is not None and point.capture_state == AccessPointCaptureState.CAPTURED
        ):
            reason = "already_completed"
        elif key in conflicts:
            reason = "conflicting_scan_records"
        elif point is None:
            reason = "not_found"
        elif point.band != request.band:
            reason = "different_band"
        elif (not isinstance(point.channel, int) or isinstance(point.channel, bool)
              or not 1 <= point.channel <= 196
              or (point.channel <= 14) != (request.band == "2.4G")):
            reason = "invalid_channel"
        elif not ({"WPA", "WPA2"} & set(point.encryption.upper().split())
                  and "PSK" in point.encryption.upper().split()):
            reason = "unsupported_encryption"
        elif not point.clients:
            reason = "no_clients"
        else:
            targets.append(point)
            continue
        skipped.append(AutoTargetSkip(key, reason))
    if cancel_event.is_set():
        return AutoScanPlan("cancelled", detail="自动筛选已取消，未进入目标队列。")
    return AutoScanPlan(
        "ready" if targets else "no_targets", tuple(targets), tuple(skipped),
        f"授权 {count} 个，符合条件 {len(targets)} 个，跳过 {len(skipped)} 个；尚未执行捕获。",
        access_points=tuple(found.values()),
    )
