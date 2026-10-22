"""抓包失败日志的字段标准化。"""

import logging
import json
from pathlib import Path


LOGGER = logging.getLogger("routermanager.capture")


def save_ssh_failure(local_dir, error, connected):
    """保存本任务的首次传输故障元数据；不写命令、地址、凭据或包内容。"""
    pending, seen = [error], set()
    records = []
    while pending and len(seen) < 12:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        stage = getattr(current, "ssh_stage", None)
        if isinstance(stage, str):
            records.append({
                "stage": stage, "kind": getattr(current, "ssh_kind", "command"),
                "action": getattr(current, "ssh_action", ""),
                "elapsed_seconds": getattr(current, "ssh_elapsed", None),
                "exception_type": type(current).__name__,
            })
        pending.extend((getattr(current, "primary_error", None), current.__cause__))
    if not records:
        return ""
    try:
        path = Path(local_dir) / "ssh-failure.json"
        payload = json.dumps({"connected": bool(connected), "failures": records},
                             ensure_ascii=True, indent=2)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(payload)
        except FileExistsError:
            pass
        return str(path)
    except (OSError, TypeError, ValueError) as exc:
        LOGGER.warning("SSH diagnostic save failed type=%s", type(exc).__name__)
        return ""


def log_capture_failure(action, resource, reason, suggestion="", task_type=""):
    """记录抓包失败定位信息，不记录密码、候选字典或数据包正文。

    参数：action 是失败动作；resource 是设备/文件/脚本；reason 是详细原因；suggestion 是
    建议处理方式；task_type 是任务类型。返回值无，副作用仅写入现有 Python 日志处理器。
    """

    LOGGER.error(
        "capture failure task=%s action=%s resource=%s reason=%s suggestion=%s",
        task_type,
        action,
        resource,
        reason,
        suggestion,
    )
