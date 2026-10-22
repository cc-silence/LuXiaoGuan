"""已验证结果的本地存在性政策；后台只读核验，主线程合并常驻状态。"""

from dataclasses import dataclass, replace
from PySide6.QtCore import QObject, Slot

from modules.capture_services.openwrt.models import AccessPointCaptureState
from modules.config_services.verified_result_paths import (
    PATH_FIELDS, bounded_paths, history_paths, portable_result_path, resolve_result_path,
)
from modules.platform_services.file_presence import FilePresence, check_local_files


@dataclass(frozen=True)
class VerifiedResultCheck:
    """携带输入版本、逐路径结论及目标结论的核验结果；取消时禁止失效合并。"""

    history: dict
    artifacts: tuple
    paths: dict
    targets: dict
    revision: int = 0
    cancelled: bool = False
    session_id: str = ""
    cancel_event: object = None

    @property
    def missing_bssids(self):
        return frozenset(key for key, state in self.targets.items() if state == "missing")


def check_verified_results(history, artifacts, revision, session_id, event):
    """只在现有 worker 调用；任一证据存在即保留，全部确认缺失才允许失效。"""
    references = {key: list(history_paths(entry)) for key, entry in history.items()}
    for artifact in artifacts:
        if artifact.verified:
            references.setdefault(artifact.bssid.upper(), []).append(artifact.path)
    resolved = {ref: resolve_result_path(ref) for refs in references.values() for ref in refs}
    checked = check_local_files(tuple(path for path in resolved.values() if path), event)
    paths = {ref: checked.get(path, FilePresence("unknown", "unresolved_path"))
             for ref, path in resolved.items()}
    targets = {}
    for key, refs in references.items():
        states = {paths[ref].state for ref in refs}
        targets[key] = "present" if "present" in states else "missing" if states == {"missing"} else "unknown"
    return VerifiedResultCheck(history, artifacts, paths, targets, revision, event.is_set(), session_id, event)


class VerifiedResults(QObject):
    """CaptureService 拥有的政策对象，不创建线程或持有页面。"""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.availability = {}
        self.revision = 0
        self.settings_manager = None
        owner.verifiedHistoryChanged.connect(self.persist)

    def configure_store(self, manager):
        """绑定现有配置保存入口，页面释放后仍保存历史；不保存页面或控件引用。"""
        self.settings_manager = manager

    @Slot(object)
    def persist(self, history):
        """只写已抓名单状态文件，不碰设置与界面文件。"""
        if self.settings_manager is None:
            return
        try:
            self.settings_manager.save_openwrt_state(history)
        except OSError as exc:
            self.owner.messageGenerated.emit(
                f"保存完成记录失败（{self.settings_manager.storage_hint()}）：{exc}。"
                "请检查磁盘空间和用户配置目录权限。（软件目录只读不影响保存）", "error",
            )

    def freeze(self):
        """主线程复制当前证据，供后台核验和回调版本比较。"""
        snapshot = self.owner.openwrt_snapshot
        return self.owner.verified_history_snapshot(), snapshot.artifacts, self.revision, snapshot.session_id

    def record_paths(self, previous, paths):
        """新产物保存可靠的便携引用，同目标旧产物仍保留为独立证据。"""
        base = self.owner._openwrt_auto_open[1]
        values = [portable_result_path(path, base=base) if path else "" for path in paths]
        result = dict(zip(PATH_FIELDS, values))
        earlier = history_paths(previous) if previous else ()
        result["artifact_paths"] = bounded_paths((*earlier, *(value for value in values if value)))
        return result

    def decorate(self, point):
        """回放已有状态的可用性说明，不从旧产物恢复用户已清除的完成标记。"""
        key = point.bssid.upper()
        if point.capture_state != AccessPointCaptureState.CAPTURED:
            return replace(point, result_availability="missing") if self.availability.get(key) == "missing" else point
        status = self.availability.get(key, "unknown" if key in self.owner._verified_history else "")
        return replace(point, result_availability=status)

    def apply(self, report):
        """主线程仅合并仍匹配输入版本的结论；不删除文件、不恢复已清除目标。"""
        owner = self.owner
        if (report.cancelled or (report.cancel_event is not None and report.cancel_event.is_set())
                or report.session_id != owner.openwrt_snapshot.session_id
                or report.revision != self.revision or owner._shutting_down
                or owner.openwrt_snapshot.task_state == "stopping"):
            return frozenset()
        snapshot = owner.openwrt_snapshot
        eligible = set()
        for key in report.targets:
            if owner._verified_history.get(key) != report.history.get(key):
                continue
            before = tuple(a for a in report.artifacts if a.bssid.upper() == key)
            current = tuple(a for a in snapshot.artifacts if a.bssid.upper() == key)
            if current == before:
                eligible.add(key)
        missing = report.missing_bssids & eligible
        history_changed = bool(missing & owner._verified_history.keys())
        for key in eligible:
            self.availability[key] = report.targets[key]
        for key in missing:
            owner._verified_history.pop(key, None)
        artifacts = tuple(
            replace(a, verified=False, availability="missing")
            if a.verified and a.bssid.upper() in eligible
            and report.paths.get(a.path, FilePresence("unknown")).state == "missing"
            else a for a in snapshot.artifacts
        )
        points = tuple(
            replace(p, capture_state=AccessPointCaptureState.PENDING, result_availability="missing")
            if p.bssid.upper() in missing and p.capture_state == AccessPointCaptureState.CAPTURED
            else self.decorate(p) for p in snapshot.access_points
        )
        owner.openwrt_backend.snapshot = replace(
            snapshot, artifacts=artifacts, access_points=points,
            has_verified_result=any(a.verified for a in artifacts)
            if artifacts != snapshot.artifacts else snapshot.has_verified_result,
        )
        # 全部内存状态先提交，再发保存/回放信号，避免重入看到一半状态。
        if history_changed:
            self.revision += 1
            owner.verifiedHistoryChanged.emit(owner.verified_history_snapshot())
        owner.openwrtSnapshotChanged.emit(owner.openwrt_snapshot)
        unknown = sum(report.targets[key] == "unknown" for key in eligible)
        if missing or unknown:
            owner.messageGenerated.emit(
                f"本地结果核验：{len(missing)} 个目标的全部已知产物确认缺失，已撤销完成标记；"
                f"{unknown} 个目标的文件位置或访问状态待核实，保留记录。"
                "可检查存储位置，或手动清除状态后重抓。", "warning" if unknown else "info",
            )
        return frozenset(missing)
