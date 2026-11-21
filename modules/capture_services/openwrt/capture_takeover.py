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
            # Persist only the already-resolved radio and interface identities.
            from .recovery_identity_snapshot import recovery_identity_snapshot
            safe_snapshot = recovery_identity_snapshot(snapshot_text)
            (workspace.local_dir / snapshot_name).write_text(
                safe_snapshot, encoding="utf-8"
            )
        except (OSError, ValueError) as exc:
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


def restore_takeover(takeover, ssh, workspace, record, cancel_event=None, notify=None,
                     warning_callback=None):
    """Attempt radio restore after owned resources are cleared; retain precise results."""
    with takeover_transaction(cancel_event):
        if record is None:
            record = takeover.pending_record
        if record is None:
            return []
        errors = []
        warnings = []
        journals = []
        fingerprint = ""
        try:
            for path in workspace.local_dir.glob("takeover-journal*.json"):
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("config_token") == record.config_token:
                    journals.append((path, data))
                    fingerprint = str(data.get("device_fingerprint", ""))
            if (not journals or not fingerprint or fingerprint != ssh.host_fingerprint
                    or str(record.session_id) != str(workspace.session_id)
                    or any(str(data.get("session_id", "")) != str(workspace.session_id)
                           or str(data.get("device_fingerprint", "")) != fingerprint
                           for _, data in journals)):
                raise RuntimeError("\u6062\u590d\u8bb0\u5f55\u4e0e\u5f53\u524d\u8bbe\u5907\u8eab\u4efd\u672a\u6838\u5b9e\uff0c\u672a\u6267\u884c\u6062\u590d\u3002")
            if not ssh.connected:
                raise RuntimeError("SSH \u5df2\u65ad\u5f00\uff0c\u6062\u590d\u8bb0\u5f55\u5df2\u4fdd\u7559\u3002")
            try:
                takeover.restore(record, cancel_event=cancel_event)
                note = takeover.audit_restore(record, cancel_event=cancel_event)
                if note:
                    warnings.append(str(note))
            except Exception as exc:
                if not ssh.connected or (cancel_event is not None and cancel_event.is_set()):
                    raise
                warnings.append(str(exc) or type(exc).__name__)
            if notify is not None:
                try:
                    synced = notify(cancel_event)
                except Exception:
                    if not ssh.connected or (cancel_event is not None and cancel_event.is_set()):
                        raise
                    synced = False
                if not synced:
                    warnings.append("\u4fe1\u53f7\u72b6\u6001\u540c\u6b65\u672a\u5b8c\u6210\uff0c\u8bf7\u5237\u65b0\u67e5\u770b\u3002")
        except Exception as exc:
            errors.append(str(exc) or type(exc).__name__)
        status = "pending" if errors else "released" if warnings else "restored"
        report = workspace.local_dir / "takeover-restore-audit.txt"
        try:
            message = "\n".join(errors or warnings) if (errors or warnings) else "\u65e0\u7ebf\u914d\u7f6e\u548c\u8fd0\u884c\u63a5\u53e3\u5df2\u6062\u590d\uff0c\u6062\u590d\u540e\u68c0\u67e5\u901a\u8fc7\u3002"
            report.write_text(message + "\n", encoding="utf-8")
            receipt = {"config_token": record.config_token, "device_fingerprint": fingerprint,
                       "radio": record.radio, "status": status}
            path = workspace.local_dir / "takeover-recovery-result.json"
            temporary = path.with_suffix(".json.part")
            temporary.write_text(json.dumps(receipt, ensure_ascii=True), encoding="utf-8")
            temporary.replace(path)
            if not errors:
                for path, data in journals:
                    data["cleanup_stage"] = status
                    temporary = path.with_suffix(".json.part")
                    temporary.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
                    temporary.replace(path)
        except (OSError, ValueError, TypeError) as exc:
            errors.append("\u6062\u590d\u68c0\u67e5\u8bb0\u5f55\u4fdd\u5b58\u5931\u8d25\uff1a" + str(exc))
        if errors:
            takeover.pending_record = record
        else:
            pending = takeover.pending_record
            if (pending is not None and pending.session_id == record.session_id
                    and pending.config_token == record.config_token and pending.radio == record.radio):
                takeover.pending_record = None
            if warnings and warning_callback is not None:
                warning_callback("\u91c7\u96c6\u8fdb\u7a0b\u548c\u4e34\u65f6\u65e0\u7ebf\u63a5\u53e3\u5df2\u6e05\u7406\uff0c\u65e0\u7ebf\u4fe1\u53f7\u672a\u80fd\u5b8c\u5168\u6062\u590d\uff1b\u5df2\u4fdd\u7559\u5f53\u524d\u8bbe\u7f6e\uff0c\u8bf7\u5728\u4fe1\u53f7\u7ba1\u7406\u4e2d\u6838\u5bf9\u5e76\u6309\u9700\u5f00\u542f\u3002\u539f\u56e0\uff1a" + "\uff1b".join(warnings))
        return errors
