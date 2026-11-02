"""Shared, journaled radio takeover and verified restoration for capture lanes."""
from contextlib import contextmanager
import json
import re
import threading

from .session_journal import SessionJournal

_TAKEOVER_MUTEX = threading.RLock()


@contextmanager
def takeover_transaction(cancel_event=None):
    """串行化执行无线配置事务（接管与恢复），防止多射频并发请求触发设备锁冲突。"""
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("操作已取消。")
        if _TAKEOVER_MUTEX.acquire(timeout=0.2):
            break
    try:
        yield
    finally:
        _TAKEOVER_MUTEX.release()


def prepare_takeover(takeover, ssh, workspace, session_id, phy, cancel_event,
                     notify, tag="", task_type="handshake", expected_band="", expected_radio=""):
    with takeover_transaction(cancel_event):
        device_fingerprint = ssh.host_fingerprint
        if not device_fingerprint:
            raise RuntimeError("无法核实接管设备身份，拒绝修改无线配置。")
        snapshot_text = takeover.snapshot_wireless(cancel_event)
        radio = takeover.resolve_radio(
            phy, snapshot_text, cancel_event, expected_band=expected_band)
        if expected_radio and radio != expected_radio:
            raise RuntimeError("Radio ownership changed; capture was not started")
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
        record = takeover.prepare_config_takeover(phy, radio, session_id, cancel_event)
        journal = SessionJournal(
            session_id=str(session_id),
            task_type=str(task_type or "handshake"),
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
        takeover.takeover_prepared(record, cancel_event)
        if not notify(cancel_event):
            raise RuntimeError("接管后信号列表未完成同步，已停止启动抓包并进入恢复。")
        return record


def restore_takeover(takeover, ssh, workspace, record, cancel_event=None, notify=None):
    """Return every restore/audit/persistence error; retain recovery on uncertainty."""
    with takeover_transaction(cancel_event):
        if record is None:
            record = takeover.pending_record
        if record is None:
            return []
        errors = []
        journals = []
        fingerprint = ""
        try:
            for path in workspace.local_dir.glob("takeover-journal*.json"):
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("config_token") == record.config_token:
                    journals.append((path, data))
                    fingerprint = str(data.get("device_fingerprint", ""))
            if not journals or not fingerprint or fingerprint != ssh.host_fingerprint:
                raise RuntimeError("恢复记录与当前设备身份未核实，未执行恢复。")
            if not ssh.connected:
                raise RuntimeError("SSH 已断开，恢复记录已保留，请重新连接后核实。")
            takeover.restore(record, cancel_event=cancel_event)
            note = takeover.audit_restore(record, cancel_event=cancel_event)
            if note:
                errors.append(str(note))
            if not errors and notify is not None:
                if not notify(cancel_event):
                    errors.append("无线恢复已核对，但页面状态同步未完成。")
        except Exception as exc:
            errors.append(str(exc) or type(exc).__name__)
        report = workspace.local_dir / "takeover-restore-audit.txt"
        try:
            report.write_text("\n".join(errors) + "\n" if errors else
                              "无线配置和运行接口已恢复，恢复后检查通过。\n", encoding="utf-8")
            receipt = {"config_token": record.config_token, "device_fingerprint": fingerprint,
                       "radio": record.radio, "status": "pending" if errors else "restored"}
            path = workspace.local_dir / "takeover-recovery-result.json"
            temporary = path.with_suffix(".json.part")
            temporary.write_text(json.dumps(receipt, ensure_ascii=True), encoding="utf-8")
            temporary.replace(path)
            if not errors:
                for path, data in journals:
                    data["cleanup_stage"] = "restored"
                    temporary = path.with_suffix(".json.part")
                    temporary.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
                    temporary.replace(path)
        except (OSError, ValueError, TypeError) as exc:
            errors.append("恢复检查记录保存失败：" + str(exc))
        if errors:
            takeover.pending_record = record
        elif takeover.pending_record == record:
            takeover.pending_record = None
        return errors
