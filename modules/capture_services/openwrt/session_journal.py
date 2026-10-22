"""OpenWrt 抓包恢复现场的会话记录模型。"""

from dataclasses import asdict, dataclass, field
from typing import Tuple


@dataclass
class SessionJournal:
    """记录本任务创建的远端资源，供异常重连后精确恢复。"""

    session_id: str
    task_type: str
    remote_work_dir: str
    remote_pids: list[int] = field(default_factory=list)
    monitor_section: str = ""
    backup_path: str = ""
    cleanup_stage: str = "not_started"
    radio: str = ""
    interfaces: Tuple[str, ...] = field(default_factory=tuple)
    phy: str = ""
    config_token: str = ""
    device_fingerprint: str = ""

    def to_dict(self):
        """返回可序列化副本，不包含密码或候选字典内容。"""

        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """从 JSON 字典恢复；字段缺失或类型异常时返回 None，不抛异常。"""

        if not isinstance(data, dict):
            return None
        try:
            return cls(
                session_id=str(data.get("session_id", "")),
                task_type=str(data.get("task_type", "")),
                remote_work_dir=str(data.get("remote_work_dir", "")),
                remote_pids=[int(pid) for pid in (data.get("remote_pids") or [])],
                monitor_section=str(data.get("monitor_section", "")),
                backup_path=str(data.get("backup_path", "")),
                cleanup_stage=str(data.get("cleanup_stage", "not_started")),
                radio=str(data.get("radio", "")),
                interfaces=tuple(str(name) for name in (data.get("interfaces") or ())),
                phy=str(data.get("phy", "")),
                config_token=str(data.get("config_token", "")),
                device_fingerprint=str(data.get("device_fingerprint", "")),
            )
        except (TypeError, ValueError):
            return None
