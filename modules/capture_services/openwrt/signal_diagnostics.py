"""Project signal state and exact activation blockers without names or credentials."""
import re

MAX_SIGNALS = 256
BLOCKERS = frozenset({"disconnected", "busy", "stale", "unsupported", "parent_disabled", "none"})


def signal_evidence(snapshot, setup_snapshot=None):
    rows = []
    for index, row in enumerate(snapshot.inventory.rows[:MAX_SIGNALS]):
        reason = ("disconnected" if not snapshot.connected else "busy" if snapshot.busy else
                  "stale" if snapshot.stale else "unsupported" if row.reason else
                  "parent_disabled" if row.radio_disabled else "none")
        rows.append(dict(index=index + 1, radio=row.radio, band=row.band, mode=row.mode,
            disabled=row.disabled, radio_disabled=row.radio_disabled, running=row.running,
            blocked_reason=reason))
    environment = getattr(setup_snapshot, "environment", None)
    setup = dict(checked=environment is not None, ready=bool(environment and environment.ready),
                 missing_packages=list(environment.missing) if environment else [],
                 issue_count=len(environment.issues) if environment else 0,
                 action=getattr(setup_snapshot, "action", ""))
    return sanitized_signal_evidence(dict(schema_version=1, environment=setup, connected=snapshot.connected,
        busy=snapshot.busy, stale=snapshot.stale, rows=rows,
        rows_omitted=max(0, len(snapshot.inventory.rows) - MAX_SIGNALS)))


def sanitized_signal_evidence(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("unsupported signal evidence")
    rows = data.get("rows", [])
    if not isinstance(rows, list) or len(rows) > MAX_SIGNALS:
        raise ValueError("signal evidence limit exceeded")
    result = {"schema_version": 1}
    for key in ("connected", "busy", "stale"):
        if type(data.get(key)) is not bool:
            raise ValueError("invalid signal state")
        result[key] = data[key]
    omitted = data.get("rows_omitted", 0)
    result["rows_omitted"] = max(0, min(1000000, omitted)) if type(omitted) is int else 0
    environment = data.get("environment")
    if isinstance(environment, dict):
        missing = environment.get("missing_packages", [])
        allowed_actions = {"", "inspect", "install", "scan", "save", "save_only", "delete", "password",
                           "pending_review", "pending_commit", "move_preview", "relocate", "orphan_preview", "orphan_cleanup"}
        result["environment"] = dict(checked=environment.get("checked") is True,
            ready=environment.get("ready") is True,
            missing_packages=[name for name in missing[:64] if isinstance(name, str)
                              and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]{0,95}", name)] if isinstance(missing, list) else [],
            issue_count=max(0, min(256, environment.get("issue_count", 0)))
                        if type(environment.get("issue_count")) is int else 0,
            action=environment.get("action") if isinstance(environment.get("action"), str) and environment["action"] in allowed_actions else "unknown")
    result["rows"] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("invalid signal row")
        clean = {"index": index + 1}
        radio = row.get("radio", "")
        clean["radio"] = radio if isinstance(radio, str) and re.fullmatch(r"radio\d+", radio) else "other"
        for key, allowed in (("band", {"2g", "5g", "6g"}), ("mode", {"ap", "sta"}),
                             ("running", {"active", "inactive", "unknown"}), ("blocked_reason", BLOCKERS)):
            value = row.get(key)
            clean[key] = value if isinstance(value, str) and value in allowed else "unknown"
        for key in ("disabled", "radio_disabled"):
            if type(row.get(key)) is not bool:
                raise ValueError("invalid signal flag")
            clean[key] = row[key]
        result["rows"].append(clean)
    return result
