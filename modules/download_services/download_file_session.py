"""下载文件与跨重启状态的协调服务。

主要作用：处理下载地址变化时遗留断点文件和状态记录的安全清理。
输入参数：state_manager 提供 download_state.json 读写；清理方法接收软件 ID、当前地址和目标文件。
返回值：发生清理时返回 DownloadSourceResetResult，否则返回 None。
副作用：可能删除未完成下载文件和 aria2 控制文件，并移除对应恢复状态；不创建 UI 或 aria2 任务。
调用限制：只能在已确认本次下载地址有效、即将提交新任务时调用，不能用于暂停或恢复同一地址。
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DownloadSourceResetResult:
    """下载地址变更后清理旧断点的结果数据。

    removed_paths 是实际删除的文件路径；为空时表示旧临时文件已不存在或被占用。
    """

    removed_paths: tuple[str, ...]


class DownloadFileSession:
    """封装下载文件和恢复状态之间的一致性清理规则。"""

    def __init__(self, state_manager):
        """保存下载状态管理器引用，不执行文件读写。

        参数：state_manager 是下载服务层的 DownloadStateManager 实例。
        返回值：无。
        副作用：无。
        """
        self._state_manager = state_manager

    def reset_stale_download_source(self, software_id, current_url, target_path):
        """清理与新下载地址不一致的旧断点状态。

        参数：software_id 是下载项 ID；current_url 是本次检测得到的地址；target_path 是本次
        保存目标。返回值：发生地址变化时返回清理结果，否则返回 None。
        副作用：只删除状态记录标记为未完成的目标文件和其 .aria2 控制文件；完整文件保留。
        """
        if not current_url:
            return None
        try:
            previous = self._state_manager.get_all_entries().get(software_id, {})
        except Exception:
            return None
        previous_url = str(previous.get("url", "")).strip()
        if not previous_url or previous_url == current_url:
            return None

        target_path = Path(target_path)
        control_path = target_path.with_suffix(target_path.suffix + ".aria2")
        try:
            incomplete = float(previous.get("progress", 0)) < 100.0
        except (TypeError, ValueError):
            incomplete = True

        removed_paths = []
        for path in (control_path, target_path if incomplete else None):
            if path is None or not path.exists():
                continue
            try:
                path.unlink()
                removed_paths.append(str(path))
            except OSError:
                # 被 aria2 占用时保留文件，仍移除旧状态，避免新地址错误复用旧 URL。
                continue
        self._state_manager.remove(software_id)
        return DownloadSourceResetResult(tuple(removed_paths))
