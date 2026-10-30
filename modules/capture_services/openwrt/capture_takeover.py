"""Shared, journaled radio takeover and verified restoration for capture lanes."""
import json
import re
from .session_journal import SessionJournal


def prepare_takeover(takeover, ssh, workspace, session_id, phy, cancel_event,
                     notify, tag="", task_type="handshake", expected_band="", expected_radio=""):
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


def restore_takeover(takeover, ssh, workspace, record, cancel_event=None):
    """Return every restore/audit/persistence error; retain recovery on uncertainty."""
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
            raise RuntimeError("\u6062\u590d\u8bb0\u5f55\u4e0e\u5f53\u524d\u8bbe\u5907\u8eab\u4efd\u672a\u6838\u5b9e\uff0c\u672a\u6267\u884c\u6062\u590d\u3002")
        if not ssh.connected:
            raise RuntimeError("SSH \u5df2\u65ad\u5f00\uff0c\u6062\u590d\u8bb0\u5f55\u5df2\u4fdd\u7559\uff0c\u8bf7\u91cd\u65b0\u8fde\u63a5\u540e\u6838\u5b9e\u3002")
        takeover.restore(record, cancel_event=cancel_event)
        note = takeover.audit_restore(record, cancel_event=cancel_event)
        if note:
            errors.append(str(note))
    except Exception as exc:
        errors.append(str(exc) or type(exc).__name__)
    report = workspace.local_dir / "takeover-restore-audit.txt"
    try:
        report.write_text("\n".join(errors) + "\n" if errors else
                          "\u65e0\u7ebf\u914d\u7f6e\u548c\u8fd0\u884c\u63a5\u53e3\u5df2\u6062\u590d\uff0c\u6062\u590d\u540e\u68c0\u67e5\u901a\u8fc7\u3002\n", encoding="utf-8")
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
        errors.append("\u6062\u590d\u68c0\u67e5\u8bb0\u5f55\u4fdd\u5b58\u5931\u8d25\uff1a" + str(exc))
    if errors:
        takeover.pending_record = record
    elif takeover.pending_record == record:
        takeover.pending_record = None
    return errors
